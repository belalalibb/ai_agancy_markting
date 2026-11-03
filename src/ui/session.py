"""UI session cookies + CSRF stamp (SPRINT-12.1 B-1, hardened in 12.1-B C-0).

Three ``HttpOnly; SameSite=Lax; Path=/app`` cookies, all derived from the auth module's own
``/api/v1/auth/login`` response and scoped so the browser never sends them to the JSON API:

* ``app_session`` — the refresh value (``Max-Age=auth_refresh_ttl_seconds``).
* ``app_access``  — the short-lived access JWT (``Max-Age=auth_access_ttl_seconds``). It is
  decoded **locally** with ``AccessTokenCodec`` on every ``/app`` request; ``/auth/refresh`` is
  called only when it is missing/expired (U1-a/U1-b: no per-request rotation, no pressure on
  the shared ``auth:ip`` bucket, no twin-request reuse detection).
* ``app_csrf``    — a random nonce generated **once per login**. The CSRF stamp embedded in
  every form is ``HMAC(key, f"{user_id}:{nonce}")[:32]`` — it changes on every login cycle
  (U2) and can never be replayed across users.

All three are cleared together on logout / denial.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from uuid import UUID

from fastapi import Response
from fastapi.responses import RedirectResponse

from src.core.config import Settings

SESSION_COOKIE = "app_session"
ACCESS_COOKIE = "app_access"
CSRF_COOKIE = "app_csrf"
ALL_COOKIES = (SESSION_COOKIE, ACCESS_COOKIE, CSRF_COOKIE)
CSRF_FIELD = "csrf"
CSRF_HEX_CHARS = 32
NONCE_BYTES = 32
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
    nonce: str
    rotated: bool = False  # True ⇒ the response must re-set ``app_access`` + ``app_session``


@dataclass(frozen=True, slots=True)
class RateLimited:
    """``/auth/refresh`` answered 429 — the session is *kept*; the page shows a retry hint."""

    retry_after: int


def cookie_name(settings: Settings, base: str) -> str:
    return f"__Secure-{base}" if settings.auth_cookie_secure else base


def session_cookie_name(settings: Settings) -> str:
    return cookie_name(settings, SESSION_COOKIE)


def new_nonce() -> str:
    return secrets.token_urlsafe(NONCE_BYTES)


def login_target(next_path: str | None = None) -> str:
    """Login URL; ``next`` is kept only for in-app paths (open-redirect guard)."""
    if next_path and next_path.startswith(f"{APP_PREFIX}/") and next_path != LOGIN_PATH:
        return f"{LOGIN_PATH}?next={next_path}"
    return LOGIN_PATH


def redirect_to_login(next_path: str | None = None) -> RedirectResponse:
    return RedirectResponse(login_target(next_path), status_code=303)


def _set(response: Response, settings: Settings, base: str, value: str, max_age: int) -> None:
    response.set_cookie(
        key=cookie_name(settings, base),
        value=value,
        max_age=max_age,
        expires=0 if max_age == 0 else None,
        path=APP_PREFIX,
        secure=bool(settings.auth_cookie_secure),
        httponly=True,
        samesite="lax",
    )


def set_session(
    response: Response,
    settings: Settings,
    *,
    refresh_value: str,
    access_token: str,
    nonce: str | None = None,
) -> None:
    """Set the (rotated) pair; ``nonce`` only at login — it lives for the whole login cycle."""
    _set(response, settings, SESSION_COOKIE, refresh_value, settings.auth_refresh_ttl_seconds)
    _set(response, settings, ACCESS_COOKIE, access_token, settings.auth_access_ttl_seconds)
    if nonce is not None:
        _set(response, settings, CSRF_COOKIE, nonce, settings.auth_refresh_ttl_seconds)


def clear_session(response: Response, settings: Settings) -> None:
    for base in ALL_COOKIES:
        _set(response, settings, base, "", 0)


def read_session(cookies: dict[str, str], settings: Settings) -> str | None:
    return cookies.get(session_cookie_name(settings)) or None


def read_access(cookies: dict[str, str], settings: Settings) -> str | None:
    return cookies.get(cookie_name(settings, ACCESS_COOKIE)) or None


def read_nonce(cookies: dict[str, str], settings: Settings) -> str | None:
    return cookies.get(cookie_name(settings, CSRF_COOKIE)) or None


def _csrf_key(settings: Settings) -> bytes:
    secret = settings.token_encryption_key or settings.auth_jwt_secret
    assert secret is not None  # auth_jwt_secret is always set by the Settings validator  # noqa: S101
    return secret.get_secret_value().encode()


def csrf_token(settings: Settings, principal: UUID, nonce: str) -> str:
    message = f"{principal}:{nonce}".encode()
    return hmac.new(_csrf_key(settings), message, hashlib.sha256).hexdigest()[:CSRF_HEX_CHARS]


def verify_csrf(settings: Settings, principal: UUID, nonce: str, presented: str | None) -> bool:
    if not presented or not nonce:
        return False
    return hmac.compare_digest(csrf_token(settings, principal, nonce), presented)
