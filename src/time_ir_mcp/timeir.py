"""Time.ir tools: dates in three calendars, holidays and events, prayer times, cities."""

from __future__ import annotations

import html
import math
import re
from datetime import date, timedelta
from typing import Annotated, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .http import action, get_text
from .registry import tool

Calendar = Literal["jalali", "gregorian", "hijri"]
BASE = {"jalali": 0, "gregorian": 1, "hijri": 2}
WEEKDAYS = ["شنبه", "یکشنبه", "دوشنبه", "سه\u200cشنبه", "چهارشنبه", "پنجشنبه", "جمعه"]  # Saturday first, like the site
IRAN_COUNTRY_ID = 78
DATE = Annotated[
    str | None,
    Field(description="YYYY-MM-DD in `calendar`, e.g. 1405-08-10. Omit for today."),
]
CAL = Annotated[Calendar, Field(description="Calendar of the date(s).")]


def _txt(s: str | None, limit: int | None = None) -> str:
    """HTML body -> plain text."""
    s = re.sub(r"<(br|/p|/div|/li|/h\d)\s*/?>", "\n", s or "")
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    s = re.sub(r"[ \t\u200b]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()
    return s if limit is None or len(s) <= limit else s[:limit].rstrip() + "..."


def _ymd(y: int, m: int, d: int) -> str:
    return f"{y:04d}-{m:02d}-{d:02d}"


def _norm(s: str) -> str:
    """Persian/Arabic letter variants and spacing folded for name matching."""
    s = s.translate(str.maketrans("يكۀ", "یکه")).replace("\u200c", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def _parse_date(date: str) -> tuple[int, int, int]:
    m = re.fullmatch(
        r"\s*(\d{3,4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*",
        date.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")),
    )
    if not m:
        raise ToolError(f"Date '{date}' is not YYYY-MM-DD (for example 1405-08-10).")
    return int(m[1]), int(m[2]), int(m[3])


async def _convert(date: str | None, calendar: Calendar) -> dict:
    """Date (or today) in all three calendars, from the site's own converter."""
    if date is None:
        now = (await action("fetchCurrentTimeAction", []))["data"]
        j, g, h = now["jalali"], now["gregorian"], now["hijri"]
        return {
            "jalali": _ymd(j["year"], j["month"], j["day"]),
            "gregorian": _ymd(g["year"], g["month"], g["day"]),
            "hijri": _ymd(h["year"], h["month"], h["day"]),
            "weekday": j["day_title"],
            "month_names": {"jalali": j["month_title"], "gregorian": g["month_title"], "hijri": h["month_title"]},
            "sign": now["astrological_sign"],
            "time": f"{now['time']['hour']:02d}:{now['time']['minute']:02d}:{now['time']['second']:02d}",
        }
    y, m, d = _parse_date(date)
    r = await action("convertDateAction", [{"day": d, "year": y, "month": m, "currentBase": BASE[calendar]}])
    if not r.get("ok"):
        raise ToolError(f"time.ir: {r.get('error', {}).get('error_message', 'conversion failed')} ({calendar} {date}).")
    by = {x["date_type"]: x for x in r["response"]["data"]["date_list"]}
    return {
        **{k: _ymd(v["year"], v["month"], v["day"]) for k, v in by.items()},
        "weekday": by["jalali"]["day_title"],
        "month_names": {k: v["month_title"] for k, v in by.items()},
        "sign": by["jalali"]["astrological_sign"],
    }


def _jalali(c: dict) -> tuple[int, int, int]:
    return _parse_date(c["jalali"])


def _event(e: dict, with_text: bool = False, dates: bool = True) -> dict:
    """An event row, trimmed. `calendar` says which calendar the occasion belongs to; `dates` False drops the day's dates."""
    out = {
        "jalali": _ymd(e["jalali_year"], e["jalali_month"], e["jalali_day"]),
        "gregorian": _ymd(e["gregorian_year"], e["gregorian_month"], e["gregorian_day"]),
        "hijri": _ymd(e["hijri_year"], e["hijri_month"], e["hijri_day"]),
        "weekday": e["jalali_day_title"],
        "title": e["title"].strip(),
        "holiday": e["is_holiday"],
        "calendar": ["jalali", "gregorian", "hijri"][e["base"]],
    }
    if not dates:
        for k in ("gregorian", "hijri", "weekday"):
            del out[k]
    if with_text and (t := _txt(e.get("body"), 900)):
        out["text"] = t
    return out


async def _month(year: int, month: int) -> dict:
    if not 1 <= month <= 12:
        raise ToolError("month must be 1 to 12 (Jalali: 1 Farvardin ... 12 Esfand).")
    r = await action(
        "fetchEventCalendarAction",
        [{"year": year, "month": month, "day": 0, "base1": 0, "base2": 1, "base3": 2}, "fa"],
        resolve_refs=True,
    )
    if r.get("status_code") != 200:
        raise ToolError(f"time.ir: {r.get('message') or 'no calendar for that month'}.")
    return r["data"]


def _month_events(data: dict, year: int, month: int) -> list[dict]:
    return [e for e in data["event_list"] if (e["jalali_year"], e["jalali_month"]) == (year, month)]


@tool("Today")
async def ti_today() -> dict:
    """Get today's date in the Jalali, Gregorian and Hijri calendars with the Tehran time, weekday, zodiac sign and today's events.

    Use first for "today", "now" or the date/time in Iran.
    """
    c = await _convert(None, "jalali")
    y, m, d = _jalali(c)
    events = [_event(e, dates=False) for e in _month_events(await _month(y, m), y, m) if e["jalali_day"] == d]
    return {**c, "is_friday": c["weekday"] == "جمعه", "events": events, "is_holiday": any(e["holiday"] for e in events)}


@tool("Convert date")
async def ti_convert_date(
    date: Annotated[str, Field(description="YYYY-MM-DD, e.g. 1405-08-10 or 2026-11-01.")],
    calendar: CAL = "jalali",
) -> dict:
    """Convert a date between the Jalali (Solar Hijri), Gregorian and Hijri (lunar) calendars, using time.ir's own converter.

    Hijri is Iran's official date (may differ by a day from Saudi Arabia's).
    """
    c = await _convert(date, calendar)
    return {"input": {"date": date, "calendar": calendar}, **c}


@tool("Month calendar")
async def ti_month(
    year: Annotated[int | None, Field(description="Jalali year, e.g. 1405. Omit for current.")] = None,
    month: Annotated[int | None, Field(description="Jalali month 1-12. Omit for current.")] = None,
) -> dict:
    """Show one Jalali month: its Gregorian and Hijri month spans, weekday of day 1, holidays and every event of the month.

    Use for "holidays this month". For one day with descriptions use ti_events.
    """
    if year is None or month is None:
        y, m, _ = _jalali(await _convert(None, "jalali"))
        year, month = year or y, month or m
    data = await _month(year, month)
    days = [d for d in data["day_list"] if d["enabled"]]
    names = {b["base"]: b["month_list"] for b in data["calendar_detail_list"]}

    def span(base: int) -> str:
        ms = names[base]
        return " - ".join(x["month_title"] for x in ms) + f" {ms[0]['year']}"

    events = [_event(e, dates=False) for e in _month_events(data, year, month)]
    return {
        "year": year,
        "month": month,
        "month_name": names[0][0]["month_title"],
        "gregorian_months": span(1),
        "hijri_months": span(2),
        "days": len(days),
        "first_day_weekday": WEEKDAYS[days[0]["column_index"]],
        "holiday_days": sorted({d["index_in_base1"] for d in days if d["is_holiday"]}),
        "fridays": [d["index_in_base1"] for d in days if d["is_weekend"]],
        "events": events,
    }


@tool("Events of a day")
async def ti_events(date: DATE = None, calendar: CAL = "jalali") -> dict:
    """List the occasions (Persian: monasebat) and official holidays of one day, with a short description when time.ir has one.

    Use for "is 2026-12-25 a holiday in Iran". Each event has `calendar` (where the occasion belongs) and `holiday`.
    Friday alone is not a holiday.
    """
    c = await _convert(date, calendar)
    y, m, d = _jalali(c)
    events = [
        _event(e, with_text=True, dates=False) for e in _month_events(await _month(y, m), y, m) if e["jalali_day"] == d
    ]
    return {
        "jalali": c["jalali"],
        "gregorian": c["gregorian"],
        "hijri": c["hijri"],
        "weekday": c["weekday"],
        "is_friday": c["weekday"] == "جمعه",
        "is_holiday": any(e["holiday"] for e in events),
        "events": events,
    }


async def _year(year: int) -> list[dict]:
    """The 12 month objects of a Jalali year (events with their long texts resolved)."""
    r = await action("fetchEventYearlyCalendarAction", [year], path="/event-year", resolve_refs=True)
    if r.get("status_code") != 200 or not r.get("data"):
        raise ToolError(f"time.ir has no calendar for Jalali year {year}.")
    return r["data"]


async def _holidays(year: int) -> list[dict]:
    """Official holidays of a Jalali year, one row per day (several occasions may share a day)."""
    days: dict[str, dict] = {}
    for i, month in enumerate(await _year(year), 1):
        for e in _month_events(month, year, i):
            if e["is_holiday"]:
                ev = _event(e)
                row = days.setdefault(
                    ev["jalali"], {k: ev[k] for k in ("jalali", "gregorian", "hijri", "weekday")} | {"titles": []}
                )
                if ev["title"] not in row["titles"]:
                    row["titles"].append(ev["title"])
    return sorted(days.values(), key=lambda r: r["jalali"])


@tool("Holidays of a year")
async def ti_holidays(
    year: Annotated[int | None, Field(description="Jalali year, e.g. 1405. Omit for current.")] = None,
) -> dict:
    """List all official holidays (تعطیلات رسمی) of a Jalali year with Gregorian and Hijri dates and weekdays.

    Fridays are listed only when also an occasion holiday. For "the next holiday" use ti_upcoming_holidays.
    """
    if year is None:
        year = _jalali(await _convert(None, "jalali"))[0]
    days = await _holidays(year)
    return {"year": year, "count": len(days), "holidays": days}


@tool("Upcoming holidays")
async def ti_upcoming_holidays(
    count: Annotated[int, Field(ge=1, le=20, description="Holidays to return.")] = 5,
    from_date: DATE = None,
    calendar: CAL = "jalali",
) -> dict:
    """List the next official holidays in Iran starting today (or from_date), with days until each.

    Use for "when is the next holiday". Fridays alone are not listed.
    """
    c = await _convert(from_date, calendar)
    start = c["jalali"]
    y = _jalali(c)[0]
    found = [h for h in await _holidays(y) if h["jalali"] >= start]
    if len(found) < count:
        found += await _holidays(y + 1)
    today = date.fromisoformat(c["gregorian"])
    out = [{**h, "days_until": (date.fromisoformat(h["gregorian"]) - today).days} for h in found[:count]]
    return {"from": start, "gregorian_from": c["gregorian"], "holidays": out}


async def _provinces() -> list[dict]:
    r = await action(
        "fetchProvinceListAction",
        [
            {
                "limit": 500,
                "offset": 0,
                "countryId": IRAN_COUNTRY_ID,
                "locale": "fa",
                "sorting": [{"ascending": True, "expression": "title"}],
            }
        ],
    )
    return r["data"]


async def _cities(province_id: int) -> list[dict]:
    r = await action(
        "fetchCityListAction",
        [
            {
                "limit": 500,
                "offset": 0,
                "provinceId": province_id,
                "locale": "fa",
                "sorting": [{"expression": "title", "ascending": True}],
            }
        ],
    )
    return r["data"]


def _find_province(provinces: list[dict], q: str) -> dict:
    if q.strip().isdigit():
        hit = [p for p in provinces if p["id"] == int(q)]
    else:
        n = _norm(q)
        hit = [p for p in provinces if _norm(p["title"]) == n] or [p for p in provinces if n in _norm(p["title"])]
    if len(hit) != 1:
        names = ", ".join(p["title"] for p in (hit or provinces))
        raise ToolError(f"{'Several provinces match' if hit else 'No province matches'} '{q}'. Options: {names}.")
    return hit[0]


@tool("Cities")
async def ti_cities(
    province: Annotated[
        str | None,
        Field(description="Province name or id, e.g. 'فارس'. Omit to list the 31 provinces."),
    ] = None,
    query: Annotated[str | None, Field(description="Filter cities by name.")] = None,
) -> dict:
    """List Iran's provinces, or the cities of one province, with the ids ti_prayer_times takes.

    No arguments: 31 provinces with capital city ids. With `province`: its cities with lat/lng.
    """
    provinces = await _provinces()
    if province is None:
        return {
            "provinces": [
                {"id": p["id"], "name": p["title"], "capital": p["capital"], "capital_city_id": p["capital_id"]}
                for p in provinces
            ]
        }
    p = _find_province(provinces, province)
    cities = await _cities(p["id"])
    if query:
        cities = [c for c in cities if _norm(query) in _norm(c["title"])]
    return {
        "province": p["title"],
        "province_id": p["id"],
        "count": len(cities),
        "cities": [{"id": c["id"], "name": c["title"], "lat": c["latitude"], "lng": c["longitude"]} for c in cities],
    }


async def _city_id(city: str, province: str | None) -> int:
    if city.strip().isdigit():
        return int(city)
    provinces = await _provinces()
    n = _norm(city)
    if province:
        p = _find_province(provinces, province)
        return _pick_city(await _cities(p["id"]), city, p)["id"]
    return _capital(provinces, city, n)["capital_id"]


def _capital(provinces: list[dict], city: str, n: str) -> dict:
    caps = [p for p in provinces if n in _norm(p["capital"]) or n == _norm(p["title"])]
    if len(caps) != 1:
        raise ToolError(f"'{city}' is not a provincial capital. Pass `province` too (see ti_cities), or the city id.")
    return caps[0]


def _pick_city(cities: list[dict], city: str, p: dict) -> dict:
    n = _norm(city)
    hit = [c for c in cities if _norm(c["title"]) == n] or [c for c in cities if n in _norm(c["title"])]
    if len(hit) != 1:
        names = ", ".join(c["title"] for c in hit[:15])
        raise ToolError(f"{'Several cities match' if hit else 'No city matches'} '{city}' in {p['title']}. {names}")
    return hit[0]


def _hm(s: str) -> str:
    h, m = s.split(":")
    return f"{int(h):02d}:{m}"


@tool("Prayer times")
async def ti_prayer_times(
    city: Annotated[str, Field(description="City name (Persian), e.g. 'شیراز', or city id.")],
    province: Annotated[
        str | None, Field(description="Province name or id; needed unless `city` is a provincial capital.")
    ] = None,
    date: DATE = None,
    calendar: CAL = "jalali",
) -> dict:
    """Get the Islamic prayer times (اوقات شرعی) for an Iranian city and day: fajr, sunrise, noon, sunset, maghrib and Islamic midnight.

    Iran local time, 24 h. Today only: another `date` returns an error. Non-capital cities need `province`.
    """
    cid = await _city_id(city, province)
    c = await _convert(date, calendar)
    y, m, d = _jalali(c)
    e = await _ephemeris(cid, y, m, d)
    return {
        "city": e["city_title"],
        "jalali": c["jalali"],
        "gregorian": c["gregorian"],
        "weekday": e["day_title"],
        "fajr": _hm(e["azan_morning"]),
        "sunrise": _hm(e["sunrise"]),
        "dhuhr": _hm(e["azan_noon"]),
        "sunset": _hm(e["sunset"]),
        "maghrib": _hm(e["azan_night"]),
        "midnight": _hm(e["mid_night"]),
        "day_length": _span(e["sunrise"], e["sunset"]),
    }


async def _ephemeris(cid: int, y: int, m: int, d: int) -> dict:
    r = await action(
        "fetchEphemerisByCityAction",
        [{"cityId": cid, "timeBase": 0, "currentYear": y, "currentMonth": m, "currentDay": d}, "fa"],
    )
    if r.get("status_code") != 200:
        raise ToolError(f"time.ir has no prayer times for city id {cid}: {r.get('message') or r.get('error')}")
    e = r["data"]
    # The site's backend ignores the date it is sent and answers for today; refuse to pass that off as another day.
    if (e["year"], e["month"], e["day"]) != (y, m, d):
        raise ToolError(
            f"Time.ir gives prayer times for today only ({_ymd(e['year'], e['month'], e['day'])}), not {_ymd(y, m, d)}."
        )
    return e


def _span(a: str, b: str) -> str:
    """Hours:minutes between two HH:MM strings."""
    mins = _minutes(b) - _minutes(a)
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _minutes(s: str) -> int:
    h, m = s.split(":")[:2]
    return int(h) * 60 + int(m)


KAABA = (21.4225, 39.8262)


@tool("Qibla direction")
async def ti_qibla(
    city: Annotated[
        str, Field(description="City name (Persian), e.g. 'شیراز', or city id (then `province` is needed).")
    ],
    province: Annotated[
        str | None, Field(description="Province name or id; needed unless `city` is a provincial capital.")
    ] = None,
) -> dict:
    """Get the qibla direction (bearing to the Kaaba in Mecca, degrees clockwise from true north) and distance for an Iranian city.

    Computed from the city's coordinates (great circle), not by Time.ir. Bearing is from true north.
    """
    provinces = await _provinces()
    if province:
        p = _find_province(provinces, province)
    elif city.strip().isdigit():
        raise ToolError("A city id needs `province` too (see ti_cities).")
    else:
        p = _capital(provinces, city, _norm(city))
    cities = await _cities(p["id"])
    c = (
        next((x for x in cities if x["id"] == int(city)), None)
        if city.strip().isdigit()
        else _pick_city(cities, city, p)
    )
    if c is None:
        raise ToolError(f"City id {city} is not in {p['title']}.")
    la1, lo1, la2, lo2 = map(math.radians, (c["latitude"], c["longitude"], *KAABA))
    dl = lo2 - lo1
    bearing = (
        math.degrees(
            math.atan2(
                math.sin(dl) * math.cos(la2),
                math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dl),
            )
        )
        % 360
    )
    a = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dl / 2) ** 2
    points = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return {
        "city": c["title"],
        "city_id": c["id"],
        "lat": c["latitude"],
        "lng": c["longitude"],
        "qibla_bearing_deg": round(bearing, 1),
        "compass": points[round(bearing / 45) % 8],
        "distance_km": round(6371 * 2 * math.asin(math.sqrt(a))),
    }


@tool("On this day in history")
async def ti_on_this_day(
    kind: Annotated[
        Literal["birth", "death", "event"] | None,
        Field(description="Keep only this kind."),
    ] = None,
) -> dict:
    """List today's historical events, birthdays and deaths of notable people, as Time.ir's "today" page shows them.

    Today only. Each item's `date` is as Time.ir prints it (Gregorian with year for history, Jalali day for national occasions).
    """
    page = await get_text("/today")
    items = []
    for raw, en, when in re.findall(
        r'__root__title">(.*?)<span class="[^"]*__title__date( en)?[ "][^>]*>\[([^\]]*)\]</span>', page, re.S
    ):
        title = _txt(raw)
        k = "birth" if re.match(r"(زادروز|تولد)", title) else "death" if title.startswith("درگذشت") else "event"
        if kind in (None, k):
            items.append({"kind": k, "title": title, "date": when.strip(), "historical": bool(en)})
    c = await _convert(None, "jalali")
    return {"jalali": c["jalali"], "gregorian": c["gregorian"], "weekday": c["weekday"], "items": items}


@tool("Search occasions")
async def ti_search_events(
    query: Annotated[str, Field(description="Text in occasion names or descriptions, e.g. 'معلم'.")],
    year: Annotated[int | None, Field(description="Jalali year. Omit for current.")] = None,
    holiday_only: Annotated[bool, Field(description="Only official holidays.")] = False,
    calendar: Annotated[
        Calendar | None,
        Field(description="Only this calendar's occasions (jalali national, gregorian world days, hijri religious)."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Max results.")] = 30,
) -> dict:
    """Search all occasions (monasebat) of a Jalali year by keyword and return each match with its dates.

    Use for "when is Teacher's Day". Title matches first, then description matches, each in date order.
    """
    if year is None:
        year = _jalali(await _convert(None, "jalali"))[0]
    n = _norm(query)
    hits = []
    for i, month in enumerate(await _year(year), 1):
        for e in _month_events(month, year, i):
            if (holiday_only and not e["is_holiday"]) or (calendar and e["base"] != BASE[calendar]):
                continue
            in_title = n in _norm(e["title"])
            if in_title or n in _norm(_txt(e.get("body"))):
                hits.append((not in_title, _event(e, with_text=True)))
    hits.sort(key=lambda h: (h[0], h[1]["jalali"]))
    return {"year": year, "query": query, "count": len(hits), "events": [h[1] for h in hits[:limit]]}


def _month_len(m: int, leap: bool) -> int:
    return 31 if m <= 6 else 30 if m <= 11 or leap else 29


async def _is_leap(y: int) -> bool:
    try:
        await _convert(f"{y}-12-30", "jalali")
    except ToolError:
        return False
    return True


@tool("Date difference")
async def ti_date_difference(
    from_date: Annotated[str, Field(description="Start date YYYY-MM-DD, e.g. birth date 1380-05-17.")],
    to_date: DATE = None,
    calendar: CAL = "jalali",
) -> dict:
    """Count the days between two dates and give the span as Jalali years, months and days (age calculator).

    Omit to_date for today. Both dates use `calendar`. If to_date is earlier, `direction` is "before".
    """
    a, b = await _convert(from_date, calendar), await _convert(to_date, calendar)
    ga, gb = date.fromisoformat(a["gregorian"]), date.fromisoformat(b["gregorian"])
    before = gb < ga
    if before:
        a, b, ga, gb = b, a, gb, ga
    (y1, m1, d1), (y2, m2, d2) = _jalali(a), _jalali(b)
    y, m, d = y2 - y1, m2 - m1, d2 - d1
    if d < 0:
        py, pm = (y2, m2 - 1) if m2 > 1 else (y2 - 1, 12)
        d += _month_len(pm, await _is_leap(py) if pm == 12 else False)
        m -= 1
    if m < 0:
        y, m = y - 1, m + 12
    days = (gb - ga).days
    return {
        "from": {"jalali": a["jalali"], "gregorian": a["gregorian"]},
        "to": {"jalali": b["jalali"], "gregorian": b["gregorian"]},
        "direction": "before" if before else "after",
        "days": days,
        "weeks": f"{days // 7} weeks {days % 7} days",
        "years_months_days": {"years": y, "months": m, "days": d},
    }


def _off_runs(start: date, days: int, off: set[date], min_days: int) -> list[tuple[date, date]]:
    runs, cur = [], []
    for i in range(days + 1):  # the extra step closes a run that ends on the last day
        day = start + timedelta(i)
        if i < days and day in off:
            cur.append(day)
            continue
        if len(cur) >= min_days:
            runs.append((cur[0], cur[-1]))
        cur = []
    return runs


@tool("Long weekends")
async def ti_long_weekends(
    year: Annotated[int | None, Field(description="Jalali year. Omit for current.")] = None,
    min_days: Annotated[int, Field(ge=2, le=14, description="Shortest run to list.")] = 3,
    thursday_off: Annotated[bool, Field(description="Count Thursdays as days off.")] = False,
) -> dict:
    """Find runs of consecutive days off in a Jalali year: official holidays joined with Fridays (optionally Thursdays).

    Use for "long weekends in 1405". Bridge days are not counted; runs are cut at the year boundary.
    """
    if year is None:
        year = _jalali(await _convert(None, "jalali"))[0]
    start = date.fromisoformat((await _convert(f"{year}-01-01", "jalali"))["gregorian"])
    total = (date.fromisoformat((await _convert(f"{year + 1}-01-01", "jalali"))["gregorian"]) - start).days
    holidays = {date.fromisoformat(h["gregorian"]): h for h in await _holidays(year)}
    weekend = {3, 4} if thursday_off else {4}  # date.weekday(): Thursday 3, Friday 4
    every = [start + timedelta(i) for i in range(total)]
    off = {d for d in every if d in holidays or d.weekday() in weekend}
    lens = [31] * 6 + [30] * 5 + [29 + (total == 366)]

    def jalali(day: date) -> str:  # Jalali date from the offset in the year; month lengths are fixed
        n = (day - start).days
        for mo, ln in enumerate(lens, 1):
            if n < ln:
                return _ymd(year, mo, n + 1)
            n -= ln
        raise AssertionError(day)

    def point(day: date) -> dict:
        return {"jalali": jalali(day), "gregorian": day.isoformat(), "weekday": WEEKDAYS[(day.weekday() + 2) % 7]}

    out = []
    for first, last in _off_runs(start, total, off, min_days):
        span = [first + timedelta(i) for i in range((last - first).days + 1)]
        titles = [t for d in span if d in holidays for t in holidays[d]["titles"]]
        out.append(
            {"from": point(first), "to": point(last), "days": len(span), "holidays": list(dict.fromkeys(titles))}
        )
    return {"year": year, "min_days": min_days, "count": len(out), "runs": out}
