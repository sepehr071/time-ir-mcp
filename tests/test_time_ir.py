import httpx
import pytest
from conftest import FIXTURES, fixture, flight

from time_ir_mcp import http

pytestmark = pytest.mark.anyio

TOOLS = {
    "ti_today",
    "ti_convert_date",
    "ti_month",
    "ti_events",
    "ti_holidays",
    "ti_upcoming_holidays",
    "ti_prayer_times",
    "ti_cities",
    "ti_on_this_day",
    "ti_search_events",
    "ti_date_difference",
    "ti_long_weekends",
    "ti_qibla",
}


async def test_all_tools_are_read_only_and_described(client):
    tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        assert t.annotations.read_only_hint is True, t.name
        assert t.title and t.description.splitlines()[0].endswith("."), t.name
        assert "title" not in str(t.input_schema.get("properties", {}).values()), t.name


async def test_convert_date_returns_three_calendars(client, api):
    api["convertDateAction"] = fixture("convert.json")
    r = (await client.call_tool("ti_convert_date", {"date": "2026-11-25", "calendar": "gregorian"})).structured_content
    assert (r["jalali"], r["gregorian"], r["hijri"]) == ("1405-09-04", "2026-11-25", "1448-06-15")
    assert r["weekday"] == "چهارشنبه"
    assert api.calls == [("convertDateAction", [{"day": 25, "year": 2026, "month": 11, "currentBase": 1}])]


async def test_convert_date_accepts_persian_digits_and_slashes(client, api):
    api["convertDateAction"] = fixture("convert.json")
    await client.call_tool("ti_convert_date", {"date": "۱۴۰۵/۹/۴"})
    assert api.calls[0][1] == [{"day": 4, "year": 1405, "month": 9, "currentBase": 0}]


async def test_convert_date_errors_are_readable(client, api):
    api["convertDateAction"] = fixture("convert_bad.json")
    r = await client.call_tool("ti_convert_date", {"date": "1405-12-31"})
    assert r.is_error and "محدوده" in r.content[0].text
    r = await client.call_tool("ti_convert_date", {"date": "tomorrow"})
    assert r.is_error and "YYYY-MM-DD" in r.content[0].text


async def test_today_has_dates_time_and_events(client, api):
    api["fetchCurrentTimeAction"] = fixture("now.json")
    api["fetchEventCalendarAction"] = fixture("month8.json")
    r = (await client.call_tool("ti_today", {})).structured_content
    j = fixture("now.json")["data"]["jalali"]
    assert r["jalali"] == f"{j['year']}-{j['month']:02d}-{j['day']:02d}" and r["time"].count(":") == 2
    assert api.calls[1][0] == "fetchEventCalendarAction"


async def test_events_of_a_day_filters_the_month(client, api):
    c = fixture("convert.json")
    c["response"]["data"]["date_list"][0].update(month=8, day=13)
    api["convertDateAction"] = c
    api["fetchEventCalendarAction"] = fixture("month8.json")
    r = (await client.call_tool("ti_events", {"date": "1405-08-13"})).structured_content
    assert [e["title"] for e in r["events"]] == ["روز دانش آموز"]
    assert r["is_holiday"] is False and api.calls[1][1][0]["month"] == 8


async def test_month_lists_events_and_layout(client, api):
    api["fetchEventCalendarAction"] = fixture("month8.json")
    r = (await client.call_tool("ti_month", {"year": 1405, "month": 8})).structured_content
    assert r["month_name"] == "آبان" and r["days"] == 30 and len(r["events"]) == 14
    assert (
        r["fridays"] == [1, 8, 15, 22, 29]
        and r["first_day_weekday"] == "جمعه"
        and r["gregorian_months"].startswith("October - November 2026")
    )


async def test_month_rejects_bad_month(client, api):
    r = await client.call_tool("ti_month", {"year": 1405, "month": 13})
    assert r.is_error and "1 to 12" in r.content[0].text


async def test_holidays_group_titles_per_day(client, api):
    api["fetchEventYearlyCalendarAction"] = fixture("year.json")
    r = (await client.call_tool("ti_holidays", {"year": 1405})).structured_content
    assert r["count"] == 26 and r["holidays"][0]["jalali"] == "1405-01-01"
    assert r["holidays"][0]["titles"] == ["جشن نوروز/جشن سال نو", "عید سعید فطر"]
    assert api.calls[0] == ("fetchEventYearlyCalendarAction", [1405])


async def test_upcoming_holidays_counts_days(client, api):
    api["fetchEventYearlyCalendarAction"] = fixture("year.json")
    api["convertDateAction"] = fixture("convert.json")  # 1405-09-04 = 2026-11-25
    r = (await client.call_tool("ti_upcoming_holidays", {"count": 2, "from_date": "1405-09-04"})).structured_content
    assert len(r["holidays"]) == 2 and all(h["jalali"] >= "1405-09-04" for h in r["holidays"])
    assert r["holidays"][0]["days_until"] >= 0


async def test_prayer_times_for_a_capital(client, api):
    api["fetchProvinceListAction"] = fixture("provinces.json")
    api["fetchCurrentTimeAction"] = fixture("now.json")
    api["fetchEphemerisByCityAction"] = fixture("eph.json")
    r = (await client.call_tool("ti_prayer_times", {"city": "شیراز"})).structured_content
    assert r["city"] == "شیراز" and len(r["fajr"]) == 5 and r["day_length"] == "11:01"
    eph = api.calls[-1][1][0]
    assert eph["cityId"] == 204 and eph["timeBase"] == 0 and (eph["currentMonth"], eph["currentDay"]) == (8, 10)


async def test_prayer_times_refuse_other_dates(client, api):
    """The site's backend ignores the date and answers for today; the tool must not label that as another day."""
    api["fetchProvinceListAction"] = fixture("provinces.json")
    api["convertDateAction"] = fixture("convert.json")  # 1405-09-04
    api["fetchEphemerisByCityAction"] = fixture("eph.json")  # answers 1405-08-10
    r = await client.call_tool("ti_prayer_times", {"city": "شیراز", "date": "1405-09-04"})
    assert r.is_error and "today only" in r.content[0].text


async def test_prayer_times_non_capital_needs_province(client, api):
    api["fetchProvinceListAction"] = fixture("provinces.json")
    r = await client.call_tool("ti_prayer_times", {"city": "مرودشت"})
    assert r.is_error and "province" in r.content[0].text
    api["fetchCityListAction"] = fixture("cities_fars.json")
    api["fetchCurrentTimeAction"] = fixture("now.json")
    api["fetchEphemerisByCityAction"] = fixture("eph.json")
    await client.call_tool("ti_prayer_times", {"city": "مرودشت", "province": "فارس"})
    assert api.calls[-1][1][0]["cityId"] == 280


async def test_cities_lists_provinces_then_cities(client, api):
    api["fetchProvinceListAction"] = fixture("provinces.json")
    r = (await client.call_tool("ti_cities", {})).structured_content
    assert len(r["provinces"]) == 31
    api["fetchCityListAction"] = fixture("cities_fars.json")
    r = (await client.call_tool("ti_cities", {"province": "فارس", "query": "مرو"})).structured_content
    assert sorted(c["id"] for c in r["cities"]) == [280, 5538]
    r = await client.call_tool("ti_cities", {"province": "xyz"})
    assert r.is_error and "No province" in r.content[0].text


async def test_on_this_day_parses_the_today_page(client, api):
    api["GET /today"] = (FIXTURES / "today.html").read_text(encoding="utf-8")
    api["fetchCurrentTimeAction"] = fixture("now.json")
    r = (await client.call_tool("ti_on_this_day", {})).structured_content
    assert [i["kind"] for i in r["items"]] == ["death", "event", "birth", "event"]
    assert r["items"][0]["date"] == "November 1 1903" and r["items"][0]["historical"] is True
    assert r["items"][1]["historical"] is False
    r = (await client.call_tool("ti_on_this_day", {"kind": "birth"})).structured_content
    assert len(r["items"]) == 1 and "لافلین" in r["items"][0]["title"]


async def test_search_events_by_title_then_text(client, api):
    api["fetchEventYearlyCalendarAction"] = fixture("year.json")
    r = (await client.call_tool("ti_search_events", {"query": "نوروز", "year": 1405})).structured_content
    assert r["count"] >= 1 and all("نوروز" in e["title"] for e in r["events"][:1])
    assert r["events"] == sorted(r["events"], key=lambda e: e["jalali"]) or r["count"] > 1
    r = (
        await client.call_tool("ti_search_events", {"query": "نوروز", "year": 1405, "holiday_only": True, "limit": 1})
    ).structured_content
    assert len(r["events"]) == 1 and r["events"][0]["holiday"] is True
    r = (await client.call_tool("ti_search_events", {"query": "zzzz", "year": 1405})).structured_content
    assert r["count"] == 0


async def test_date_difference_borrows_months(client, api):
    api["convertDateAction"] = fixture("convert.json")  # always 1405-09-04 = 2026-11-25
    api["fetchCurrentTimeAction"] = fixture("now.json")  # 1405-08-10 = 2026-11-01
    r = (await client.call_tool("ti_date_difference", {"from_date": "1405-09-04", "to_date": None})).structured_content
    assert r["direction"] == "before" and r["days"] == 24
    assert r["years_months_days"] == {"years": 0, "months": 0, "days": 24}  # Aban has 30 days: 10 -> 4 Azar


async def test_long_weekends_join_fridays_and_holidays(client, api):
    api["fetchEventYearlyCalendarAction"] = fixture("year.json")

    def convert(args):  # Nowruz 1405 = 2026-03-21, Nowruz 1406 = 2027-03-21
        c = fixture("convert.json")
        g = {1405: (2026, 3, 21), 1406: (2027, 3, 21)}[args[0]["year"]]
        c["response"]["data"]["date_list"][1].update(year=g[0], month=g[1], day=g[2])
        return c

    api["convertDateAction"] = convert
    r = (await client.call_tool("ti_long_weekends", {"year": 1405})).structured_content
    assert r["runs"][0]["from"]["jalali"] == "1405-01-01" and r["runs"][0]["days"] >= 3
    assert all(x["days"] >= 3 for x in r["runs"])


async def test_qibla_bearing_for_a_capital(client, api):
    api["fetchProvinceListAction"] = fixture("provinces.json")
    api["fetchCityListAction"] = fixture("cities_fars.json")
    r = (await client.call_tool("ti_qibla", {"city": "شیراز"})).structured_content
    assert r["city"] == "شیراز" and 230 < r["qibla_bearing_deg"] < 245 and 1000 < r["distance_km"] < 1800


async def test_stale_action_id_is_rediscovered():
    """A 404 'Server action not found' makes the client read the new id from the site's scripts and retry."""
    new = "7f" + "ab" * 20

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path in ("/", "/event-year"):
            return httpx.Response(200, text='<script src="/_next/static/chunks/abc-1.js?dpl=x"></script>')
        if request.url.path.endswith(".js"):
            js = f'let a=(0,I.createServerReference)("{new}",I.callServer,void 0,I.findSourceMapURL,"fetchCurrentTimeAction");'
            return httpx.Response(200, text=js)
        if request.headers["next-action"] == new:
            return httpx.Response(200, content=flight(fixture("now.json")))
        return httpx.Response(404, text="Server action not found.")

    http.set_transport(httpx.MockTransport(handler))
    r = await http.action("fetchCurrentTimeAction", [])
    assert r["data"]["jalali"]["year"] >= 1400 and http.ACTION_IDS["fetchCurrentTimeAction"] == new


def test_flight_text_rows_are_resolved():
    rows = http.parse_flight(flight({"body": "$2", "n": ["$2", "$zz"]}, rows={"2": "سلام <b>x</b>"}))
    assert rows["2"] == "سلام <b>x</b>"
    assert http.resolve(rows["1"], rows) == {"body": "سلام <b>x</b>", "n": ["سلام <b>x</b>", "$zz"]}


async def test_action_headers_match_the_web_app():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b'0:{"a":1}\n1:{"ok":true}\n')

    http.set_transport(httpx.MockTransport(handler))
    await http.action("fetchCurrentTimeAction", [])
    h = seen[0].headers
    assert h["accept"] == "text/x-component" and h["next-action"] == http.ACTION_IDS["fetchCurrentTimeAction"]
    assert h["origin"] == "https://www.time.ir" and h["referer"] == "https://www.time.ir/"
    assert h["sec-fetch-site"] == "same-origin" and h["x-deployment-id"] == http.DEPLOYMENT
    assert "next-router-state-tree" in h and h["accept-language"].startswith("fa-IR")


async def test_jitter_added_to_gap(monkeypatch):
    waits = []

    async def fake_sleep(s):
        waits.append(s)

    monkeypatch.setattr(http.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(http, "MIN_GAP", 0)
    monkeypatch.setattr(http, "JITTER", (0.3, 0.3))
    http.set_transport(httpx.MockTransport(lambda r: httpx.Response(200, content=b'1:{"ok":1}\n')))
    await http.action("fetchCurrentTimeAction", [])
    assert waits and waits[0] == pytest.approx(0.3)
