from contextlib import asynccontextmanager

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from teslamate_mcp.config import Settings
from teslamate_mcp.server import create_app

TOKEN = "t" * 48


@asynccontextmanager
async def session(app, token=TOKEN):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     headers={"Authorization": f"Bearer {token}"}) as client:
            async with streamable_http_client("http://localhost:8000/mcp", http_client=client) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    yield s


def app_with_upstream(handler):
    return create_app(
        Settings(mcp_token=TOKEN, allowed_origins=("https://mcp.example.com",)),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_real_mcp_client_lists_only_read_only_tools_and_calls_status():
    def upstream(request):
        assert request.url.path == "/api/v1/cars/2/status"
        return httpx.Response(200, json={"data": {"car_id": 2, "status": {"state": "asleep"}}})
    async with session(app_with_upstream(upstream)) as s:
        tools = (await s.list_tools()).tools
        assert {t.name for t in tools} == {
            "list_cars", "get_car", "get_status", "get_battery_health",
            "list_charges", "get_current_charge", "get_charge", "list_drives",
            "get_drive", "get_updates", "get_global_settings", "get_api_info",
        }
        assert all(t.annotations.readOnlyHint for t in tools)
        result = await s.call_tool("get_status", {"car_id": 2})
        assert not result.isError
        assert result.structuredContent == {"data": {"car_id": 2, "status": {"state": "asleep"}}}


@pytest.mark.asyncio
async def test_filters_serialized_using_upstream_names():
    def upstream(request):
        assert request.url.path == "/api/v1/cars/1/drives"
        assert dict(request.url.params) == {
            "startDate": "2026-01-01T00:00:00Z", "endDate": "2026-02-01T00:00:00Z",
            "minDistance": "2.0", "maxDistance": "10.0", "location": "Home",
            "page": "1", "show": "100",
        }
        return httpx.Response(200, json={"data": {"drives": []}})
    async with session(app_with_upstream(upstream)) as s:
        result = await s.call_tool("list_drives", {
            "car_id": 1, "start_date": "2026-01-01T00:00:00Z", "end_date": "2026-02-01T00:00:00Z",
            "min_distance": 2, "max_distance": 10, "location": "Home",
        })
        assert not result.isError


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    {"car_id": 0}, {"car_id": -1}, {"car_id": 1, "start_date": "yesterday"},
    {"car_id": 1, "start_date": "2026-01-01T00:00:00"},
    {"car_id": 1, "min_distance": -1},
    {"car_id": 1, "min_distance": 10, "max_distance": 2},
    {"car_id": 1, "page": 0}, {"car_id": 1, "page_size": 501},
    {"car_id": 1, "start_date": "2026-02-01T00:00:00Z", "end_date": "2026-01-01T00:00:00Z"},
])
async def test_invalid_filters_do_not_reach_upstream(args):
    seen = []
    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={})
    async with session(app_with_upstream(upstream)) as s:
        assert (await s.call_tool("list_drives", args)).isError
    assert not seen


@pytest.mark.parametrize("authorization", [None, "Bearer wrong", "Basic dXNlcjpwYXNz"])
@pytest.mark.asyncio
async def test_rejects_unauthorized_before_mcp(authorization):
    app = app_with_upstream(lambda _: httpx.Response(200, json={}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000") as client:
        headers = {"Authorization": authorization} if authorization else {}
        r = await client.post("/mcp", headers=headers, json={})
        assert r.status_code == 401
        assert r.headers["www-authenticate"].startswith("Bearer")


@pytest.mark.asyncio
async def test_origin_policy_and_liveness():
    app = app_with_upstream(lambda _: httpx.Response(200, json={}))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000") as client:
            assert (await client.get("/healthz")).json() == {"status": "ok"}
            headers = {"Authorization": f"Bearer {TOKEN}"}
            assert (await client.post("/mcp", headers={**headers, "Origin": "https://evil.example"}, json={})).status_code == 403
            assert (await client.post("/mcp", headers={**headers, "Origin": "https://mcp.example.com"}, json={})).status_code != 403
            assert (await client.post("/mcp", headers=headers, json={})).status_code != 403


@pytest.mark.asyncio
async def test_tool_upstream_error_does_not_leak_response():
    async with session(app_with_upstream(lambda _: httpx.Response(500, text="secret database password"))) as s:
        result = await s.call_tool("list_cars", {})
        assert result.isError
        text = " ".join(c.text for c in result.content if c.type == "text")
        assert "HTTP 500" in text
        assert "password" not in text


@pytest.mark.asyncio
async def test_api_info_uses_canonical_trailing_slash_without_redirects():
    def upstream(request):
        if request.url.path == "/api/v1/":
            return httpx.Response(200, json={"message": "TeslaMateApi v1 running..", "path": "/api/v1"})
        return httpx.Response(301, headers={"Location": "/api/v1/"})
    async with session(app_with_upstream(upstream)) as s:
        result = await s.call_tool("get_api_info", {})
        assert not result.isError
        assert result.structuredContent["path"] == "/api/v1"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,path", [("list_drives", "/api/v1/cars/1/drives"),
                                     ("list_charges", "/api/v1/cars/1/charges")])
async def test_history_can_retrieve_pages_beyond_first_100(tool, path):
    def upstream(request):
        assert request.url.path == path
        assert dict(request.url.params) == {"page": "2", "show": "25"}
        return httpx.Response(200, json={"data": {"car": {"car_id": 1}}})
    async with session(app_with_upstream(upstream)) as s:
        result = await s.call_tool(tool, {"car_id": 1, "page": 2, "page_size": 25})
        assert not result.isError


@pytest.mark.asyncio
async def test_no_active_charge_preserves_known_absence_message():
    async with session(app_with_upstream(lambda _: httpx.Response(
        200, json={"error": "No active charging in progress."}))) as s:
        result = await s.call_tool("get_current_charge", {"car_id": 1})
        assert result.isError
        assert result.structuredContent == {"error": "No active charging in progress."}
