"""UI session cookie + CSRF stamp (SPRINT-12.1 B-1).

The UI does not mint its own credentials: ``app_session`` carries the *same* refresh
value the auth module issued (``/api/v1/auth/login``), scoped to ``Path=/app`` so the
browser never sends it to the JSON API. Every ``/app/*`` request exchanges it for a
short-lived access token via ``POST /auth/refresh`` (rotation included), which keeps
reuse detection and revocation exactly as the auth module implements them.

CSRF: ``HMAC(token_encryption_key, principal)`` (hex, 32 chars) is embedded in every
POST form and compared in constant time. The directive names the *session id* as the
HMAC input, but the real auth module rotates ``sid`` on **every** ``/auth/refresh`` (reuse
detection, SPRINT-04 I-A2) — i.e. on every ``/app`` request — so a sid-bound stamp could
never match the request that presents it. The stamp is therefore bound to the stable
``user_id`` of the session (§5-5: real code is the reference; recorded in §6).
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from uuid import UUID

from fastapi import Response
from fastapi.responses import RedirectResponse

from src.core.config import Settings

SESSION_COOKIE = "app_session"
CSRF_FIELD = "csrf"
CSRF_HEX_CHARS = 32
APP_PREFIX = "/app"
LOGIN_PATH = f"{APP_PREFIX}/login"


@dataclass(frozen=True, slots=True)
class AppUser:
    user_id: UUID
    tenant_id: UUID
    sid: UUID
    email: str | None
    access_token: str
    refresh_value: str


def session_cookie_name(settings: Settings) -> str:
    return f"__Secure-{SESSION_COOKIE}" if settings.auth_cookie_secure else SESSION_COOKIE


def redirect_to_login(next_path: str | None = None) -> RedirectResponse:
    """303 to the login screen; ``next`` is kept only for in-app paths (open-redirect guard)."""
    target = LOGIN_PATH
    if next_path and next_path.startswith(f"{APP_PREFIX}/") and next_path != LOGIN_PATH:
        target = f"{LOGIN_PATH}?next={next_path}"
    return RedirectResponse(target, status_code=303)


def set_session(response: Response, settings: Settings, refresh_value: str) -> None:
    response.set_cookie(
        key=session_cookie_name(settings),
        value=refresh_value,
        max_age=settings.auth_refresh_ttl_seconds,
        path=APP_PREFIX,
        secure=bool(settings.auth_cookie_secure),
        httponly=True,
        samesite="lax",
    )


def clear_session(response: Response, settings: Settings) -> None:
    response.set_cookie(
        key=session_cookie_name(settings),
        value="",
        max_age=0,
        expires=0,
        path=APP_PREFIX,
        secure=bool(settings.auth_cookie_secure),
        httponly=True,
        samesite="lax",
    )


def read_session(cookies: dict[str, str], settings: Settings) -> str | None:
    value = cookies.get(session_cookie_name(settings))
    return value or None


def _csrf_key(settings: Settings) -> bytes:
    secret = settings.token_encryption_key or settings.auth_jwt_secret
    assert secret is not None  # auth_jwt_secret is always set by the Settings validator  # noqa: S101
    return secret.get_secret_value().encode()


def csrf_token(settings: Settings, principal: UUID) -> str:
    digest = hmac.new(_csrf_key(settings), str(principal).encode(), hashlib.sha256)
    return digest.hexdigest()[:CSRF_HEX_CHARS]


def verify_csrf(settings: Settings, principal: UUID, presented: str | None) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(csrf_token(settings, principal), presented)
