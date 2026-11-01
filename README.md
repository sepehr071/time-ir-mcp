<!-- mcp-name: io.github.sepehr071/time-ir-mcp -->

# time-ir-mcp: Time.ir Iranian calendar MCP server for AI agents

**time-ir-mcp is an unofficial, read-only MCP server that gives AI agents the Iranian calendar of Time.ir (تایم، تقویم و ساعت ایران).**<br>
Its 13 tools give today's date in the Jalali, Gregorian and Hijri calendars, convert dates, show a month with its events,<br>
list the occasions and official holidays of a day or a year, find the next holidays and return today's prayer times (اوقات شرعی) for Iranian cities, search occasions, count date differences and find long weekends.<br>
No API key or account is needed. It works in Claude, Cursor, Copilot and any other MCP client.

[![PyPI](https://img.shields.io/pypi/v/time-ir-mcp?color=2563eb)](https://pypi.org/project/time-ir-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/time-ir-mcp)](https://pypi.org/project/time-ir-mcp/)
[![CI](https://github.com/sepehr071/time-ir-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/sepehr071/time-ir-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](https://github.com/sepehr071/time-ir-mcp/blob/main/LICENSE)

[For AI agents](#for-ai-agents) · [Quick start](#quick-start) · [Tools](#tools) · [Good to know](#good-to-know) · [فارسی](#فارسی)

---

## For AI agents

- **What:** unofficial MCP server (model context protocol) for time.ir: the date and time in Iran in three calendars, date conversion, official holidays, monthly and daily occasions (مناسبت‌ها) and prayer times for Iranian cities.
- **Install:** `uvx time-ir-mcp` or `claude mcp add time-ir -- uvx time-ir-mcp`. Transport: stdio. Needs [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python 3.10+).
- **Auth:** none. Optional `TIME_IR_MCP_PROXY` (HTTP proxy URL, or a comma-separated list); system proxy settings are ignored.
- **Access:** read-only. Nothing is written anywhere, so no confirmation is needed before calling any tool.
- **Tools:** 13, all prefixed `ti_`. Typical sequences: `ti_today`; `ti_events` (is it a holiday?); `ti_upcoming_holidays`; `ti_cities` -> `ti_prayer_times`.
- **Use when:** the user asks what the date is in Iran, to convert between Jalali (شمسی), Gregorian (میلادی) and Hijri (قمری) dates, whether a day is an official holiday, what occasions a day has, when the next holiday is, or the azan times of an Iranian city.
- **Do not use when:** the user wants other countries' holidays or times (Time.ir covers Iran's official calendar only).
- **Output:** compact JSON. Dates are `YYYY-MM-DD` strings in the Jalali, Gregorian and Hijri calendars; times are Iran local time (24 h). Titles and descriptions are Persian, as Time.ir has them.

## Tools

| Tool | What it does |
|---|---|
| `ti_today` | Get today's date in the Jalali, Gregorian and Hijri calendars with the Tehran time, weekday, zodiac sign and today's events |
| `ti_convert_date` | Convert a date between the Jalali, Gregorian and Hijri calendars with time.ir's own converter |
| `ti_month` | Show one Jalali month: Gregorian and Hijri spans, weekday of day 1, holidays, Fridays and every event |
| `ti_events` | List the occasions and official holidays of one day, with a short description when time.ir has one |
| `ti_holidays` | List all official holidays of a Jalali year with Gregorian and Hijri dates |
| `ti_upcoming_holidays` | List the next official holidays in Iran with days until each |
| `ti_prayer_times` | Get today's prayer times (fajr, sunrise, noon, sunset, maghrib, Islamic midnight, day length) for an Iranian city |
| `ti_cities` | List Iran's provinces, or the cities of one province, with the ids the prayer tool takes |
| `ti_on_this_day` | List today's historical events, births and deaths of notable people from time.ir's today page |
| `ti_search_events` | Search all occasions of a Jalali year by keyword, holiday flag or calendar |
| `ti_date_difference` | Count days between two dates and give Jalali years, months, days (age calculator) |
| `ti_long_weekends` | Find runs of consecutive days off (holidays plus Fridays) in a Jalali year |
| `ti_qibla` | Get the qibla bearing and distance to Mecca for an Iranian city (computed from the site's coordinates) |

All 13 tools are annotated `readOnlyHint: true`.

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). No API key or account.

```bash
claude mcp add time-ir -- uvx time-ir-mcp
```

Claude Desktop, Cursor, Windsurf and Gemini CLI take the same stdio entry:

```json
{
  "mcpServers": {
    "time-ir": { "command": "uvx", "args": ["time-ir-mcp"] }
  }
}
```

Example prompts: "What is today's Jalali date?", "Is 25 December a holiday in Iran?", "When is the next official holiday?",
"Convert 1405-01-01 to Gregorian and Hijri.", "Azan times in Mashhad today."
<span dir="rtl">«تاریخ امروز به شمسی چیست؟»، «اذان ظهر شیراز چه ساعتی است؟»، «تعطیلات رسمی بهمن ۱۴۰۵ کدام‌اند؟»</span>

## How it works

`time-ir-mcp` runs locally and calls the same "server actions" the time.ir pages call (www.time.ir is a Next.js app;
the site's server reads api.time.ir with its own key, which this server never sees). Requests go out through a
Chrome-impersonating client, spaced at least one second apart, cached for 10 minutes (the current time is never
cached), and only to www.time.ir. Action ids change when the site is redeployed; when the site rejects one, the server
looks the new id up by function name in the site's scripts and retries once.

## Good to know

- **Iran's official calendar only.** Holidays are Iran's; the Hijri date is Iran's (it can differ by a day from Saudi Arabia's). No world clock.
- **Friday** is the weekend and is not reported as a holiday on its own; a Friday that is also an occasion holiday is.
- **Prayer times** are for the Jalali date given (other calendars are converted first) in Iran local time. Cities that are not provincial capitals need `province`.
- **Persian data.** Titles and descriptions are Persian. Event descriptions are HTML on the site; the tool returns plain text, cut at 900 characters.
- **Fragile by nature.** The site has no public API; if it changes its pages, a tool can fail until the server is updated.

## فارسی

<div dir="rtl">

**time-ir-mcp** یک سرور MCP غیررسمی و فقط‌خواندنی برای [تایم](https://www.time.ir) (تقویم و ساعت ایران) است: تاریخ امروز به شمسی، میلادی و قمری، تبدیل تاریخ، مناسبت‌ها و تعطیلات رسمی، و اوقات شرعی شهرهای ایران.

نصب (با [uv](https://docs.astral.sh/uv/getting-started/installation/)): `claude mcp add time-ir -- uvx time-ir-mcp`. کلید API یا حساب لازم نیست. اگر شبکهٔ شما درخواست‌ها را مسدود کرد، متغیر `TIME_IR_MCP_PROXY` را روی یک پراکسی HTTP بگذارید.

</div>

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run python -m pytest -q          # offline tests on recorded time.ir answers
uv run python -m pytest -m live -q  # calls the real time.ir site
uv build
```

## Disclaimer

Unofficial and not affiliated with or endorsed by Time.ir or Radcom. It is read-only and uses the public pages of
www.time.ir, which can change without notice. For religious or legal purposes check the official sources.

## Author

Built and maintained by **Sepehr Radmard** (سپهر رادمرد). Connect on [LinkedIn](https://www.linkedin.com/in/sepehr-radmard) or follow [@sepehr071 on GitHub](https://github.com/sepehr071) for more MCP servers for Iranian websites, all listed in [iran-mcp](https://github.com/sepehr071/iran-mcp).

## License

[MIT](https://github.com/sepehr071/time-ir-mcp/blob/main/LICENSE)
