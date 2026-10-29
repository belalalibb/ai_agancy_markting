"""Application settings (12-factor) loaded from environment / ``.env``.

All secrets are injected via environment variables only — nothing is
hard-coded here (see ``SESSION_RECOVERY.md`` §3 and ``.env.template``).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "test", "staging", "production"]

# Oldest Graph API version still alive (sunset 2027-01-21). Anything older is rejected.
META_GRAPH_MIN_SUPPORTED_VERSION: tuple[int, int] = (21, 0)


def _parse_graph_version(value: str) -> tuple[int, int] | None:
    """``"v26.0"`` → ``(26, 0)``; ``None`` when the string is malformed."""
    if not value.startswith("v"):
        return None
    parts = value[1:].split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1])


class Settings(BaseSettings):
    """Typed, validated runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application --------------------------------------------------------
    app_name: str = "AI Growth OS"
    app_version: str = "0.1.0"
    app_env: AppEnv = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # --- Security -----------------------------------------------------------
    token_encryption_key: SecretStr | None = Field(
        default=None,
        description="Fernet key (urlsafe-base64, 32 bytes) used to encrypt stored access tokens.",
    )

    # CSRF protection for the OAuth ``state`` parameter (HMAC-SHA256 signed, stateless).
    oauth_state_secret: SecretStr | None = Field(
        default=None,
        description="Secret used to sign OAuth `state`; generate with secrets.token_urlsafe(32).",
    )
    oauth_state_ttl_seconds: int = Field(default=600, ge=30, le=3600)

    # --- Database (PostgreSQL 16+ / pgvector — ARCHITECTURE_LOCK D-032) ------
    database_url: str = "postgresql+asyncpg://aigos:aigos_dev_only@localhost:5432/ai_growth_os"
    database_echo: bool = False

    # --- Meta Graph API -----------------------------------------------------
    meta_app_id: str = ""
    meta_app_secret: SecretStr = SecretStr("")
    meta_graph_api_version: str = "v26.0"
    meta_graph_base_url: str = "https://graph.facebook.com"
    meta_oauth_authorize_url: str = "https://www.facebook.com/{version}/dialog/oauth"
    meta_oauth_redirect_uri: str = "http://localhost:8000/api/v1/social/meta/callback"
    # Read-only by default. Publishing scopes are added in a later sprint after App Review.
    meta_oauth_scopes: str = "pages_show_list,pages_read_engagement,instagram_basic"
    meta_http_timeout_seconds: float = 15.0
    meta_http_max_retries: int = 3

    @field_validator("meta_graph_api_version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        value = value.strip()
        parsed = _parse_graph_version(value)
        if parsed is None:
            msg = "meta_graph_api_version must look like 'v26.0'"
            raise ValueError(msg)
        if parsed < META_GRAPH_MIN_SUPPORTED_VERSION:
            minimum = "v{}.{}".format(*META_GRAPH_MIN_SUPPORTED_VERSION)
            msg = (
                f"meta_graph_api_version {value!r} is no longer supported by Meta; "
                f"{minimum} is the oldest live version (sunset 2027-01-21). Use v26.0."
            )
            raise ValueError(msg)
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def meta_scopes_list(self) -> list[str]:
        return [scope.strip() for scope in self.meta_oauth_scopes.split(",") if scope.strip()]

    @property
    def meta_graph_url(self) -> str:
        """Fully-qualified versioned Graph API base, e.g. ``https://graph.facebook.com/v26.0``."""
        return f"{self.meta_graph_base_url.rstrip('/')}/{self.meta_graph_api_version}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance (cleared in tests via ``cache_clear``)."""
    return Settings()
