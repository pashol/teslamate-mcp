import hmac
import json
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import RequestBodyLimitMiddleware, TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api import APIError, TeslaMateAPI
from .config import Settings

PositiveID = Annotated[int, Field(gt=0, strict=True)]
Distance = Annotated[float, Field(ge=0, allow_inf_nan=False)]
PageSize = Annotated[int, Field(ge=1, le=500, strict=True)]


class SecurityMiddleware:
    def __init__(self, app, settings: Settings, oauth_provider=None, oauth_paths=()):
        self.app = app
        self.settings = settings
        self.oauth_provider = oauth_provider
        self.oauth_paths = frozenset(oauth_paths)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/healthz":
            if scope["path"] in self.oauth_paths:
                await self.app(scope, receive, send)
                return
            headers = httpx.Headers(scope["headers"])
            origin = headers.get("origin")
            if origin is not None and origin not in self.settings.allowed_origins:
                await JSONResponse({"error": "Origin not allowed"}, status_code=403)(scope, receive, send)
                return
            auth = headers.get("authorization", "")
            scheme, _, token = auth.partition(" ")
            authenticated = scheme.lower() == "bearer" and hmac.compare_digest(
                token.encode(), self.settings.mcp_token.encode())
            if not authenticated and scheme.lower() == "bearer" and token and self.oauth_provider:
                authenticated = await self.oauth_provider.verify_token(token) is not None
            if not authenticated:
                challenge = 'Bearer realm="teslamate-mcp"'
                if self.oauth_provider:
                    challenge += (
                        f', resource_metadata="{self.settings.oauth_base_url}'
                        '/.well-known/oauth-protected-resource/mcp"'
                    )
                await JSONResponse(
                    {"error": "Bearer authentication required"}, status_code=401,
                    headers={"WWW-Authenticate": challenge},
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _dates(start_date: str | None, end_date: str | None) -> dict:
    params = {}
    parsed = []
    for name, value in (("startDate", start_date), ("endDate", end_date)):
        if value is None:
            parsed.append(None)
            continue
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if "T" not in value or dt.tzinfo is None:
                raise ValueError
        except ValueError:
            raise ToolError("Dates must be RFC3339 timestamps with a timezone") from None
        parsed.append(dt)
        params[name] = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if all(parsed) and parsed[0] > parsed[1]:
        raise ToolError("start_date must not be after end_date")
    return params


def create_app(settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None):
    api = TeslaMateAPI(settings, transport=transport)
    mcp = FastMCP(
        "TeslaMate", instructions=(
            "Read-only TeslaMate telemetry and history. Values use the upstream TeslaMate settings; "
            "never assume units. Prefer narrow date ranges for history. Status is last logged data, "
            "not necessarily live. No tools wake or control the car."
        ),
        host="0.0.0.0", port=8000, stateless_http=True, json_response=True,
        max_request_body_size=65536,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["localhost:*", "127.0.0.1:*", "teslamate-mcp:*",
                           *[urlsplit(x).netloc for x in settings.allowed_origins]],
            allowed_origins=list(settings.allowed_origins),
        ),
    )
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                idempotentHint=True, openWorldHint=True)

    async def fetch(path: str, params: dict | None = None) -> CallToolResult:
        try:
            data = await api.get(path, params)
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(data, ensure_ascii=False))],
                structuredContent=data if isinstance(data, dict) else {"result": data},
                isError=isinstance(data, dict) and "error" in data,
            )
        except APIError as exc:
            raise ToolError(str(exc)) from None

    @mcp.tool(annotations=read_only)
    async def list_cars() -> CallToolResult:
        """List logged cars and their TeslaMate IDs."""
        return await fetch("/api/v1/cars")

    @mcp.tool(annotations=read_only)
    async def get_car(car_id: PositiveID) -> CallToolResult:
        """Get one logged car."""
        return await fetch(f"/api/v1/cars/{car_id}")

    @mcp.tool(annotations=read_only)
    async def get_status(car_id: PositiveID) -> CallToolResult:
        """Get last logged status; does not wake or contact the vehicle."""
        return await fetch(f"/api/v1/cars/{car_id}/status")

    @mcp.tool(annotations=read_only)
    async def get_battery_health(car_id: PositiveID) -> CallToolResult:
        """Get estimated battery health from logged data."""
        return await fetch(f"/api/v1/cars/{car_id}/battery-health")

    @mcp.tool(annotations=read_only)
    async def list_charges(car_id: PositiveID, start_date: str | None = None,
                           end_date: str | None = None, location: str | None = None,
                           page: PositiveID = 1, page_size: PageSize = 100) -> CallToolResult:
        """List a page of charges, newest first. Use RFC3339 dates; increment page for more records."""
        params = _dates(start_date, end_date)
        params.update(page=page, show=page_size)
        if location is not None:
            params["location"] = location
        return await fetch(f"/api/v1/cars/{car_id}/charges", params)

    @mcp.tool(annotations=read_only)
    async def get_current_charge(car_id: PositiveID) -> CallToolResult:
        """Get the current charging session from logged data."""
        return await fetch(f"/api/v1/cars/{car_id}/charges/current")

    @mcp.tool(annotations=read_only)
    async def get_charge(car_id: PositiveID, charge_id: PositiveID) -> CallToolResult:
        """Get details of a charging session."""
        return await fetch(f"/api/v1/cars/{car_id}/charges/{charge_id}")

    @mcp.tool(annotations=read_only)
    async def list_drives(car_id: PositiveID, start_date: str | None = None,
                          end_date: str | None = None, min_distance: Distance | None = None,
                          max_distance: Distance | None = None,
                          location: str | None = None, page: PositiveID = 1,
                          page_size: PageSize = 100) -> CallToolResult:
        """List a page of drives, newest first. Increment page for more. RFC3339 dates; TeslaMate units."""
        params = _dates(start_date, end_date)
        params.update(page=page, show=page_size)
        if min_distance is not None and max_distance is not None and min_distance > max_distance:
            raise ToolError("min_distance must not exceed max_distance")
        for name, value in (("minDistance", min_distance), ("maxDistance", max_distance), ("location", location)):
            if value is not None:
                params[name] = value
        return await fetch(f"/api/v1/cars/{car_id}/drives", params)

    @mcp.tool(annotations=read_only)
    async def get_drive(car_id: PositiveID, drive_id: PositiveID) -> CallToolResult:
        """Get details of a recorded drive."""
        return await fetch(f"/api/v1/cars/{car_id}/drives/{drive_id}")

    @mcp.tool(annotations=read_only)
    async def get_updates(car_id: PositiveID) -> CallToolResult:
        """Get recorded vehicle software updates."""
        return await fetch(f"/api/v1/cars/{car_id}/updates")

    @mcp.tool(annotations=read_only)
    async def get_global_settings() -> CallToolResult:
        """Get TeslaMate settings, including configured units."""
        return await fetch("/api/v1/globalsettings")

    @mcp.tool(annotations=read_only)
    async def get_api_info() -> CallToolResult:
        """Get TeslaMateAPI v1 information."""
        return await fetch("/api/v1/")

    @mcp.custom_route("/healthz", methods=["GET"])
    async def health(_: Request):
        return JSONResponse({"status": "ok"})

    app = mcp.streamable_http_app()
    oauth_provider = None
    if settings.google_client_id:
        try:
            from .auth import create_oauth_provider
        except ModuleNotFoundError:
            raise RuntimeError(
                "Google OAuth requires the optional .[oauth] dependencies; "
                "for Docker, rebuild with INSTALL_OAUTH=true"
            ) from None
        oauth_provider = create_oauth_provider(settings)
    oauth_routes = oauth_provider.get_routes("/mcp") if oauth_provider else []
    app.router.routes.extend(oauth_routes)
    app.add_middleware(
        SecurityMiddleware, settings=settings, oauth_provider=oauth_provider,
        oauth_paths=[route.path for route in oauth_routes],
    )
    if oauth_provider:
        app.add_middleware(RequestBodyLimitMiddleware, max_body_size=65536)
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=[
            "localhost", "127.0.0.1", "teslamate-mcp",
            *[urlsplit(x).hostname for x in settings.allowed_origins],
        ])
    return app
