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

    # --- Database (PostgreSQL 16+ / pgvector — ARCHITECTURE_LOCK D-032) ------
    database_url: str = "postgresql+psycopg://user:password@localhost:5432/ai_growth_os"
    database_echo: bool = False

    # --- Meta Graph API -----------------------------------------------------
    meta_app_id: str = ""
    meta_app_secret: SecretStr = SecretStr("")
    meta_graph_api_version: str = "v19.0"
    meta_graph_base_url: str = "https://graph.facebook.com"
    meta_oauth_authorize_url: str = "https://www.facebook.com/{version}/dialog/oauth"
    meta_oauth_redirect_uri: str = "http://localhost:8000/api/v1/social/meta/callback"
    meta_oauth_scopes: str = (
        "pages_show_list,pages_read_engagement,pages_manage_posts,"
        "instagram_basic,instagram_content_publish,business_management"
    )
    meta_http_timeout_seconds: float = 15.0
    meta_http_max_retries: int = 3

    @field_validator("meta_graph_api_version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("v") or not value[1:].replace(".", "").isdigit():
            msg = "meta_graph_api_version must look like 'v19.0'"
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
        """Fully-qualified versioned Graph API base, e.g. ``https://graph.facebook.com/v19.0``."""
        return f"{self.meta_graph_base_url.rstrip('/')}/{self.meta_graph_api_version}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance (cleared in tests via ``cache_clear``)."""
    return Settings()
