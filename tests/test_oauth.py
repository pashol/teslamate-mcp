import base64
import hashlib
import logging
import re
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest

from teslamate_mcp.auth import create_oauth_provider
from teslamate_mcp.server import create_app
from test_auth import oauth_settings
from test_server import session

BASE = "https://mcp.example.com"
REDIRECT = "https://chatgpt.com/connector_platform/oauth/callback"
SCOPES = "openid https://www.googleapis.com/auth/userinfo.email"
VERIFIER = "v" * 64


@pytest.fixture
def fake_google(monkeypatch):
    identity = {
        "aud": "test.apps.googleusercontent.com", "sub": "google-user-123",
        "email": "user@example.com", "email_verified": "true",
        "scope": SCOPES, "expires_in": "3600",
    }
    state = {"identity": identity, "unavailable": False, "revoked": False}
    def google(request):
        assert request.url.host in ("oauth2.googleapis.com", "www.googleapis.com")
        if state["unavailable"]:
            raise httpx.ConnectError("simulated Google outage", request=request)
        if request.url.path == "/token":
            form = parse_qs(request.content.decode())
            if "client_id" in form:
                assert form["client_id"] == ["test.apps.googleusercontent.com"]
            else:
                scheme, value = request.headers["authorization"].split(" ", 1)
                assert scheme == "Basic"
                assert base64.b64decode(value).decode() == "test.apps.googleusercontent.com:dummy-google-secret"
            if state["revoked"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={
                "access_token": "dummy-google-access-token",
                "refresh_token": "dummy-google-refresh-token",
                "expires_in": 3600, "token_type": "Bearer", "scope": SCOPES,
            })
        if request.url.path == "/tokeninfo":
            if state["revoked"]:
                return httpx.Response(400, json={"error": "invalid_token"})
            return httpx.Response(200, json=identity)
        if request.url.path == "/oauth2/v2/userinfo":
            return httpx.Response(200, json={
                "id": "google-user-123", "email": identity.get("email"),
                "verified_email": identity.get("email_verified") == "true",
            })
        raise AssertionError(f"unexpected Google path: {request.url.path}")
    original_init = httpx.AsyncClient.__init__
    def client_init(self, *args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(google))
        original_init(self, *args, **kwargs)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", client_init)
    return state


async def authorize(client, *, clear_cookies=False, wrong_state=False):
    registration = await client.post("/register", json={
        "client_name": "ChatGPT test", "redirect_uris": [REDIRECT],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none",
        "scope": SCOPES,
    })
    assert registration.status_code == 201, registration.text
    client_id = registration.json()["client_id"]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
    response = await client.get("/authorize", params={
        "client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": SCOPES, "state": "chatgpt-state", "code_challenge": challenge,
        "code_challenge_method": "S256", "resource": BASE + "/mcp",
    })
    assert response.status_code in (302, 303), response.text
    consent_url = response.headers["location"]
    page = await client.get(consent_url)
    assert page.status_code == 200, page.text
    data = dict(re.findall(r'name="(txn_id|csrf_token)" value="([^"]+)"', page.text))
    assert set(data) == {"txn_id", "csrf_token"}
    approved = await client.post(consent_url, data=data | {"action": "approve"})
    assert approved.status_code == 302, approved.text
    google_url = urlsplit(approved.headers["location"])
    assert google_url.hostname == "accounts.google.com"
    google_params = parse_qs(google_url.query)
    assert google_params["redirect_uri"] == [BASE + "/auth/callback"]
    assert google_params["code_challenge_method"] == ["S256"]
    assert set(google_params["scope"][0].split()) == set(SCOPES.split())
    if clear_cookies:
        client.cookies.clear()
    callback = await client.get("/auth/callback", params={
        "code": "dummy-google-code",
        "state": "invalid-state" if wrong_state else google_params["state"][0],
    })
    if clear_cookies or wrong_state:
        assert callback.status_code >= 400
        assert "location" not in callback.headers
        return None
    assert callback.status_code == 302, callback.text
    downstream = urlsplit(callback.headers["location"])
    assert downstream.hostname == "chatgpt.com"
    params = parse_qs(downstream.query)
    assert params["state"] == ["chatgpt-state"]
    return {
        "grant_type": "authorization_code", "client_id": client_id,
        "code": params["code"][0], "redirect_uri": REDIRECT,
        "code_verifier": VERIFIER, "resource": BASE + "/mcp",
    }


@pytest.mark.asyncio
async def test_oauth_discovery_and_registration_are_public_but_mcp_is_not(tmp_path):
    app = create_app(oauth_settings(tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        rejected = await client.post("/mcp", json={})
        assert rejected.status_code == 401
        challenge = rejected.headers["www-authenticate"]
        assert 'resource_metadata="' in challenge
        metadata_url = challenge.split('resource_metadata="')[1].split('"')[0]
        resource = await client.get(metadata_url)
        assert resource.status_code == 200
        assert resource.json()["resource"] == BASE + "/mcp"
        assert resource.json()["authorization_servers"] == [BASE + "/"]
        discovery = await client.get("/.well-known/oauth-authorization-server")
        assert discovery.status_code == 200
        assert discovery.json()["registration_endpoint"] == BASE + "/register"
        registered = await client.post("/register", json={
            "client_name": "ChatGPT test",
            "redirect_uris": ["https://chatgpt.com/connector_platform/oauth/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        })
        assert registered.status_code == 201
        assert registered.json()["client_id"]
        assert (await client.get("/healthz")).json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_existing_bearer_clients_still_call_tools_with_oauth_enabled(tmp_path):
    app = create_app(oauth_settings(tmp_path), transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": {"cars": []}})))
    async with session(app) as s:
        assert len((await s.list_tools()).tools) == 12
        assert not (await s.call_tool("list_cars", {})).isError


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/mcp", "/mcp/", "/authorize/extra", "/register/", "/unknown"])
async def test_no_anonymous_path_bypass(tmp_path, path):
    app = create_app(oauth_settings(tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        r = await client.post(path, json={})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_host_origin_and_public_oauth_body_limits(tmp_path):
    app = create_app(oauth_settings(tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        assert (await client.get("/.well-known/oauth-authorization-server",
                                 headers={"Host": "evil.example"})).status_code in (400, 421)
        assert (await client.post("/mcp", json={}, headers={
            "Authorization": "Bearer " + "t" * 48,
            "Origin": "https://evil.example",
        })).status_code == 403
        # The provider's consent protections, not the MCP Origin allowlist,
        # handle cross-site OAuth requests.
        r = await client.post("/authorize", data={}, headers={"Origin": "https://chatgpt.com"})
        assert r.status_code != 403
        assert r.status_code != 401
        assert (await client.post("/register", content=b"x" * 65537)).status_code == 413


@pytest.mark.asyncio
async def test_raw_google_token_cannot_reach_teslamate(tmp_path):
    seen = []
    def upstream(request):
        seen.append(request)
        return httpx.Response(200, json={})
    app = create_app(oauth_settings(tmp_path), transport=httpx.MockTransport(upstream))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        r = await client.post("/mcp", json={}, headers={"Authorization": "Bearer google-token"})
        assert r.status_code == 401
    assert not seen


@pytest.mark.asyncio
async def test_complete_oauth_flow_refresh_persistence_and_no_secret_logging(tmp_path, fake_google, caplog):
    caplog.set_level(logging.INFO)
    settings = oauth_settings(tmp_path)
    def make_app(config=settings):
        return create_app(config, transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"data": {"cars": []}})))
    app = make_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        exchange = await authorize(client)
        response = await client.post("/token", data=exchange)
        assert response.status_code == 200, response.text
        tokens = response.json()
        assert tokens["access_token"] != "dummy-google-access-token"
        async with session(app, tokens["access_token"]) as s:
            assert len((await s.list_tools()).tools) == 12
            assert not (await s.call_tool("list_cars", {})).isError
        assert (await client.post("/token", data=exchange)).status_code >= 400
    # A new application/provider reads the same encrypted persistent state.
    recreated = make_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=recreated), base_url=BASE) as client:
        refreshed = await client.post("/token", data={
            "grant_type": "refresh_token", "client_id": exchange["client_id"],
            "refresh_token": tokens["refresh_token"],
        })
        assert refreshed.status_code == 200, refreshed.text
        async with session(recreated, refreshed.json()["access_token"]) as s:
            assert not (await s.call_tool("list_cars", {})).isError
    files = [p for p in (tmp_path / "oauth").rglob("*") if p.is_file()]
    assert files
    for path in files:
        raw = path.read_bytes()
        assert b"dummy-google-access-token" not in raw
        assert b"dummy-google-refresh-token" not in raw
    wrong_key = oauth_settings(tmp_path, oauth_storage_key=base64.urlsafe_b64encode(b"z" * 32).decode())
    broken = make_app(wrong_key)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=broken, raise_app_exceptions=False), base_url=BASE) as client:
        result = await client.post("/token", data={
            "grant_type": "refresh_token", "client_id": exchange["client_id"],
            "refresh_token": refreshed.json()["refresh_token"],
        })
        assert result.status_code >= 400
    for secret in ("dummy-google-access-token", "dummy-google-refresh-token", "dummy-google-secret"):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_proxy_token_signature_issuer_audience_and_expiry_are_enforced(tmp_path, fake_google):
    settings = oauth_settings(tmp_path)
    app = create_app(settings)
    # Use the same configured signing key only to construct deliberately invalid
    # signed test credentials. The real HTTP authentication path must reject them.
    provider = create_oauth_provider(settings)
    provider.get_routes("/mcp")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        exchange = await authorize(client)
        response = await client.post("/token", data=exchange)
        original = response.json()["access_token"]
        claims = jwt.decode(original, options={"verify_signature": False})
        for changed in (
            {"iss": "https://evil.example"},
            {"aud": "https://another.example/mcp"},
            {"exp": int(time.time()) - 600},
        ):
            invalid = jwt.encode(claims | changed, provider._jwt_signing_key, algorithm="HS256")
            r = await client.post("/mcp", json={}, headers={"Authorization": f"Bearer {invalid}"})
            assert r.status_code == 401
        invalid = jwt.encode(claims, b"wrong-signing-key" * 3, algorithm="HS256")
        r = await client.post("/mcp", json={}, headers={"Authorization": f"Bearer {invalid}"})
        assert r.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cookie", "state"])
async def test_callback_is_bound_to_browser_and_state(tmp_path, fake_google, failure):
    app = create_app(oauth_settings(tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        await authorize(client, clear_cookies=failure == "cookie", wrong_state=failure == "state")


@pytest.mark.asyncio
async def test_wrong_pkce_and_unregistered_redirect_are_rejected(tmp_path, fake_google):
    app = create_app(oauth_settings(tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        exchange = await authorize(client)
        response = await client.post("/token", data=exchange | {"code_verifier": "w" * 64})
        assert response.status_code >= 400
        response = await client.get("/authorize", params={
            "client_id": exchange["client_id"], "redirect_uri": "https://evil.example/callback",
            "response_type": "code", "code_challenge": "x" * 43, "code_challenge_method": "S256",
        })
        assert response.status_code >= 400
        assert "location" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["email", "unverified", "audience", "expired", "scope", "outage", "revoked"])
async def test_invalid_google_sessions_never_reach_teslamate(tmp_path, fake_google, failure):
    def forbidden(request):
        pytest.fail("Denied identity reached TeslaMateAPI")
    app = create_app(oauth_settings(tmp_path), transport=httpx.MockTransport(forbidden))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        exchange = await authorize(client)
        response = await client.post("/token", data=exchange)
        assert response.status_code == 200, response.text
        token = response.json()["access_token"]
        identity = fake_google["identity"]
        if failure == "email":
            identity["email"] = "someone@gmail.com"
        elif failure == "unverified":
            identity["email_verified"] = "false"
        elif failure == "audience":
            identity["aud"] = "another.apps.googleusercontent.com"
        elif failure == "expired":
            identity["expires_in"] = "-20"
        elif failure == "scope":
            identity["scope"] = "openid"
        elif failure == "outage":
            fake_google["unavailable"] = True
        else:
            fake_google["revoked"] = True
        response = await client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "list_cars", "arguments": {}},
        }, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
        })
        assert response.status_code == 401
