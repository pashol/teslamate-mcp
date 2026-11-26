"""Optional Google OAuth bridge; vehicle/API credentials never enter this flow."""

import logging
import re
import time
from pathlib import Path

from cryptography.fernet import Fernet
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleProvider
from key_value.aio.stores.filetree import FileTreeStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from .config import Settings

GOOGLE_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]


class OAuthURLFilter(logging.Filter):
    """Redact credentials in HTTPX/Google diagnostic URLs (including failures)."""

    pattern = re.compile(
        r"([?&](?:access_token|refresh_token|client_secret|code|state|txn_id|code_verifier)=)"
        r"[^&\s\"'<>]+"
    )

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.pattern.sub(r"\1[REDACTED]", record.getMessage())
        record.args = ()
        return True


class AllowedGoogleProvider(GoogleProvider):
    def __init__(self, *, allowed_emails: tuple[str, ...], **kwargs):
        super().__init__(**kwargs)
        self.allowed_emails = frozenset(allowed_emails)
        self.google_audience = kwargs["client_id"]

    async def load_access_token(self, token: str) -> AccessToken | None:
        # The parent validates the proxy JWT and looks up/verifies its Google
        # session. Never run the Google verifier directly on a client credential.
        validated = await super().load_access_token(token)
        if validated is None:
            return None
        claims = validated.claims or {}
        email = claims.get("email")
        verified = claims.get("email_verified")
        if (not isinstance(email, str) or email.lower() not in self.allowed_emails
                or not (verified is True or verified == "true")
                or claims.get("aud") != self.google_audience
                or validated.expires_at is None or validated.expires_at <= time.time()):
            return None
        return validated


def create_oauth_provider(settings: Settings) -> GoogleProvider | None:
    if not settings.google_client_id:
        return None
    # This compatible provider version uses Google's documented query-parameter
    # tokeninfo API. HTTPX normally includes the full URL in INFO-level logs.
    for name in ("httpx", "fastmcp.server.auth.providers.google",
                 "fastmcp.server.auth.oauth_proxy.proxy"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, OAuthURLFilter) for f in logger.filters):
            logger.addFilter(OAuthURLFilter())
    directory = Path(settings.oauth_storage_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    storage = FernetEncryptionWrapper(
        FileTreeStore(data_directory=directory),
        fernet=Fernet(settings.oauth_storage_key.encode("ascii")),
    )
    return AllowedGoogleProvider(
        allowed_emails=settings.google_allowed_emails,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        base_url=settings.oauth_base_url,
        redirect_path="/auth/callback",
        required_scopes=GOOGLE_SCOPES,
        valid_scopes=GOOGLE_SCOPES,
        jwt_signing_key=settings.oauth_signing_key,
        client_storage=storage,
        require_authorization_consent=True,
        # Google's provider does not implement RFC 8707. The proxy still binds
        # its own downstream tokens to our MCP resource.
        forward_resource=False,
    )
