import base64
import time

import pytest
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleProvider

from teslamate_mcp.auth import create_oauth_provider
from teslamate_mcp.config import Settings


def oauth_settings(tmp_path, **overrides):
    values = dict(
        mcp_token="t" * 48,
        google_client_id="test.apps.googleusercontent.com",
        google_client_secret="dummy-google-secret",
        google_allowed_emails=("user@example.com",),
        allowed_origins=("https://mcp.example.com",),
        oauth_base_url="https://mcp.example.com",
        oauth_signing_key="s" * 48,
        oauth_storage_key=base64.urlsafe_b64encode(b"k" * 32).decode(),
        oauth_storage_dir=str(tmp_path / "oauth"),
    )
    return Settings(**(values | overrides))


@pytest.mark.asyncio
@pytest.mark.parametrize("email,verified,audience,expiry,allowed", [
    ("user@example.com", True, "test.apps.googleusercontent.com", 300, True),
    ("User@Example.com", "true", "test.apps.googleusercontent.com", 300, True),
    ("someone@gmail.com", True, "test.apps.googleusercontent.com", 300, False),
    ("another@example.com", True, "test.apps.googleusercontent.com", 300, False),
    ("user+other@example.com", True, "test.apps.googleusercontent.com", 300, False),
    (None, True, "test.apps.googleusercontent.com", 300, False),
    ("user@example.com", False, "test.apps.googleusercontent.com", 300, False),
    ("user@example.com", "false", "test.apps.googleusercontent.com", 300, False),
    ("user@example.com", None, "test.apps.googleusercontent.com", 300, False),
    ("user@example.com", 0, "test.apps.googleusercontent.com", 300, False),
    ("user@example.com", 1, "test.apps.googleusercontent.com", 300, False),
    ("user@example.com", True, "another.apps.googleusercontent.com", 300, False),
    ("user@example.com", True, "test.apps.googleusercontent.com", -10, False),
    ("user@example.com", True, "test.apps.googleusercontent.com", None, False),
])
async def test_verified_google_identity_policy(tmp_path, monkeypatch, email, verified,
                                              audience, expiry, allowed):
    async def validated(self, token):
        return AccessToken(
            token=token, client_id="google-subject", scopes=["openid"],
            expires_at=int(time.time()) + expiry if expiry is not None else None,
            claims={"email": email, "email_verified": verified, "aud": audience},
        )
    monkeypatch.setattr(GoogleProvider, "load_access_token", validated)
    provider = create_oauth_provider(oauth_settings(tmp_path))
    result = await provider.verify_token("fake-downstream-token")
    assert (result is not None) is allowed


@pytest.mark.asyncio
async def test_raw_upstream_or_invalid_token_is_not_accepted(tmp_path):
    provider = create_oauth_provider(oauth_settings(tmp_path))
    provider.get_routes("/mcp")
    assert await provider.verify_token("raw-google-access-token") is None


def test_legacy_configuration_has_no_oauth_provider():
    assert create_oauth_provider(Settings(mcp_token="t" * 48)) is None
