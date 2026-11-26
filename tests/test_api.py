import httpx
import pytest

from teslamate_mcp.api import APIError, TeslaMateAPI
from teslamate_mcp.config import Settings

pytestmark = pytest.mark.asyncio


def settings(**kwargs):
    return Settings(mcp_token="a" * 48, **kwargs)


async def test_preserves_json_and_filters_and_bearer_header():
    def upstream(request):
        assert request.method == "GET"
        assert str(request.url) == (
            "http://teslamateapi:8080/api/v1/cars/1/drives"
            "?startDate=2026-01-01T00%3A00%3A00Z&location=Home"
        )
        assert request.headers["Authorization"] == "Bearer upstream-secret"
        return httpx.Response(200, json={"data": {"drives": [{"drive_id": 9, "distance": 1.2}]}})

    api = TeslaMateAPI(settings(api_token="upstream-secret"), transport=httpx.MockTransport(upstream))
    assert await api.get("/api/v1/cars/1/drives", {"startDate": "2026-01-01T00:00:00Z", "location": "Home"}) == {
        "data": {"drives": [{"drive_id": 9, "distance": 1.2}]}
    }


async def test_custom_upstream_header_uses_raw_token():
    def upstream(request):
        assert request.headers["X-API-Token"] == "upstream-secret"
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"data": {}})

    api = TeslaMateAPI(settings(api_token="upstream-secret", api_token_header="X-API-Token"),
                       transport=httpx.MockTransport(upstream))
    assert await api.get("/api/v1/cars") == {"data": {}}


@pytest.mark.parametrize("path", [
    "/api/v1/cars/1/wake_up", "/api/v1/cars/1/command",
    "/api/v1/cars/1/logging",
    "http://evil/api/v1/cars", "/api/v1/cars/../settings", "/api/v1/cars/0",
])
async def test_non_read_only_routes_rejected(path):
    api = TeslaMateAPI(settings())
    with pytest.raises(ValueError):
        await api.get(path)


@pytest.mark.parametrize("status,body", [(401, b"secret-token"), (500, b"database password"),
                                       (302, b""), (200, b"<html>secret</html>")])
async def test_upstream_errors_are_sanitized(status, body):
    api = TeslaMateAPI(settings(), transport=httpx.MockTransport(
        lambda _: httpx.Response(status, content=body, headers={"Location": "http://evil"})))
    with pytest.raises(APIError) as exc:
        await api.get("/api/v1/cars")
    assert "secret" not in str(exc.value)
    assert "password" not in str(exc.value)


async def test_timeout_is_sanitized():
    def upstream(request):
        raise httpx.ReadTimeout("secret internal address", request=request)
    api = TeslaMateAPI(settings(), transport=httpx.MockTransport(upstream))
    with pytest.raises(APIError, match="unavailable|timed out"):
        await api.get("/api/v1/cars")


async def test_large_response_is_rejected():
    api = TeslaMateAPI(settings(max_response_bytes=32), transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=b'{"data":"' + b"x" * 100 + b'"}')))
    with pytest.raises(APIError, match="too large"):
        await api.get("/api/v1/cars")


async def test_http_200_application_errors_are_sanitized():
    api = TeslaMateAPI(settings(), transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"error": "private database password"})))
    with pytest.raises(APIError) as exc:
        await api.get("/api/v1/cars")
    assert "password" not in str(exc.value)


async def test_requests_uncompressed_response():
    def upstream(request):
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(200, json={"data": {}})
    api = TeslaMateAPI(settings(), transport=httpx.MockTransport(upstream))
    assert await api.get("/api/v1/cars") == {"data": {}}


async def test_compressed_stream_rejected_before_any_decompression_or_read():
    class CompressedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("Compressed body read before rejecting encoding")
            yield b""  # Makes this an async generator; must never be consumed.

    api = TeslaMateAPI(settings(max_response_bytes=32), transport=httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=CompressedStream())))
    with pytest.raises(APIError, match="compressed"):
        await api.get("/api/v1/cars")
