import json
from pathlib import Path

import httpx
import pytest
from mcp import Client

from time_ir_mcp import http
from time_ir_mcp.server import mcp

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def flight(obj, rows=None) -> bytes:
    """Encode an action result like a Next.js flight response (row 1; `rows` adds raw text rows)."""
    head = b'0:{"a":"$@1","f":"","q":"","i":false}\n'
    extra = b"".join(f"{k}:T{len(v.encode()):x},{v}".encode() for k, v in (rows or {}).items())
    return head + extra + b"1:" + json.dumps(obj, ensure_ascii=False).encode() + b"\n"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_http_client(monkeypatch):
    """The shared httpx client is bound to one event loop; each test gets a new loop."""
    monkeypatch.setattr(http, "RETRY_DELAY", 0)
    monkeypatch.setattr(http, "MIN_GAP", 0)
    monkeypatch.setattr(http, "JITTER", (0, 0))
    http.set_transport(None)
    yield
    http.set_transport(None)


@pytest.fixture
def api():
    """Fake time.ir: api["fetchXAction"] = result object; api.calls = [(action name, args)].

    Requests are routed by their `next-action` header (the ids in http.ACTION_IDS).
    """

    class Routes(dict):
        calls: list[tuple[str, list]]

    routes = Routes()
    routes.calls = []
    names = {v: k for k, v in http.ACTION_IDS.items()}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":  # pages: routes["GET /today"] = html text
            return httpx.Response(200, text=routes[f"GET {request.url.path}"])
        name = names.get(request.headers.get("next-action", ""))
        if name is None:
            return httpx.Response(404, text="Server action not found.")
        routes.calls.append((name, json.loads(request.content)))
        args = json.loads(request.content)
        result = routes[name](args) if callable(routes[name]) else routes[name]  # a function answers per call
        return httpx.Response(200, content=flight(result))

    http.set_transport(httpx.MockTransport(handler))
    return routes


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        call_tool = c.call_tool

        async def parsed(*args, **kwargs):
            result = await call_tool(*args, **kwargs)
            if not result.is_error:
                result.structured_content = json.loads(result.content[0].text)
            return result

        c.call_tool = parsed
        yield c
