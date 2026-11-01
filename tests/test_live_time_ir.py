import pytest

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_live_today(client):
    r = (await client.call_tool("ti_today", {})).structured_content
    assert r["jalali"].startswith("14") and len(r["gregorian"]) == 10 and r["hijri"].startswith("14")


async def test_live_convert_date_known_pair(client):
    r = (await client.call_tool("ti_convert_date", {"date": "1405-01-01"})).structured_content
    assert (r["gregorian"], r["hijri"]) == ("2026-03-21", "1447-10-01")  # Nowruz 1405, Eid al-Fitr


async def test_live_events_nowruz_is_a_holiday(client):
    r = (await client.call_tool("ti_events", {"date": "1405-01-01"})).structured_content
    assert r["is_holiday"] and any("نوروز" in e["title"] for e in r["events"])


async def test_live_month_and_holidays(client):
    m = (await client.call_tool("ti_month", {"year": 1405, "month": 1})).structured_content
    assert m["days"] == 31 and 1 in m["holiday_days"]
    h = (await client.call_tool("ti_holidays", {"year": 1405})).structured_content
    assert 15 < h["count"] < 40 and h["holidays"][0]["jalali"] == "1405-01-01"


async def test_live_upcoming_holidays(client):
    r = (await client.call_tool("ti_upcoming_holidays", {"count": 3})).structured_content
    assert len(r["holidays"]) == 3 and r["holidays"][0]["days_until"] >= 0


async def test_live_prayer_times(client):
    r = (await client.call_tool("ti_prayer_times", {"city": "مشهد"})).structured_content
    assert r["city"] == "مشهد" and r["fajr"] < r["sunrise"] < r["dhuhr"] < r["sunset"] < r["maghrib"]


async def test_live_cities(client):
    r = (await client.call_tool("ti_cities", {"province": "فارس", "query": "مرودشت"})).structured_content
    assert any(c["name"] == "مرودشت" for c in r["cities"])


async def test_live_prayer_times_refuse_other_days(client):
    r = await client.call_tool("ti_prayer_times", {"city": "مشهد", "date": "1405-02-10"})
    assert r.is_error and "today only" in r.content[0].text


async def test_live_on_this_day(client):
    r = (await client.call_tool("ti_on_this_day", {})).structured_content
    assert r["items"] and all(i["kind"] in ("birth", "death", "event") for i in r["items"])


async def test_live_search_events(client):
    r = (await client.call_tool("ti_search_events", {"query": "روز معلم", "year": 1405})).structured_content
    assert any(e["jalali"] == "1405-02-12" for e in r["events"])


async def test_live_date_difference(client):
    r = (
        await client.call_tool(
            "ti_date_difference", {"from_date": "2001-08-08", "to_date": "2026-11-01", "calendar": "gregorian"}
        )
    ).structured_content
    assert r["days"] == 9216 and r["years_months_days"] == {"years": 25, "months": 2, "days": 23}


async def test_live_long_weekends(client):
    r = (await client.call_tool("ti_long_weekends", {"year": 1405})).structured_content
    assert r["runs"][0]["from"]["jalali"] == "1405-01-01" and r["runs"][0]["days"] == 4


async def test_live_qibla(client):
    r = (await client.call_tool("ti_qibla", {"city": "تبریز"})).structured_content
    assert 198 < r["qibla_bearing_deg"] < 203
