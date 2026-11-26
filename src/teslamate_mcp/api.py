import asyncio
import json
import re

import httpx

from .config import Settings


class APIError(Exception):
    """Safe error message suitable for MCP clients."""


_READ_ROUTES = re.compile(
    r"/api/?|/api/(?:healthz|readyz|ping)|/api/v1/?|/api/v1/globalsettings"
    r"|/api/v1/cars(?:/[1-9][0-9]*(?:/(?:status|battery-health|updates"
    r"|charges(?:/(?:current|[1-9][0-9]*))?|drives(?:/[1-9][0-9]*)?))?)?"
)


class TeslaMateAPI:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    async def get(self, path: str, params: dict | None = None) -> dict | list:
        if not _READ_ROUTES.fullmatch(path):
            raise ValueError("Only documented read-only API paths are allowed")
        # Bound memory before HTTPX can expand a compressed response chunk.
        headers = {"Accept-Encoding": "identity"}
        if self.settings.api_token:
            value = self.settings.api_token
            if self.settings.api_token_header.lower() == "authorization":
                value = f"Bearer {value}"
            headers[self.settings.api_token_header] = value
        try:
            async with asyncio.timeout(self.settings.timeout_seconds):
                async with httpx.AsyncClient(
                    base_url=self.settings.api_url, headers=headers, transport=self.transport,
                    timeout=self.settings.timeout_seconds, follow_redirects=False, trust_env=False,
                ) as client:
                    async with client.stream("GET", path, params=params) as response:
                        if response.status_code != 200:
                            raise APIError(f"TeslaMateAPI returned HTTP {response.status_code}")
                        if response.headers.get("content-encoding", "identity").lower().strip() != "identity":
                            raise APIError("TeslaMateAPI returned a compressed response; uncompressed data is required")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > self.settings.max_response_bytes:
                                raise APIError("TeslaMateAPI response too large; use a narrower date range")
                            body.extend(chunk)
                        try:
                            result = json.loads(body)
                        except (ValueError, UnicodeError):
                            raise APIError("TeslaMateAPI returned invalid JSON") from None
                        if not isinstance(result, (dict, list)):
                            raise APIError("TeslaMateAPI returned an unexpected JSON value")
                        if isinstance(result, dict) and "error" in result:
                            if (not path.endswith("/charges/current")
                                    or result.get("error") not in (
                                        "No active charging in progress.", "No current charge found.")):
                                raise APIError("TeslaMateAPI reported an application error")
                        return result
        except (httpx.HTTPError, TimeoutError):
            raise APIError("TeslaMateAPI unavailable or timed out") from None
