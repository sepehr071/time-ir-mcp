"""Shared async HTTP client for www.time.ir.

The site is a Next.js app: its pages call "server actions" (POST to a page path with a `next-action` id header and a
JSON array of arguments) and the server fetches api.time.ir with a secret key. `action` calls those server actions the
way the site's own scripts do. Action ids change with each deployment, so they are looked up by function name in the
site's JavaScript when the built-in id is rejected (HTTP 404 "Server action not found").
"""

from __future__ import annotations

import asyncio
import copy
import json as json_lib
import os
import random
import re
import time
from typing import Any

import httpx
from curl_cffi.const import CurlOpt
from httpx_curl_cffi import AsyncCurlTransport
from mcp.server.mcpserver.exceptions import ToolError

SITE = "https://www.time.ir"
HOSTS = {"www.time.ir"}  # host allowlist
# Ids seen on 2026-11-01 (deployment time-20261022-01); refreshed from the site's scripts when rejected.
ACTION_IDS = {
    "fetchCurrentTimeAction": "7fffc75f55b4984adc6ab97b46a22b13b4d62e161d",
    "fetchEventCalendarAction": "7f71dd3872f60c98972a30c49789c9221ad9c30ca3",
    "fetchEventYearlyCalendarAction": "7fca86ca1a4519af93bc69256c0d2820f70c1d7384",
    "fetchEphemerisByCityAction": "7f1e44efdf5519d461a4303c36a7a260d7e450a023",
    "fetchProvinceListAction": "7f070d646c40dc2c38c92e11ee58af8ecbd3c5328f",
    "fetchCityListAction": "7f75fe3376ac37a5a8fce9d9d747397c39baaaf912",
    "convertDateAction": "7f210029fd665665497969a63d183b65d9a05bf4d7",
}
# Sent with every action by the site's own router (checked in Chrome 2026-11-01); the id follows the deployment.
DEPLOYMENT = "time-20261022-01"
ROUTER_TREE = "%5B%22%22%2C%7B%22children%22%3A%5B%5B%22locale%22%2C%22fa%22%2C%22d%22%2Cnull%5D%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%2Cnull%2Cnull%2C4096%5D%7D%2Cnull%2Cnull%2C4096%5D%7D%2Cnull%2Cnull%2C4112%5D"
PAGES = ("/", "/event-year")  # pages whose scripts hold the actions above

HEADERS = {
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "priority": "u=1, i",
}

MAX_CONCURRENCY = 2
MIN_GAP = 1.0  # seconds between uncached calls: the site is a small calendar, be gentle
JITTER = (0.1, 0.5)  # random seconds added to the gap before each uncached call
MAX_RETRIES = 2
MAX_RETRY_WAIT = 10
CACHE_TTL = 600  # seconds; calendar data is static, "now" is not cached
CACHE_MAX = 128
RETRY_DELAY = 0.5
MAX_BODY = 5_000_000  # bytes; a whole year of events is ~0.5 MB

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None
_proxy_index = 0
_retired: set[asyncio.Task] = set()
_cache: dict[tuple, tuple[float, Any]] = {}
_last_call = 0.0
_gap_lock: asyncio.Lock | None = None


class _XhrTransport(AsyncCurlTransport):
    """The Chrome profile sends navigation-only headers; a None value makes curl drop them on XHR calls."""

    def _create_request_params(self, req: httpx.Request) -> dict[str, Any]:
        # The parent drops "accept: */*" as an httpx default; curl would then send Chrome's page Accept instead.
        accept = req.headers.get("accept")
        p = super()._create_request_params(req)
        extra = [(b"cache-control", None)]
        if req.headers.get("sec-fetch-mode") != "navigate":
            extra += [(b"upgrade-insecure-requests", None), (b"sec-fetch-user", None)]
        p["headers"] = [*p["headers"], *extra, *([(b"accept", b"*/*")] if accept == "*/*" else [])]
        return p


class ApiError(ToolError):
    """A failed upstream call. `status` is the HTTP status, or None for network errors."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client and caches."""
    global _transport, _client, _limit, _proxy_index, _gap_lock, DEPLOYMENT
    _transport, _client, _limit, _proxy_index, _gap_lock = transport, None, None, 0, None
    _cache.clear()
    ACTION_IDS.update(_DEFAULT_IDS)
    DEPLOYMENT = _DEFAULT_DEPLOYMENT


_DEFAULT_IDS = dict(ACTION_IDS)
_DEFAULT_DEPLOYMENT = DEPLOYMENT


def _proxies() -> list[str]:
    """TIME_IR_MCP_PROXY: one proxy URL, or a comma-separated list used one at a time."""
    return [p.strip() for p in os.environ.get("TIME_IR_MCP_PROXY", "").split(",") if p.strip()]


def _next_proxy(client: httpx.AsyncClient) -> None:
    """Sticky pool: move to the next proxy after a block or connect error; the next call rebuilds the client."""
    global _client, _limit, _proxy_index
    if len(_proxies()) < 2 or client is not _client:
        return
    _client, _limit, _proxy_index = None, None, _proxy_index + 1
    t = asyncio.create_task(_close_later(client))
    _retired.add(t)
    t.add_done_callback(_retired.discard)


async def _close_later(client: httpx.AsyncClient) -> None:
    await asyncio.sleep(30)  # longer than the 20 s request timeout
    await client.aclose()


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        proxies = _proxies()
        _client = httpx.AsyncClient(
            transport=_transport
            or _XhrTransport(
                impersonate="chrome",
                default_headers=True,
                curl_options={CurlOpt.NOPROXY: "*"},  # httpx-curl-cffi drops its own NOPROXY unless non-empty
                proxy=proxies[_proxy_index % len(proxies)] if proxies else None,
                max_connections=2 * MAX_CONCURRENCY,
            ),
            headers=HEADERS,
            timeout=httpx.Timeout(25, connect=8),
            limits=httpx.Limits(max_connections=2 * MAX_CONCURRENCY),
            follow_redirects=False,
            trust_env=False,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
    assert _limit is not None
    return _client, _limit


def parse_flight(body: bytes) -> dict[str, Any]:
    """Split a Next.js flight response into rows: {row id: parsed JSON, or text for `T` rows}."""
    rows: dict[str, Any] = {}
    row = re.compile(rb"(\w+):(T([0-9a-f]+),)?")
    i = 0
    while i < len(body) and (m := row.match(body, i)):
        i = m.end()
        key = m.group(1).decode()
        if m.group(2):  # text row: the hex number is its length in bytes
            n = int(m.group(3), 16)
            rows[key] = body[i : i + n].decode("utf-8", "replace")
            i += n
            continue
        j = body.find(b"\n", i)
        j = len(body) if j < 0 else j
        try:
            rows[key] = json_lib.loads(body[i:j])
        except ValueError:
            pass
        i = j + 1
    return rows


def resolve(value: Any, rows: dict[str, Any]) -> Any:
    """Replace "$<row>" strings (long texts the server sent as separate rows) by their text."""
    if isinstance(value, str):
        return rows.get(value[1:], value) if re.fullmatch(r"\$[0-9a-f]+", value) else value
    if isinstance(value, list):
        return [resolve(x, rows) for x in value]
    if isinstance(value, dict):
        return {k: resolve(x, rows) for k, x in value.items()}
    return value


async def action(name: str, args: list[Any], path: str = "/", resolve_refs: bool = False) -> Any:
    """Call one of the site's server actions and return its result (row 1 of the flight response).

    `args` is the JSON argument list the site's script passes. Answers are cached for CACHE_TTL seconds, except
    `fetchCurrentTimeAction`. A stale action id is looked up again from the site's scripts, once.
    """
    key = (name, path, json_lib.dumps(args, sort_keys=True), resolve_refs)
    if name != "fetchCurrentTimeAction" and (hit := _cache.get(key)) and time.monotonic() - hit[0] < CACHE_TTL:
        return copy.deepcopy(hit[1])
    for attempt in (0, 1):
        r, content = await _call(path, ACTION_IDS[name], args)
        if r.status_code == 404 and attempt == 0:
            await _discover()
            if name not in ACTION_IDS:
                raise ApiError(f"time.ir no longer has the '{name}' action; the site changed.")
            continue
        break
    if r.status_code >= 400:
        raise ApiError(_status_message(r), r.status_code)
    rows = parse_flight(content)
    result = rows.get("1")
    if result is None:
        raise ApiError("time.ir returned no data for that request.", r.status_code)
    if resolve_refs:
        result = resolve(result, rows)
    if len(_cache) >= CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic(), copy.deepcopy(result))
    return result


async def _call(path: str, action_id: str, args: list[Any]) -> tuple[httpx.Response, bytes]:
    headers = {
        "Origin": SITE,
        "Referer": f"{SITE}{path}",
        "Accept": "text/x-component",
        "Content-Type": "text/plain;charset=UTF-8",
        "next-action": action_id,
        "next-router-state-tree": ROUTER_TREE,
        "x-deployment-id": DEPLOYMENT,
        "sec-fetch-site": "same-origin",
    }
    return await _retrying("POST", SITE + path, headers, json_lib.dumps(args, separators=(",", ":")).encode())


async def get_text(path: str, accept: str = "*/*") -> str:
    """GET a page or script of the site (used to look up action ids)."""
    nav = path.endswith("/") or "." not in path.rsplit("/", 1)[-1]
    headers = {"Accept": "text/html,*/*;q=0.8" if nav else accept, "sec-fetch-site": "same-origin"}
    r, content = await _retrying("GET", SITE + path, headers, None)
    if r.status_code >= 400:
        raise ApiError(_status_message(r), r.status_code)
    return content.decode("utf-8", "replace")


async def _retrying(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[httpx.Response, bytes]:
    host = httpx.URL(url).host
    if host not in HOSTS:
        raise ApiError(f"Refusing to call unexpected host {host}.")
    for attempt in range(MAX_RETRIES + 1):
        last = attempt == MAX_RETRIES
        wait = RETRY_DELAY * 2**attempt * random.uniform(1, 1.5)
        try:
            r, content = await _request(method, url, headers, body, host)
        except ApiError as e:
            if last or e.status is not None:
                raise
            await asyncio.sleep(min(wait, MAX_RETRY_WAIT))
            continue
        if (r.status_code == 429 or r.status_code >= 500) and not last:
            await asyncio.sleep(min(_retry_after(r) or wait, MAX_RETRY_WAIT))
            continue
        return r, content
    raise AssertionError("unreachable")


async def _request(method, url, headers, body, host) -> tuple[httpx.Response, bytes]:
    global _last_call, _gap_lock
    client, limit = _get_client()
    _gap_lock = _gap_lock or asyncio.Lock()
    try:
        async with limit:
            async with _gap_lock:  # spaces calls MIN_GAP apart
                await asyncio.sleep(max(0.0, _last_call + MIN_GAP - time.monotonic()) + random.uniform(*JITTER))
                _last_call = time.monotonic()
            async with client.stream(method, url, headers=headers, content=body) as r:
                content = await _read_capped(r, host)
    except httpx.TimeoutException as e:
        raise ApiError(f"{host} did not answer in time. Try again in a moment.") from e
    except httpx.RequestError as e:
        if isinstance(e, httpx.ConnectError | httpx.ProxyError):
            _next_proxy(client)
        raise ApiError(
            f"Could not reach {host} ({type(e).__name__}). Check the internet connection "
            "(or the TIME_IR_MCP_PROXY setting if you use one)."
        ) from e
    if r.status_code in (403, 429):
        _next_proxy(client)
    return r, content


async def _read_capped(r: httpx.Response, host: str) -> bytes:
    """Read the body, refusing anything over MAX_BODY."""
    buf = bytearray()
    async for chunk in r.aiter_bytes():
        buf += chunk
        if len(buf) > MAX_BODY:
            raise ApiError(f"{host} sent a response over {MAX_BODY // 1_000_000} MB; refused.", r.status_code)
    return bytes(buf)


async def _discover() -> None:
    """Refresh ACTION_IDS from the `createServerReference("<id>", ..., "<name>")` calls in the site's scripts."""
    global DEPLOYMENT
    found: dict[str, str] = {}
    seen: set[str] = set()
    for page in PAGES:
        html = await get_text(page)
        if dpl := re.search(r"dpl=([\w-]+)", html):
            DEPLOYMENT = dpl.group(1)
        for chunk in sorted(set(re.findall(r"/_next/static/chunks/[\w-]+\.js", html)) - seen):
            seen.add(chunk)
            js = await get_text(chunk)
            for ident, name in re.findall(r'createServerReference\)?\("([0-9a-f]{40,})",[^")]*"(\w+)"\)', js):
                found[name] = ident
        if all(n in found for n in _DEFAULT_IDS):
            break
    ACTION_IDS.update(found)


def _retry_after(r: httpx.Response) -> float | None:
    v = r.headers.get("retry-after", "")
    return float(v) if r.status_code in (429, 503) and v.replace(".", "", 1).isdigit() else None


def _status_message(r: httpx.Response) -> str:
    code = r.status_code
    if code == 403:
        return "time.ir blocked the request (HTTP 403). Try another network or set TIME_IR_MCP_PROXY."
    if code == 404:
        return "time.ir found nothing at that address (HTTP 404)."
    if code == 429:
        return "time.ir is rate limiting requests (HTTP 429). Wait a minute before retrying."
    if code >= 500:
        return f"time.ir had a server error (HTTP {code}). Try again later."
    return f"time.ir rejected the request (HTTP {code})."
