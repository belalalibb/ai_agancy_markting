"""``/app`` routes (SPRINT-12.1 B-2/B-3): login, brand pick, today, inbox.

Every screen is rendered server-side (Jinja2, autoescape on) in Arabic/RTL; all copy
comes from ``i18n/ar.json`` (templates contain no literal Arabic). Data is fetched
through :class:`src.ui.api.ApiClient` — the public JSON API, in-process — so tenancy,
roles, entitlements and WA caps are enforced by the owning modules, never re-implemented.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import jwt
from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.core.config import Settings, get_settings
from src.modules.auth.jwt import ALGORITHM, AUDIENCE, AccessTokenCodec
from src.ui.api import ApiClient, ApiError
from src.ui.session import (
    APP_PREFIX,
    CSRF_FIELD,
    LOGIN_PATH,
    AppUser,
    RateLimited,
    clear_session,
    csrf_token,
    login_target,
    new_nonce,
    read_access,
    read_nonce,
    read_session,
    set_session,
    verify_csrf,
)

router = APIRouter(prefix=APP_PREFIX, include_in_schema=False)

UI_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = UI_DIR / "templates"
STATIC_DIR = UI_DIR / "static"
I18N_PATH = UI_DIR / "i18n" / "ar.json"

WINDOW_SOON = timedelta(hours=4)
TODAY_LIMIT = 50
INBOX_LIMIT = 20
MESSAGES_LIMIT = 50
MAX_REPLY_CHARS = 4096
LEAD_STAGES = ("new", "qualified", "quoted", "won", "lost", "not_lead")  # = LeadStage (D-079)
OBJECTIONS = ("price", "timing", "trust", "competitor", "no_response", "other")  # = ObjectionCode
REFRESH_CACHE_MAX = 256  # C-0.2 (4): in-process twin-request coalescing — no Redis, no table
REFRESH_CACHE_TTL = 30.0
HX_REQUEST = "hx-request"

_MESSAGES: dict[str, str] = json.loads(I18N_PATH.read_text(encoding="utf-8"))


def t(key: str, **kwargs: Any) -> str:
    text = _MESSAGES.get(key, key)
    return text.format(**kwargs) if kwargs else text


_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(default=True, default_for_string=True),
)
_env.globals["t"] = t
templates = Jinja2Templates(env=_env)


# ------------------------------------------------------------------ helpers --
def _error_text(code: str) -> str:
    return _MESSAGES.get(f"error.{code}", _MESSAGES["error.generic"])


def _is_partial(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def _render(
    request: Request, name: str, ctx: Mapping[str, Any], *, status: int = 200
) -> HTMLResponse:
    return templates.TemplateResponse(request, name, dict(ctx), status_code=status)


def _api(
    request: Request,
    settings: Settings,
    token: str | None = None,
    *,
    refresh_value: str | None = None,
) -> ApiClient:
    cookies = {settings.auth_refresh_cookie_name: refresh_value} if refresh_value else None
    host = request.client.host if request.client is not None else "127.0.0.1"
    return ApiClient(
        request.app,
        prefix=settings.api_v1_prefix,
        access_token=token,
        cookies=cookies,
        client_ip=host,
    )


def _codec(settings: Settings) -> AccessTokenCodec:
    assert settings.auth_jwt_secret is not None  # Settings validator guarantees it  # noqa: S101
    return AccessTokenCodec(
        secret=settings.auth_jwt_secret,
        issuer=settings.app_name,
        ttl_seconds=settings.auth_access_ttl_seconds,
    )


class _RefreshGate:
    """Coalesce concurrent refreshes of the *same* refresh value (C-0.2 step 4).

    Two late twins that both carry an expired ``app_access`` would otherwise both call
    ``/auth/refresh`` with the same cookie; the auth module treats the second as reuse and
    revokes **every** session (U1-b). The first caller refreshes under a per-value lock, the
    twin waits and reads the cached result. Bounded (256 entries), swept lazily.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._results: dict[str, tuple[float, AppUser | RateLimited | None]] = {}

    def lock(self, key: str) -> asyncio.Lock:
        self._sweep()
        return self._locks.setdefault(key, asyncio.Lock())

    def cached(self, key: str) -> AppUser | RateLimited | None | object:
        hit = self._results.get(key)
        if hit is None or hit[0] <= time.monotonic():
            return _MISS
        return hit[1]

    def remember(self, key: str, value: AppUser | RateLimited | None) -> None:
        self._results[key] = (time.monotonic() + REFRESH_CACHE_TTL, value)

    def _sweep(self) -> None:
        now = time.monotonic()
        for key in [k for k, (exp, _) in self._results.items() if exp <= now]:
            self._results.pop(key, None)
            self._locks.pop(key, None)
        while len(self._results) > REFRESH_CACHE_MAX:
            oldest = next(iter(self._results))
            self._results.pop(oldest, None)
            self._locks.pop(oldest, None)


_MISS = object()
_gate = _RefreshGate()


def _user_from(
    settings: Settings, payload: Mapping[str, Any], refresh_value: str, nonce: str
) -> AppUser:
    actor = _codec(settings).decode(payload["access_token"])
    return AppUser(
        user_id=actor.user_id,
        tenant_id=actor.tenant_id,
        sid=actor.sid,
        email=(payload.get("user") or {}).get("email"),
        access_token=payload["access_token"],
        refresh_value=refresh_value,
        nonce=nonce,
        rotated=True,
    )


async def _refresh(
    request: Request, settings: Settings, refresh_value: str, nonce: str
) -> AppUser | RateLimited | None:
    """One ``POST /auth/refresh`` — 200 ⇒ rotated user · 429 ⇒ keep cookies · else ⇒ None."""
    api = _api(request, settings, refresh_value=refresh_value)
    try:
        response = await api.request("POST", "/auth/refresh")
    finally:
        await api.aclose()
    if response.status_code == 429:
        try:
            retry = int(response.headers.get("retry-after", "0"))
        except ValueError:
            retry = 0
        return RateLimited(retry_after=max(1, retry))
    if response.status_code != 200:
        return None
    rotated = response.cookies.get(settings.auth_refresh_cookie_name) or refresh_value
    try:
        return _user_from(settings, response.json(), rotated, nonce)
    except jwt.InvalidTokenError:
        return None


async def _resolve_user(request: Request, settings: Settings) -> AppUser | RateLimited | None:
    """C-0.2: decode ``app_access`` locally; call ``/auth/refresh`` only when it is unusable."""
    nonce = read_nonce(request.cookies, settings)
    refresh_value = read_session(request.cookies, settings)
    if nonce is None or refresh_value is None:
        return None
    access = read_access(request.cookies, settings)
    if access:
        try:
            actor = _codec(settings).decode(access)
        except jwt.InvalidTokenError:
            actor = None
        if actor is not None:
            return AppUser(
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
                sid=actor.sid,
                email=None,
                access_token=access,
                refresh_value=refresh_value,
                nonce=nonce,
            )
    key = hashlib.sha256(refresh_value.encode()).hexdigest()
    async with _gate.lock(key):
        hit = _gate.cached(key)
        if hit is not _MISS:
            return hit  # type: ignore[return-value]
        result = await _refresh(request, settings, refresh_value, nonce)
        _gate.remember(key, result)
        return result


def _principal_ignoring_expiry(settings: Settings, access: str | None) -> UUID | None:
    """Logout only: the CSRF stamp is bound to ``user_id``, which must be recoverable from an
    *expired* ``app_access`` too (signature, issuer and audience are still verified)."""
    if not access:
        return None
    assert settings.auth_jwt_secret is not None  # Settings validator guarantees it  # noqa: S101
    try:
        claims = jwt.decode(
            access,
            settings.auth_jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=settings.app_name,
            options={"verify_exp": False, "require": ["sub"]},
        )
        return UUID(str(claims["sub"]))
    except (jwt.InvalidTokenError, ValueError):
        return None


def _with_session(settings: Settings, user: AppUser, response: Response) -> Response:
    if user.rotated:
        set_session(
            response,
            settings,
            refresh_value=user.refresh_value,
            access_token=user.access_token,
        )
    return response


def _denied(settings: Settings, request: Request) -> Response:
    """No usable session: 303 for browsers; ``HX-Redirect`` + empty 200 for htmx (C-0.4 a)."""
    target = login_target(request.url.path)
    if _is_partial(request):
        response: Response = Response(status_code=200, headers={"HX-Redirect": target})
    else:
        response = RedirectResponse(target, status_code=303)
    clear_session(response, settings)
    return response


def _rate_limited(request: Request, limited: RateLimited) -> Response:
    """Refresh was throttled: cookies untouched, translated hint, ``Retry-After`` (C-0.2)."""
    text = _error_text("rate_limited")
    headers = {"Retry-After": str(limited.retry_after)}
    if _is_partial(request):
        return _flash_fragment(text, status=200, extra=headers)
    ctx = {"message": text}
    response = _render(request, "error.html", ctx, status=503)
    response.headers.update(headers)
    return response


def _flash_fragment(text: str, *, status: int, extra: Mapping[str, str] | None = None) -> Response:
    """htmx-only: swap the message into ``#flash`` instead of the form's target (C-0.4 c)."""
    body = templates.env.from_string('<p role="alert" data-flash>{{ text }}</p>').render(text=text)
    headers = {"HX-Retarget": "#flash", "HX-Reswap": "innerHTML", **(extra or {})}
    return HTMLResponse(body, status_code=status, headers=headers)


def _base_ctx(user: AppUser, settings: Settings, brand: Mapping[str, Any] | None = None) -> dict:
    return {
        "user": user,
        "brand": brand,
        "csrf": csrf_token(settings, user.user_id, user.nonce),
        "is_admin": user.user_id in settings.platform_admin_user_ids,
        "app_name": settings.app_name,
    }


def _csrf_ok(settings: Settings, user: AppUser, presented: str | None) -> bool:
    return verify_csrf(settings, user.user_id, user.nonce, presented)


def _forbidden(settings: Settings, request: Request, user: AppUser) -> Response:
    """403: cookies stay (C-0.3); htmx callers get the message retargeted into ``#flash``."""
    text = _error_text("csrf_rejected")
    if _is_partial(request):
        response = _flash_fragment(text, status=403)
    else:
        response = HTMLResponse(text, status_code=403)
    return _with_session(settings, user, response)


class _Gate:
    """Result of the per-request session gate: either a user or a ready response."""

    __slots__ = ("response", "user")

    def __init__(self, user: AppUser | None, response: Response | None) -> None:
        self.user = user
        self.response = response


async def _gate_user(request: Request, settings: Settings, csrf: str | None = ...) -> _Gate:
    """Session (303 / HX-Redirect / 503) then — for POSTs — CSRF (403). ``csrf=...`` skips."""
    resolved = await _resolve_user(request, settings)
    if resolved is None:
        return _Gate(None, _denied(settings, request))
    if isinstance(resolved, RateLimited):
        return _Gate(None, _rate_limited(request, resolved))
    if csrf is not ... and not _csrf_ok(settings, resolved, csrf):
        return _Gate(None, _forbidden(settings, request, resolved))
    return _Gate(resolved, None)


async def _call(
    request: Request, settings: Settings, user: AppUser, fn: Any
) -> tuple[AppUser, Any]:
    """Run ``fn(api)``; on a 401 from the API (revoked elsewhere — H6) refresh **once** and
    retry; a second failure propagates ``ApiError(401)`` ⇒ caller answers ``_denied``."""
    api = _api(request, settings, user.access_token)
    try:
        return user, await fn(api)
    except ApiError as exc:
        if exc.status != 401:
            raise
    finally:
        await api.aclose()
    fresh = await _refresh(request, settings, user.refresh_value, user.nonce)
    if not isinstance(fresh, AppUser):
        raise ApiError(401, "unauthenticated")
    api = _api(request, settings, fresh.access_token)
    try:
        return fresh, await fn(api)
    finally:
        await api.aclose()


def _api_error_page(request: Request, exc: ApiError) -> HTMLResponse:
    status = exc.status if exc.status in (403, 404) else 502
    return _render(request, "error.html", {"message": _error_text(exc.code)}, status=status)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _expires_soon(conversation: Mapping[str, Any], now: datetime) -> bool:
    expires = _parse_dt(conversation.get("window_expires_at"))
    if not conversation.get("window_open") or expires is None:
        return False
    return expires - now <= WINDOW_SOON


# -------------------------------------------------------------------- auth ---
@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str | None = None) -> Response:  # noqa: A002
    return _render(request, "login.html", {"error": None, "next": next or "", "email": ""})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",  # noqa: A002
) -> Response:
    settings = get_settings()
    api = _api(request, settings)
    try:
        response = await api.request(
            "POST", "/auth/login", json={"email": email, "password": password}
        )
    finally:
        await api.aclose()
    if response.status_code != 200:
        code = "invalid_credentials" if response.status_code in (401, 400) else None
        if response.status_code == 429:
            code = "rate_limited"
        payload = response.json() if response.content else {}
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, dict) and isinstance(detail.get("error"), str):
            code = detail["error"]
        ctx = {"error": _error_text(code or "generic"), "next": next, "email": email}
        return _render(request, "login.html", ctx, status=response.status_code)
    refresh_value = response.cookies.get(settings.auth_refresh_cookie_name)
    access_token = (response.json() or {}).get("access_token")
    if not refresh_value or not access_token:
        ctx = {"error": _error_text("generic"), "next": next, "email": email}
        return _render(request, "login.html", ctx, status=502)
    target = next if next.startswith(f"{APP_PREFIX}/") and next != LOGIN_PATH else f"{APP_PREFIX}/"
    redirect = RedirectResponse(target, status_code=303)
    set_session(
        redirect,
        settings,
        refresh_value=refresh_value,
        access_token=access_token,
        nonce=new_nonce(),
    )
    return redirect


@router.post("/logout")
async def logout(
    request: Request, csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None
) -> Response:
    """CSRF-checked like every other ``POST /app/*`` (C-0.3); without a session it is a
    harmless redirect (nothing to revoke, cookies cleared)."""
    settings = get_settings()
    nonce = read_nonce(request.cookies, settings)
    refresh_value = read_session(request.cookies, settings)
    access = read_access(request.cookies, settings)
    if refresh_value and nonce:
        principal = _principal_ignoring_expiry(settings, access)
        if principal is None or not verify_csrf(settings, principal, nonce, csrf):
            return HTMLResponse(_error_text("csrf_rejected"), status_code=403)
        api = _api(request, settings, refresh_value=refresh_value)
        try:
            await api.request("POST", "/auth/logout")
        finally:
            await api.aclose()
    response = RedirectResponse(LOGIN_PATH, status_code=303)
    clear_session(response, settings)
    return response


# -------------------------------------------------------------- brand pick ---
@router.get("/", response_class=HTMLResponse)
async def pick_brand(request: Request) -> Response:
    settings = get_settings()
    gate = await _gate_user(request, settings)
    if gate.response is not None:
        return gate.response
    try:
        user, brands = await _call(request, settings, gate.user, lambda api: api.get("/brands"))
    except ApiError as exc:
        return _api_failure(request, settings, gate.user, exc)
    if len(brands) == 1:
        target = f"{APP_PREFIX}/b/{brands[0]['id']}/today"
        return _with_session(settings, user, RedirectResponse(target, status_code=303))
    ctx = {**_base_ctx(user, settings), "brands": brands}
    return _with_session(settings, user, _render(request, "brands.html", ctx))


def _api_failure(request: Request, settings: Settings, user: AppUser, exc: ApiError) -> Response:
    """401 after the one retry ⇒ session is gone elsewhere (H6) ⇒ denied; else error page."""
    if exc.status == 401:
        return _denied(settings, request)
    return _with_session(settings, user, _api_error_page(request, exc))


async def _brand_frame(api: ApiClient, brand_id: UUID) -> dict[str, Any]:
    brand = await api.get(f"/brands/{brand_id}")
    plan = await api.get(f"/brands/{brand_id}/entitlement")
    return {"brand": brand, "plan": plan}


# ------------------------------------------------------------------- today ---
async def _today_ctx(api: ApiClient, brand_id: UUID) -> dict[str, Any]:
    now = datetime.now(UTC)
    frame = await _brand_frame(api, brand_id)
    alerts = await api.get(f"/brands/{brand_id}/pulse/alerts", open="true", limit=TODAY_LIMIT)
    conversations = await api.get(f"/brands/{brand_id}/whatsapp/conversations", limit=INBOX_LIMIT)
    pending = await api.get(
        f"/brands/{brand_id}/content/items", status="pending_approval", limit=TODAY_LIMIT
    )
    return {
        **frame,
        "alerts": alerts["items"],
        "open_count": alerts["open_count"],
        "closing": [c for c in conversations["items"] if _expires_soon(c, now)],
        "pending": pending["items"],
        "now": now,
    }


async def _today_page(
    request: Request, settings: Settings, user: AppUser, brand_id: UUID, flash: str | None = None
) -> Response:
    try:
        user, data = await _call(request, settings, user, lambda api: _today_ctx(api, brand_id))
    except ApiError as exc:
        return _api_failure(request, settings, user, exc)
    ctx = {**_base_ctx(user, settings, data["brand"]), **data, "flash": flash}
    name = "partials/today_cards.html" if _is_partial(request) else "today.html"
    return _with_session(settings, user, _render(request, name, ctx))


@router.get("/b/{brand_id}/today", response_class=HTMLResponse)
async def today(request: Request, brand_id: UUID) -> Response:
    settings = get_settings()
    gate = await _gate_user(request, settings)
    if gate.response is not None:
        return gate.response
    return await _today_page(request, settings, gate.user, brand_id)


async def _action(
    request: Request, csrf: str | None, method: str, path: str, body: Any = None
) -> tuple[_Gate, str | None, Any]:
    """Gate (session + CSRF) then perform one API write; returns the flash (if any) + result."""
    settings = get_settings()
    gate = await _gate_user(request, settings, csrf)
    if gate.response is not None:
        return gate, None, None
    try:
        user, result = await _call(
            request, settings, gate.user, lambda api: api.json(method, path, json=body)
        )
    except ApiError as exc:
        if exc.status == 401:
            return _Gate(None, _denied(settings, request)), None, None
        return gate, _error_text(exc.code), None
    return _Gate(user, None), None, result


async def _today_action(
    request: Request, brand_id: UUID, csrf: str | None, method: str, path: str, body: Any = None
) -> Response:
    gate, flash, _ = await _action(request, csrf, method, path, body)
    if gate.response is not None:
        return gate.response
    return await _today_page(request, get_settings(), gate.user, brand_id, flash)


@router.post("/b/{brand_id}/today/alerts/{alert_id}/ack", response_class=HTMLResponse)
async def ack_alert(
    request: Request,
    brand_id: UUID,
    alert_id: UUID,
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    path = f"/brands/{brand_id}/pulse/alerts/{alert_id}/ack"
    return await _today_action(request, brand_id, csrf, "POST", path)


@router.post("/b/{brand_id}/today/items/{item_id}/approve", response_class=HTMLResponse)
async def approve_item(
    request: Request,
    brand_id: UUID,
    item_id: UUID,
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    path = f"/brands/{brand_id}/content/items/{item_id}/approve"
    return await _today_action(request, brand_id, csrf, "POST", path, {})


# ------------------------------------------------------------------- inbox ---
async def _inbox_ctx(api: ApiClient, brand_id: UUID, conversation_id: UUID | None) -> dict:
    frame = await _brand_frame(api, brand_id)
    conversations = await api.get(f"/brands/{brand_id}/whatsapp/conversations", limit=INBOX_LIMIT)
    thread = None
    current = None
    if conversation_id is not None:
        thread = await api.get(
            f"/brands/{brand_id}/whatsapp/conversations/{conversation_id}/messages",
            limit=MESSAGES_LIMIT,
        )
        current = next(
            (c for c in conversations["items"] if c["id"] == str(conversation_id)), None
        )
    return {
        **frame,
        "conversations": conversations["items"],
        "thread": thread,
        "current": current,
        "conversation_id": conversation_id,
        "lead_stages": LEAD_STAGES,
        "objections": OBJECTIONS,
        "now": datetime.now(UTC),
    }


async def _inbox_page(
    request: Request,
    settings: Settings,
    user: AppUser,
    brand_id: UUID,
    conversation_id: UUID | None,
    *,
    flash: str | None = None,
    draft: str = "",
) -> Response:
    try:
        user, data = await _call(
            request, settings, user, lambda api: _inbox_ctx(api, brand_id, conversation_id)
        )
    except ApiError as exc:
        return _api_failure(request, settings, user, exc)
    ctx = {**_base_ctx(user, settings, data["brand"]), **data, "flash": flash, "draft": draft}
    name = "partials/thread.html" if _is_partial(request) and conversation_id else "inbox.html"
    return _with_session(settings, user, _render(request, name, ctx))


@router.get("/b/{brand_id}/inbox", response_class=HTMLResponse)
async def inbox(request: Request, brand_id: UUID, conversation: UUID | None = None) -> Response:
    settings = get_settings()
    gate = await _gate_user(request, settings)
    if gate.response is not None:
        return gate.response
    return await _inbox_page(request, settings, gate.user, brand_id, conversation)


async def _inbox_action(
    request: Request,
    brand_id: UUID,
    conversation_id: UUID,
    csrf: str | None,
    method: str,
    path: str,
    body: Any,
    *,
    prefill: str | None = None,
) -> Response:
    gate, flash, result = await _action(request, csrf, method, path, body)
    if gate.response is not None:
        return gate.response
    draft = str(result.get(prefill) or "") if prefill and isinstance(result, dict) else ""
    return await _inbox_page(
        request, get_settings(), gate.user, brand_id, conversation_id, flash=flash, draft=draft
    )


@router.post("/b/{brand_id}/inbox/{conversation_id}/reply", response_class=HTMLResponse)
async def reply(
    request: Request,
    brand_id: UUID,
    conversation_id: UUID,
    body: Annotated[str, Form(max_length=MAX_REPLY_CHARS)] = "",
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    """Form fields are optional on purpose: the session check (303) and the CSRF check
    (403) run before any input validation; an empty body is a 422 from the API ⇒
    translated flash — anonymous callers never see a schema error."""
    path = f"/brands/{brand_id}/whatsapp/conversations/{conversation_id}/messages"
    return await _inbox_action(
        request, brand_id, conversation_id, csrf, "POST", path, {"body": body}
    )


@router.post("/b/{brand_id}/inbox/{conversation_id}/suggest", response_class=HTMLResponse)
async def suggest(
    request: Request,
    brand_id: UUID,
    conversation_id: UUID,
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    path = f"/brands/{brand_id}/whatsapp/conversations/{conversation_id}/suggest-reply"
    return await _inbox_action(
        request, brand_id, conversation_id, csrf, "POST", path, {}, prefill="suggested_body"
    )


@router.post("/b/{brand_id}/inbox/{conversation_id}/stage", response_class=HTMLResponse)
async def set_stage(
    request: Request,
    brand_id: UUID,
    conversation_id: UUID,
    lead_stage: Annotated[str, Form()] = "",
    objection_code: Annotated[str, Form()] = "",
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    path = f"/brands/{brand_id}/whatsapp/conversations/{conversation_id}/lead-stage"
    body: dict[str, Any] = {"lead_stage": lead_stage}
    if objection_code:
        body["objection_code"] = objection_code
    return await _inbox_action(request, brand_id, conversation_id, csrf, "PATCH", path, body)


# ------------------------------------------------------------------ static ---
VENDOR_CACHE_CONTROL = "public, max-age=86400, immutable"
VENDOR_PREFIX = f"{APP_PREFIX}/static/vendor/"


class VendorCacheControl:
    """Pure-ASGI wrapper (C-0.6): ``Cache-Control`` on ``/app/static/vendor/*`` 200s only."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(VENDOR_PREFIX):
            await self.app(scope, receive, send)
            return

        async def send_tagged(message: Any) -> None:
            if message["type"] == "http.response.start" and message["status"] == 200:
                headers = [(k, v) for k, v in message["headers"] if k.lower() != b"cache-control"]
                headers.append((b"cache-control", VENDOR_CACHE_CONTROL.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_tagged)
