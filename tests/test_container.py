import json
import os
import subprocess


def test_compose_service_is_internal_and_hardened():
    result = subprocess.run(
        ["docker", "compose", "-f", "compose.example.yml", "config", "--format", "json"],
        env={**os.environ, "MCP_BEARER_TOKEN": "test-token-not-for-production",
             "MCP_ALLOWED_ORIGINS": "https://mcp.example.test"},
        check=True, text=True, capture_output=True,
    )
    service = json.loads(result.stdout)["services"]["teslamate-mcp"]
    assert not service.get("ports")
    assert service["read_only"]
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["healthcheck"]["test"][0] == "CMD"
    assert not any("docker.sock" in str(v) for v in service.get("volumes", []))
    assert service["environment"]["TESLAMATE_API_URL"] == "http://teslamateapi:8080"
    assert service["environment"]["MCP_ALLOWED_ORIGINS"] == "https://mcp.example.test"
    assert service["restart"] == "unless-stopped"
    assert not service.get("volumes")
    assert not any(k.startswith(("MCP_GOOGLE_", "MCP_OAUTH_")) for k in service["environment"])
    assert service["build"].get("args", {}).get("INSTALL_OAUTH", "false") == "false"


def test_compose_passes_optional_oauth_without_losing_bearer_access():
    result = subprocess.run(
        ["docker", "compose", "-f", "compose.example.yml", "-f", "compose.oauth.example.yml",
         "config", "--format", "json"],
        env={**os.environ, "MCP_BEARER_TOKEN": "test-token-not-for-production",
             "MCP_ALLOWED_ORIGINS": "https://mcp.example.test",
             "MCP_GOOGLE_CLIENT_ID": "dummy.apps.googleusercontent.com",
             "MCP_GOOGLE_CLIENT_SECRET": "dummy-secret"},
        check=True, text=True, capture_output=True,
    )
    service = json.loads(result.stdout)["services"]["teslamate-mcp"]
    environment = service["environment"]
    assert environment["MCP_GOOGLE_CLIENT_ID"] == "dummy.apps.googleusercontent.com"
    assert environment["MCP_GOOGLE_CLIENT_SECRET"] == "dummy-secret"
    assert environment["MCP_BEARER_TOKEN"] == "test-token-not-for-production"
    assert service["build"]["args"]["INSTALL_OAUTH"] == "true"
    state_mount = next(v for v in service["volumes"] if v["target"] == "/var/lib/teslamate-mcp")
    assert state_mount["type"] == "volume"
    assert not state_mount.get("read_only", False)


def test_default_server_starts_without_importing_oauth_packages():
    import sys
    # A fresh interpreter models the bearer-only install. Merely having the
    # extras installed in the development venv must not hide an eager import.
    result = subprocess.run([sys.executable, "-c", """
import importlib.abc
import sys
class NoOAuthPackages(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('fastmcp', 'key_value'):
            raise ModuleNotFoundError('Optional OAuth package must not load: ' + fullname)
sys.meta_path.insert(0, NoOAuthPackages())
from teslamate_mcp.config import Settings
from teslamate_mcp.server import create_app
app = create_app(Settings(mcp_token='t' * 48))
assert app is not None
"""], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
