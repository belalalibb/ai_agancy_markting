"""Application settings (12-factor) loaded from environment / ``.env``.

All secrets are injected via environment variables only — nothing is
hard-coded here (see ``SESSION_RECOVERY.md`` §3 and ``.env.template``).
"""

from __future__ import annotations

import secrets
import warnings
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "test", "staging", "production"]

# Minimum HS256 key length (bytes) — RFC 7518 §3.2 recommends >= hash output size.
AUTH_JWT_SECRET_MIN_BYTES = 32

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

    # --- Auth (SPRINT-04) ---------------------------------------------------
    auth_jwt_secret: SecretStr | None = Field(
        default=None,
        description=(
            "HS256 key for access JWTs (>= 32 bytes). Required outside development; "
            "in development a random per-process key is generated with a warning."
        ),
    )
    auth_access_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    auth_refresh_ttl_seconds: int = Field(default=2_592_000, ge=3600, le=7_776_000)
    auth_cookie_secure: bool | None = Field(
        default=None,
        description="`Secure` flag on the refresh cookie. Default: true except development.",
    )
    auth_allowed_origins: str = Field(
        default="",
        description="Comma-separated browser Origins accepted on cookie-authenticated auth calls.",
    )

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
    # SPRINT-07 B3: publishing scopes requested by default (narrow via the environment).
    # Older connections without them keep working for reads; only ``schedule`` asks for a
    # reconnect (409 ``reauth_required``).
    meta_oauth_scopes: str = (
        "pages_show_list,pages_read_engagement,pages_manage_posts,"
        "instagram_basic,instagram_content_publish"
    )
    meta_http_timeout_seconds: float = 15.0
    meta_http_max_retries: int = 3

    # --- AI gateway (SPRINT-06 Part B-2, D-056) --------------------------------------
    # ``fake`` is the default everywhere until the owner picks a real provider (directive §7);
    # ``openai_compatible`` talks to ``{base_url}/chat/completions`` with a Bearer key that
    # travels **only** in the Authorization header (never in URLs or logs).
    ai_provider: Literal["fake", "openai_compatible"] = "fake"
    ai_provider_base_url: str = "https://api.openai.com/v1"
    ai_provider_api_key: SecretStr | None = Field(
        default=None, description="Bearer key for the OpenAI-compatible endpoint (header only)."
    )
    ai_default_model: str = "gpt-4o-mini"
    ai_http_timeout_seconds: float = 30.0
    # SPRINT-07 A-5: daily token budget per brand (sum of prompt+completion tokens of the
    # ``ai_generated`` revisions in the last 24 h) ⇒ 429 ``generation_budget_exceeded``.
    ai_daily_token_budget_per_brand: int = 200_000

    # --- Scheduler / publishing (SPRINT-07 B3) ------------------------------------------
    # Off by default (tests, dev). The worker loop is started in ``lifespan`` only when on.
    scheduler_enabled: bool = False
    scheduler_poll_seconds: int = 15
    scheduler_lease_seconds: int = 120
    scheduler_batch_size: int = 10
    # Backoff after retryable attempt n (1-based); the last value repeats.
    publish_retry_backoff_seconds: tuple[int, ...] = (60, 300, 900)
    # SPRINT-08 A-3: policy at creation, evidence in the row — written into
    # ``publish_jobs.max_attempts`` by ``content/service.py::_job_for``; the scheduler never
    # reads this setting (``job.max_attempts`` is the truth for an existing job).
    publish_max_attempts: int = Field(default=3, ge=1, le=10)

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

    @model_validator(mode="after")
    def _resolve_auth_defaults(self) -> Settings:
        """A5/A10: enforce the JWT secret outside development; derive cookie `Secure`."""
        if self.auth_jwt_secret is None or self.auth_jwt_secret.get_secret_value() == "":
            if self.app_env != "development":
                msg = (
                    "AUTH_JWT_SECRET is required outside development "
                    f"(app_env={self.app_env!r}); generate with secrets.token_urlsafe(48)."
                )
                raise ValueError(msg)
            warnings.warn(
                "AUTH_JWT_SECRET not set — using a random per-process key (development only); "
                "all access tokens are invalidated on restart.",
                stacklevel=2,
            )
            self.auth_jwt_secret = SecretStr(secrets.token_urlsafe(48))
        elif len(self.auth_jwt_secret.get_secret_value().encode()) < AUTH_JWT_SECRET_MIN_BYTES:
            msg = f"AUTH_JWT_SECRET must be at least {AUTH_JWT_SECRET_MIN_BYTES} bytes"
            raise ValueError(msg)
        if self.auth_cookie_secure is None:
            self.auth_cookie_secure = self.app_env != "development"
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def auth_allowed_origins_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.auth_allowed_origins.split(",") if o.strip()]

    @property
    def auth_refresh_cookie_name(self) -> str:
        """``__Secure-`` prefix wherever the cookie is ``Secure``; plain ``refresh`` in dev.

        SPRINT-04.1 H5: *not* ``__Host-`` — that prefix mandates ``Path=/`` (RFC 6265bis
        §4.1.3.2) and browsers silently drop a ``__Host-`` cookie scoped to
        ``/api/v1/auth``. Keeping the narrow path (the cookie is only ever sent to the auth
        routes) matters more than ``__Host-``'s sub-domain guarantee on a host we control.
        """
        return "__Secure-refresh" if self.auth_cookie_secure else "refresh"

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
