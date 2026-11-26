import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    mcp_token: str = field(repr=False)
    api_url: str = "http://teslamateapi:8080"
    api_token: str = field(default="", repr=False)
    api_token_header: str = "Authorization"
    allowed_origins: tuple[str, ...] = ()
    timeout_seconds: float = 20
    max_response_bytes: int = 4 * 1024 * 1024
    google_client_id: str = ""
    google_client_secret: str = field(default="", repr=False)
    google_allowed_emails: tuple[str, ...] = ()
    oauth_base_url: str = ""
    oauth_signing_key: str = field(default="", repr=False)
    oauth_storage_key: str = field(default="", repr=False)
    oauth_storage_dir: str = ""

    def __post_init__(self):
        if len(self.mcp_token) < 32 or any(c.isspace() for c in self.mcp_token):
            raise ValueError("MCP_BEARER_TOKEN must contain at least 32 non-whitespace characters")
        url = urlsplit(self.api_url)
        if (url.scheme not in ("http", "https") or not url.hostname or url.username
                or url.password or url.query or url.fragment or url.path not in ("", "/")):
            raise ValueError("TESLAMATE_API_URL must be an HTTP(S) origin without credentials")
        object.__setattr__(self, "api_url", self.api_url.rstrip("/"))
        if self.timeout_seconds <= 0 or self.max_response_bytes <= 0:
            raise ValueError("Timeout and response limit must be positive")
        if self.api_token and self.api_token == self.mcp_token:
            raise ValueError("MCP and upstream tokens must be distinct")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if (parsed.scheme not in ("https", "http") or not parsed.hostname
                    or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password):
                raise ValueError("MCP_ALLOWED_ORIGINS must contain HTTP(S) origins")
        oauth_values = (self.google_client_id, self.google_client_secret,
                        self.google_allowed_emails, self.oauth_base_url,
                        self.oauth_signing_key, self.oauth_storage_key, self.oauth_storage_dir)
        if not any(oauth_values):
            return
        if not all(oauth_values):
            raise ValueError("OAuth configuration requires all Google, URL, key and storage settings")
        public = urlsplit(self.oauth_base_url)
        if (public.scheme != "https" or not public.hostname or public.username
                or public.password or public.query or public.fragment
                or public.path not in ("", "/")):
            raise ValueError("OAuth base URL must be an HTTPS origin without credentials")
        object.__setattr__(self, "oauth_base_url", self.oauth_base_url.rstrip("/"))
        if self.oauth_base_url not in self.allowed_origins:
            raise ValueError("OAuth base URL must be included in MCP_ALLOWED_ORIGINS")
        if (len(self.oauth_signing_key) < 32
                or any(c.isspace() for c in self.oauth_signing_key)):
            raise ValueError("OAuth signing key must contain at least 32 non-whitespace characters")
        try:
            from cryptography.fernet import Fernet

            Fernet(self.oauth_storage_key.encode("ascii"))
        except (ValueError, UnicodeError):
            raise ValueError("OAuth storage key must be a Fernet key") from None
        if not Path(self.oauth_storage_dir).is_absolute():
            raise ValueError("OAuth storage directory must be absolute")
        emails = tuple(email.strip().lower() for email in self.google_allowed_emails)
        if any(not email or email.count("@") != 1 or any(c.isspace() for c in email)
               for email in emails):
            raise ValueError("OAuth allowlist must contain explicit email addresses")
        object.__setattr__(self, "google_allowed_emails", emails)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            mcp_token=os.environ.get("MCP_BEARER_TOKEN", ""),
            api_url=os.environ.get("TESLAMATE_API_URL", "http://teslamateapi:8080"),
            api_token=os.environ.get("TESLAMATE_API_TOKEN", ""),
            api_token_header=os.environ.get("TESLAMATE_API_TOKEN_HEADER", "Authorization"),
            allowed_origins=tuple(x.strip() for x in os.environ.get(
                "MCP_ALLOWED_ORIGINS", "").split(",") if x.strip()),
            google_client_id=os.environ.get("MCP_GOOGLE_CLIENT_ID", ""),
            google_client_secret=os.environ.get("MCP_GOOGLE_CLIENT_SECRET", ""),
            google_allowed_emails=tuple(x.strip() for x in os.environ.get(
                "MCP_GOOGLE_ALLOWED_EMAILS", "").split(",") if x.strip()),
            oauth_base_url=os.environ.get("MCP_OAUTH_BASE_URL", ""),
            oauth_signing_key=os.environ.get("MCP_OAUTH_SIGNING_KEY", ""),
            oauth_storage_key=os.environ.get("MCP_OAUTH_STORAGE_KEY", ""),
            oauth_storage_dir=os.environ.get("MCP_OAUTH_STORAGE_DIR", ""),
        )
