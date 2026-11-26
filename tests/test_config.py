import base64

import pytest

from teslamate_mcp.config import Settings


def test_requires_strong_separate_mcp_token(monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "short-secret")
    with pytest.raises(ValueError) as exc:
        Settings.from_env()
    assert "short-secret" not in str(exc.value)


def test_normalizes_base_and_origins(monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "a" * 48)
    monkeypatch.setenv("TESLAMATE_API_URL", "http://teslamateapi:8080/")
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "https://mcp.example.com")
    settings = Settings.from_env()
    assert settings.api_url == "http://teslamateapi:8080"
    assert settings.allowed_origins == ("https://mcp.example.com",)
    assert "a" * 48 not in repr(settings)


def test_does_not_use_a_built_in_public_origin(monkeypatch):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "a" * 48)
    monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)
    settings = Settings.from_env()
    assert settings.allowed_origins == ()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://user:secret@host", "http://host/?token=x"])
def test_rejects_unsafe_base_url(monkeypatch, url):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "a" * 48)
    monkeypatch.setenv("TESLAMATE_API_URL", url)
    with pytest.raises(ValueError):
        Settings.from_env()


def oauth_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_BEARER_TOKEN", "t" * 48)
    values = {
        "MCP_GOOGLE_CLIENT_ID": "test.apps.googleusercontent.com",
        "MCP_GOOGLE_CLIENT_SECRET": "dummy-google-secret",
        "MCP_GOOGLE_ALLOWED_EMAILS": "User@Example.com",
        "MCP_ALLOWED_ORIGINS": "https://mcp.example.com",
        "MCP_OAUTH_BASE_URL": "https://mcp.example.com",
        "MCP_OAUTH_SIGNING_KEY": "s" * 48,
        "MCP_OAUTH_STORAGE_KEY": base64.urlsafe_b64encode(b"k" * 32).decode(),
        "MCP_OAUTH_STORAGE_DIR": str(tmp_path / "oauth"),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def test_google_configuration_is_loaded_without_revealing_secrets(monkeypatch, tmp_path):
    values = oauth_env(monkeypatch, tmp_path)
    settings = Settings.from_env()
    assert getattr(settings, "google_allowed_emails", None) == ("user@example.com",)
    assert settings.google_client_id == "test.apps.googleusercontent.com"
    assert settings.oauth_base_url == "https://mcp.example.com"
    for name in ("MCP_GOOGLE_CLIENT_SECRET", "MCP_OAUTH_SIGNING_KEY", "MCP_OAUTH_STORAGE_KEY"):
        assert values[name] not in repr(settings)


@pytest.mark.parametrize("missing", [
    "MCP_GOOGLE_CLIENT_ID", "MCP_GOOGLE_CLIENT_SECRET", "MCP_GOOGLE_ALLOWED_EMAILS",
    "MCP_OAUTH_BASE_URL", "MCP_OAUTH_SIGNING_KEY", "MCP_OAUTH_STORAGE_KEY",
    "MCP_OAUTH_STORAGE_DIR",
])
def test_partial_oauth_configuration_fails_closed(monkeypatch, tmp_path, missing):
    oauth_env(monkeypatch, tmp_path)
    monkeypatch.delenv(missing)
    with pytest.raises(ValueError, match="OAuth"):
        Settings.from_env()


@pytest.mark.parametrize("name,value", [
    ("MCP_OAUTH_BASE_URL", "http://mcp.example.com"),
    ("MCP_OAUTH_BASE_URL", "https://user:password@mcp.example.com"),
    ("MCP_OAUTH_BASE_URL", "https://mcp.example.com/mcp"),
    ("MCP_OAUTH_BASE_URL", "https://mcp.example.com/?secret=x"),
    ("MCP_OAUTH_STORAGE_KEY", "not-a-fernet-key"),
    ("MCP_OAUTH_SIGNING_KEY", "short"),
    ("MCP_OAUTH_SIGNING_KEY", "s" * 47 + " "),
    ("MCP_OAUTH_STORAGE_DIR", "relative/path"),
    ("MCP_GOOGLE_ALLOWED_EMAILS", ", ,"),
])
def test_unsafe_oauth_configuration_is_rejected(monkeypatch, tmp_path, name, value):
    oauth_env(monkeypatch, tmp_path)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="OAuth"):
        Settings.from_env()
