"""``/app`` routes (SPRINT-12.1 B/C): login, brand pick, today, inbox, content,
diagnosis, settings.

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
        "umami_website_id": settings.umami_website_id,
        "umami_script_url": settings.umami_script_url,
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
    playbooks = await api.get("/playbooks")
    # SPRINT-13 B-3: the stored ``brand_profiles.sector`` drives the card (``?sector=`` on the
    # page stays an override when nothing is stored).
    profile = await _optional(api, f"/brands/{brand_id}/brain/profile")
    sector = ((profile or {}).get("sector") or "").strip()
    chosen = next((p for p in playbooks if p["sector"] == sector), None)
    return {
        **frame,
        "playbooks": playbooks,
        "playbook": chosen,
        "alerts": alerts["items"],
        "open_count": alerts["open_count"],
        "closing": [c for c in conversations["items"] if _expires_soon(c, now)],
        "pending": pending["items"],
        "now": now,
    }


async def _today_page(
    request: Request,
    settings: Settings,
    user: AppUser,
    brand_id: UUID,
    flash: str | None = None,
    *,
    sector: str = "",
) -> Response:
    try:
        user, data = await _call(request, settings, user, lambda api: _today_ctx(api, brand_id))
    except ApiError as exc:
        return _api_failure(request, settings, user, exc)
    if data["playbook"] is None and sector:  # D-4 / B-3: ``?sector=`` override when unset
        data["playbook"] = next((p for p in data["playbooks"] if p["sector"] == sector), None)
    ctx = {**_base_ctx(user, settings, data["brand"]), **data, "flash": flash}
    name = "partials/today_cards.html" if _is_partial(request) else "today.html"
    return _with_session(settings, user, _render(request, name, ctx))


@router.get("/b/{brand_id}/today", response_class=HTMLResponse)
async def today(request: Request, brand_id: UUID, sector: str = "") -> Response:
    settings = get_settings()
    gate = await _gate_user(request, settings)
    if gate.response is not None:
        return gate.response
    return await _today_page(request, settings, gate.user, brand_id, sector=sector)


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


# ----------------------------------------------------------------- content ---
CONTENT_TABS = (
    "draft",
    "pending_approval",
    "approved",
    "scheduled",
    "published",
    "publish_failed",
)
CONTENT_LIMIT = 50
BATCH_MAX = 50
ITEM_ACTIONS = ("submit", "withdraw", "approve", "unschedule", "archive")  # optional-note posts
GRADES = ("measured", "inferred", "estimated")  # = share.EVIDENCE_GRADES (D-074 order)
CONSENTS = ("signals_only", "none", "full")  # = tenancy LearningConsent
GRANT_ROLES = ("manager", "viewer")  # = GrantRole


def _grade_of(summary: Mapping[str, Any]) -> str:
    """Strongest cited grade — mirrors ``brand_brain.share.evidence_grade`` over the public
    ``evidence_kinds_summary`` (the list API has no ``evidence_grade`` field; §6)."""
    for grade in GRADES:
        if int(summary.get(grade) or 0) > 0:
            return grade
    return GRADES[-1]


def _translate_batch(results: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "item_id": str(r["item_id"]),
            "status": t(f"content.batch_{r['status']}"),
            "error": _error_text(r["error"]) if r.get("error") else "",
        }
        for r in results
    ]


def _tab(status: str) -> str | None:
    return status if status in CONTENT_TABS else None


def _not_found_page(request: Request) -> Response:
    return _render(request, "error.html", {"message": _error_text("not_found")}, status=404)


async def _gated_action(
    request: Request, csrf: str | None, spec: tuple[str, str, Any] | None
) -> tuple[_Gate, str | None, Any]:
    """Session + CSRF first — an anonymous probe never learns whether an action exists
    (303/HX-Redirect as for every ``/app`` route); only then an unknown action is a 404."""
    if spec is not None:
        return await _action(request, csrf, *spec)
    gate = await _gate_user(request, get_settings(), csrf)
    if gate.response is None:
        gate = _Gate(None, _not_found_page(request))
    return gate, None, None


async def _content_ctx(api: ApiClient, brand_id: UUID, status: str | None) -> dict[str, Any]:
    frame = await _brand_frame(api, brand_id)
    params: dict[str, Any] = {"limit": CONTENT_LIMIT}
    if status:
        params["status"] = status
    page = await api.get(f"/brands/{brand_id}/content/items", **params)
    return {**frame, "items": page["items"], "status": status or "", "tabs": CONTENT_TABS}


async def _content_page(
    request: Request,
    settings: Settings,
    user: AppUser,
    brand_id: UUID,
    status: str | None,
    *,
    flash: str | None = None,
    batch: list[dict[str, str]] | None = None,
) -> Response:
    try:
        user, data = await _call(
            request, settings, user, lambda api: _content_ctx(api, brand_id, status)
        )
    except ApiError as exc:
        return _api_failure(request, settings, user, exc)
    ctx = {**_base_ctx(user, settings, data["brand"]), **data, "flash": flash, "batch": batch}
    name = "partials/content_list.html" if _is_partial(request) else "content.html"
    return _with_session(settings, user, _render(request, name, ctx))


@router.get("/b/{brand_id}/content", response_class=HTMLResponse)
async def content(request: Request, brand_id: UUID, status: str = "") -> Response:
    settings = get_settings()
    gate = await _gate_user(request, settings)
    if gate.response is not None:
        return gate.response
    return await _content_page(request, settings, gate.user, brand_id, _tab(status))


@router.post("/b/{brand_id}/content/batch-approve", response_class=HTMLResponse)
async def content_batch_approve(
    request: Request,
    brand_id: UUID,
    item_ids: Annotated[list[str] | None, Form()] = None,
    status: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    """«Approve selected» ⇒ one ``approvals/batch`` call; every line comes back translated
    (``approved`` / ``skipped: <reason>``) — D-055: a human ticked every box."""
    body: dict[str, Any] = {"item_ids": (item_ids or [])[:BATCH_MAX]}
    if note:
        body["note"] = note
    path = f"/brands/{brand_id}/content/approvals/batch"
    gate, flash, result = await _action(request, csrf, "POST", path, body)
    if gate.response is not None:
        return gate.response
    batch = _translate_batch(result["results"]) if isinstance(result, dict) else None
    return await _content_page(
        request, get_settings(), gate.user, brand_id, _tab(status), flash=flash, batch=batch
    )


@router.post("/b/{brand_id}/content/{item_id}/{action}", response_class=HTMLResponse)
async def content_item_action(  # noqa: PLR0913 - one form field per lifecycle input
    request: Request,
    brand_id: UUID,
    item_id: UUID,
    action: str,
    status: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
    scheduled_for: Annotated[str, Form()] = "",
    target_page_id: Annotated[str, Form()] = "",
    brief: Annotated[str, Form()] = "",
    tone: Annotated[str, Form()] = "professional",
    language: Annotated[str, Form()] = "ar",
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    """One form per lifecycle action; the buttons mirror the API state machine (D-055 — the
    UI never publishes, ``retry`` re-queues a failed job, ``approve`` stays OWNER-only
    upstream). Unknown actions are a 404 page and never reach the API."""
    base = f"/brands/{brand_id}/content/items/{item_id}"
    spec: tuple[str, str, Any] | None = None
    if action in ITEM_ACTIONS:
        spec = ("POST", f"{base}/{action}", {"note": note} if note else {})
    elif action == "reject":
        spec = ("POST", f"{base}/reject", {"note": note})
    elif action == "schedule":
        body: dict[str, Any] = {"scheduled_for": scheduled_for}
        if target_page_id:
            body["target_page_id"] = target_page_id
        spec = ("POST", f"{base}/schedule", body)
    elif action == "retry":
        spec = ("POST", f"{base}/publish/retry", None)
    elif action == "generate":
        spec = ("POST", f"{base}/generate", {"brief": brief, "tone": tone, "language": language})
    gate, flash, _ = await _gated_action(request, csrf, spec)
    if gate.response is not None:
        return gate.response
    return await _content_page(
        request,
        get_settings(),
        gate.user,
        brand_id,
        _tab(status),
        flash=flash or t("content.done"),
    )


# --------------------------------------------------------------- diagnosis ---
async def _diagnosis_ctx(api: ApiClient, brand_id: UUID) -> dict[str, Any]:
    frame = await _brand_frame(api, brand_id)
    page = await api.get(f"/brands/{brand_id}/brain/recommendations", limit=CONTENT_LIMIT)
    cost = None
    if frame["brand"].get("role") == "owner":  # GET /brain/cost is OWNER-only upstream
        cost = await api.get(f"/brands/{brand_id}/brain/cost")
    items = [
        {**r, "grade": _grade_of(r.get("evidence_kinds_summary") or {})} for r in page["items"]
    ]
    today = datetime.now(UTC).date().isoformat()
    today_cost = next((d for d in (cost or {}).get("items", []) if d["day"] == today), None)
    return {**frame, "recommendations": items, "cost": cost, "today_cost": today_cost}


async def _diagnosis_page(
    request: Request,
    settings: Settings,
    user: AppUser,
    brand_id: UUID,
    *,
    flash: str | None = None,
    share: Mapping[str, Any] | None = None,
) -> Response:
    try:
        user, data = await _call(
            request, settings, user, lambda api: _diagnosis_ctx(api, brand_id)
        )
    except ApiError as exc:
        return _api_failure(request, settings, user, exc)
    ctx = {**_base_ctx(user, settings, data["brand"]), **data, "flash": flash, "share": share}
    name = "partials/recommendations.html" if _is_partial(request) else "diagnosis.html"
    return _with_session(settings, user, _render(request, name, ctx))


@router.get("/b/{brand_id}/diagnosis", response_class=HTMLResponse)
async def diagnosis(request: Request, brand_id: UUID) -> Response:
    settings = get_settings()
    gate = await _gate_user(request, settings)
    if gate.response is not None:
        return gate.response
    return await _diagnosis_page(request, settings, gate.user, brand_id)


@router.post("/b/{brand_id}/diagnosis/diagnose", response_class=HTMLResponse)
async def diagnose(
    request: Request, brand_id: UUID, csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None
) -> Response:
    path = f"/brands/{brand_id}/brain/diagnose"
    gate, flash, result = await _action(request, csrf, "POST", path, None)
    if gate.response is not None:
        return gate.response
    if flash is None and isinstance(result, dict):
        flash = t("diagnosis.done", n=len(result.get("recommendations", [])))
    return await _diagnosis_page(request, get_settings(), gate.user, brand_id, flash=flash)


@router.post("/b/{brand_id}/diagnosis/{rec_id}/{action}", response_class=HTMLResponse)
async def recommendation_action(
    request: Request,
    brand_id: UUID,
    rec_id: UUID,
    action: str,
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    """``accept``/``dismiss`` ⇒ recommendations API; ``share``/``unshare`` ⇒ the public link
    (C-2): the URL is rendered **once** and never stored by the UI; 409 ⇒ translated flash."""
    brain = f"/brands/{brand_id}/brain"
    spec: tuple[str, str, Any] | None = None
    if action in ("accept", "dismiss"):
        spec = ("POST", f"{brain}/recommendations/{rec_id}/{action}", None)
    elif action in ("share", "unshare"):
        method = "POST" if action == "share" else "DELETE"
        spec = (method, f"{brain}/diagnoses/{rec_id}/share", None)
    gate, flash, result = await _gated_action(request, csrf, spec)
    if gate.response is not None:
        return gate.response
    share = None
    if flash is None:
        if action == "share" and isinstance(result, dict):
            share = {"rec_id": str(rec_id), "url": result["url"]}
        flash = t(f"diagnosis.{action}_done")
    return await _diagnosis_page(
        request, get_settings(), gate.user, brand_id, flash=flash, share=share
    )


# ---------------------------------------------------------------- settings ---
async def _optional(api: ApiClient, path: str) -> Any:
    """``None`` instead of an error page for sections a brand may legitimately lack (no
    WhatsApp number yet ⇒ 404; VIEWER ⇒ 403) — anything else still raises."""
    try:
        return await api.get(path)
    except ApiError as exc:
        if exc.status in (403, 404):
            return None
        raise


async def _settings_ctx(api: ApiClient, brand_id: UUID) -> dict[str, Any]:
    frame = await _brand_frame(api, brand_id)
    owner = frame["brand"].get("role") == "owner"
    connections = await api.get(f"/brands/{brand_id}/social/meta/connections")
    wa = await _optional(api, f"/brands/{brand_id}/whatsapp/account")
    grants = await api.get(f"/brands/{brand_id}/grants") if owner else []
    return {
        **frame,
        "is_owner": owner,
        "connections": connections,
        "wa": wa,
        "grants": [g for g in grants if not g.get("revoked_at")],
        "consents": CONSENTS,
        "grant_roles": GRANT_ROLES,
    }


async def _settings_page(
    request: Request, settings: Settings, user: AppUser, brand_id: UUID, flash: str | None = None
) -> Response:
    try:
        user, data = await _call(request, settings, user, lambda api: _settings_ctx(api, brand_id))
    except ApiError as exc:
        return _api_failure(request, settings, user, exc)
    ctx = {**_base_ctx(user, settings, data["brand"]), **data, "flash": flash}
    return _with_session(settings, user, _render(request, "settings.html", ctx))


@router.get("/b/{brand_id}/settings", response_class=HTMLResponse)
async def brand_settings(request: Request, brand_id: UUID) -> Response:
    settings = get_settings()
    gate = await _gate_user(request, settings)
    if gate.response is not None:
        return gate.response
    return await _settings_page(request, settings, gate.user, brand_id)


async def _settings_action(
    request: Request, brand_id: UUID, csrf: str | None, method: str, path: str, body: Any, ok: str
) -> Response:
    gate, flash, _ = await _action(request, csrf, method, path, body)
    if gate.response is not None:
        return gate.response
    return await _settings_page(request, get_settings(), gate.user, brand_id, flash or t(ok))


@router.post("/b/{brand_id}/settings/connections/start")
async def connection_start(
    request: Request, brand_id: UUID, csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None
) -> Response:
    """The Facebook Login URL is answered as a 303 (the form targets a new window) — the
    UI never stores the OAuth ``state``."""
    path = f"/brands/{brand_id}/social/meta/connections/start"
    gate, flash, result = await _action(request, csrf, "POST", path, None)
    if gate.response is not None:
        return gate.response
    if flash is None and isinstance(result, dict) and result.get("authorization_url"):
        response = RedirectResponse(result["authorization_url"], status_code=303)
        return _with_session(get_settings(), gate.user, response)
    return await _settings_page(request, get_settings(), gate.user, brand_id, flash)


@router.post("/b/{brand_id}/settings/connections/{connection_id}/resync")
async def connection_resync(
    request: Request,
    brand_id: UUID,
    connection_id: UUID,
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    path = f"/brands/{brand_id}/social/meta/connections/{connection_id}/resync"
    return await _settings_action(request, brand_id, csrf, "POST", path, None, "settings.resynced")


@router.post("/b/{brand_id}/settings/consent", response_class=HTMLResponse)
async def set_consent(
    request: Request,
    brand_id: UUID,
    learning_consent: Annotated[str, Form()] = "",
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    path = f"/brands/{brand_id}/learning-consent"  # the API is PATCH (no PUT) — §6
    body = {"learning_consent": learning_consent}
    return await _settings_action(request, brand_id, csrf, "PATCH", path, body, "settings.saved")


@router.post("/b/{brand_id}/settings/grants", response_class=HTMLResponse)
async def add_grant(
    request: Request,
    brand_id: UUID,
    grantee_tenant_id: Annotated[str, Form()] = "",
    role: Annotated[str, Form()] = "",
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    """Grants are keyed by *tenant id* in the API (there is no e-mail lookup — §6)."""
    body = {"grantee_tenant_id": grantee_tenant_id, "role": role}
    path = f"/brands/{brand_id}/grants"
    return await _settings_action(request, brand_id, csrf, "POST", path, body, "settings.granted")


@router.post("/b/{brand_id}/settings/grants/{grant_id}/revoke", response_class=HTMLResponse)
async def revoke_grant(
    request: Request,
    brand_id: UUID,
    grant_id: UUID,
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    path = f"/brands/{brand_id}/grants/{grant_id}"
    return await _settings_action(
        request, brand_id, csrf, "DELETE", path, None, "settings.revoked"
    )


# ------------------------------------------------------------------- admin ---
ADMIN_TABS = ("providers", "models", "routing", "probe", "audit", "dead_letter")  # SPRINT-13
FLAG_KEYS = ("platform_readonly", "feature_pulse", "feature_lead_loop")  # = ops.domain.FLAG_KEYS
PURPOSES = (
    "content_generation",
    "whatsapp_suggest_reply",
    "brand_diagnosis",
    "content_plan",
    "audio_transcription",
)  # = catalog.domain.PURPOSES
PROVIDER_KINDS = ("openai_compatible", "fake")  # = ProviderKind
CAPABILITIES = ("text", "audio")  # = Capability
PLANS = ("trial", "starter", "growth", "agency")  # = entitlements Plan
PROVIDER_KEY_FIELD = "credential"  # neutral form field; mapped to the API body in the router


def _is_admin(settings: Settings, user: AppUser) -> bool:
    return user.user_id in settings.platform_admin_user_ids


async def _gate_admin(request: Request, settings: Settings, csrf: str | None = ...) -> _Gate:
    """D-0: the session gate first, then a **local** admin check **before any API call** —
    non-admins get a 404 page (never 403: the panel's existence is not disclosed)."""
    gate = await _gate_user(request, settings, csrf)
    if gate.response is None and not _is_admin(settings, gate.user):
        return _Gate(None, _with_session(settings, gate.user, _not_found_page(request)))
    return gate


def _provider_body(form: Mapping[str, str]) -> dict[str, Any]:
    """Form ⇒ ``ProviderCreate``/``ProviderPatch``; the secret travels under its API name
    only here, is **sent** and never echoed (the API answers ``has_credential``)."""
    body: dict[str, Any] = {}
    for key in ("slug", "kind", "base_url", "credential_ref"):
        if form.get(key):
            body[key] = form[key]
    if form.get("timeout_seconds"):
        body["timeout_seconds"] = form["timeout_seconds"]
    if form.get(PROVIDER_KEY_FIELD):
        body["api_" + "key"] = form[PROVIDER_KEY_FIELD]
    if "enabled" in form:
        body["enabled"] = form["enabled"] == "true"
    return body


def _model_body(form: Mapping[str, str]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for key in ("provider_id", "model_name", "capability"):
        if form.get(key):
            body[key] = form[key]
    keys = ("prompt_per_1k", "completion_per_1k", "cost_per_minute_usd", "max_audio_seconds")
    body["pricing"] = {k: form[k].strip() for k in keys if form.get(k, "").strip()}
    if form.get("max_tokens_cap"):
        body["max_tokens_cap"] = int(form["max_tokens_cap"])
    if "enabled" in form:
        body["enabled"] = form["enabled"] == "true"
    return body


async def _admin_ai_ctx(api: ApiClient, tab: str) -> dict[str, Any]:
    ctx: dict[str, Any] = {"tab": tab, "tabs": ADMIN_TABS, "purposes": PURPOSES}
    ctx["providers"] = await api.get("/admin/ai/providers")
    ctx["models"] = await api.get("/admin/ai/models")
    counts: dict[str, int] = {}
    for m in ctx["models"]:
        counts[str(m["provider_id"])] = counts.get(str(m["provider_id"]), 0) + 1
    ctx["model_counts"] = counts
    ctx["provider_slugs"] = {str(p["id"]): p["slug"] for p in ctx["providers"]}
    if tab == "routing":
        routes = await api.get("/admin/ai/routing")
        chains: dict[str, list[dict[str, Any]]] = {p: [] for p in PURPOSES}
        for r in sorted(routes, key=lambda r: (r["purpose"], r["priority"])):
            chains.setdefault(r["purpose"], []).append(r)
        ctx["chains"] = chains
    elif tab == "audit":  # SPRINT-13 A-1/A-2: last 50 admin acts (read-only) + kill switches
        ctx["actions"] = await api.get("/admin/actions", limit=50)
        ctx["flags"] = await api.get("/admin/flags")
    elif tab == "dead_letter":  # SPRINT-13 A-3
        ctx["jobs"] = await api.get("/admin/publishing/dead-letter", limit=50)
    ctx["kinds"], ctx["capabilities"] = PROVIDER_KINDS, CAPABILITIES
    return ctx


async def _admin_ai_page(
    request: Request,
    settings: Settings,
    user: AppUser,
    tab: str,
    *,
    flash: str | None = None,
    probe: Mapping[str, Any] | None = None,
) -> Response:
    tab = tab if tab in ADMIN_TABS else ADMIN_TABS[0]
    try:
        user, data = await _call(request, settings, user, lambda api: _admin_ai_ctx(api, tab))
    except ApiError as exc:
        return _api_failure(request, settings, user, exc)
    ctx = {**_base_ctx(user, settings), **data, "flash": flash, "probe": probe}
    name = "partials/admin_ai_tab.html" if _is_partial(request) else "admin_ai.html"
    return _with_session(settings, user, _render(request, name, ctx))


@router.get("/admin/ai", response_class=HTMLResponse)
async def admin_ai(request: Request, tab: str = "providers") -> Response:
    settings = get_settings()
    gate = await _gate_admin(request, settings)
    if gate.response is not None:
        return gate.response
    return await _admin_ai_page(request, settings, gate.user, tab)


async def _admin_form(request: Request) -> dict[str, str]:
    form = await request.form()
    return {k: str(v) for k, v in form.items() if isinstance(v, str)}


def _ai_error_text(category: str | None) -> str | None:
    """Every ``ErrorCategory`` (12) has an ``ai_error.*`` key; unknown ⇒ generic."""
    if not category:
        return None
    return _MESSAGES.get(f"ai_error.{category}", _MESSAGES["error.generic"])


async def _admin_ai_spec(
    request: Request, tab: str, action: str, form: Mapping[str, str]
) -> tuple[str, str, Any] | None:
    target = form.get("id", "")
    if tab == "providers":
        if action == "create":
            return ("POST", "/admin/ai/providers", _provider_body(form))
        if action == "update" and target:
            return ("PATCH", f"/admin/ai/providers/{target}", _provider_body(form))
        if action == "delete" and target:
            return ("DELETE", f"/admin/ai/providers/{target}", None)
    elif tab == "models":
        if action == "create":
            return ("POST", "/admin/ai/models", _model_body(form))
        if action == "update" and target:
            return ("PATCH", f"/admin/ai/models/{target}", _model_body(form))
        if action == "delete" and target:
            return ("DELETE", f"/admin/ai/models/{target}", None)
    elif tab == "routing" and action == "put" and form.get("purpose") in PURPOSES:
        raw = await request.form()
        models = [str(m) for m in raw.getlist("models") if str(m)]
        return ("PUT", f"/admin/ai/routing/{form['purpose']}", {"models": models})
    elif tab == "probe" and action == "run":
        body: dict[str, Any] = {
            "model_id": form.get("model_id", ""),
            "prompt": form.get("prompt", ""),
        }
        if form.get("max_tokens"):
            body["max_tokens"] = int(form["max_tokens"])
        return ("POST", "/admin/ai/probe", body)
    elif tab == "audit" and action == "flag" and form.get("key") in FLAG_KEYS:
        return ("PUT", f"/admin/flags/{form['key']}", {"value": form.get("value") == "true"})
    elif tab == "dead_letter" and action == "requeue" and target:
        return ("POST", f"/admin/publishing/jobs/{target}/requeue", None)
    return None


@router.post("/admin/ai/{tab}/{action}", response_class=HTMLResponse)
async def admin_ai_action(request: Request, tab: str, action: str) -> Response:
    """One POST per catalog write (D-077). ``tab`` ∈ providers|models|routing|probe;
    ``action`` ∈ create|update|delete|put|run. Fields are read from the raw form so one
    handler serves every tab; the secret field is renamed in :func:`_provider_body`."""
    settings = get_settings()
    form = await _admin_form(request)
    gate = await _gate_admin(request, settings, form.get(CSRF_FIELD))
    if gate.response is not None:
        return gate.response
    spec = await _admin_ai_spec(request, tab, action, form)
    if spec is None:
        return _with_session(settings, gate.user, _not_found_page(request))
    try:
        user, result = await _call(
            request, settings, gate.user, lambda api: api.json(spec[0], spec[1], json=spec[2])
        )
    except ApiError as exc:
        if exc.status == 401:
            return _denied(settings, request)
        return await _admin_ai_page(request, settings, gate.user, tab, flash=_error_text(exc.code))
    probe = None
    flash: str | None = t("admin.saved")
    if tab == "probe" and isinstance(result, dict):
        probe = {**result, "category_text": _ai_error_text(result.get("error_category"))}
        flash = None
    return await _admin_ai_page(request, settings, user, tab, flash=flash, probe=probe)


# ------------------------------------------------------- admin entitlements ---
async def _admin_ent_ctx(api: ApiClient, brand_id: str) -> dict[str, Any]:
    ctx: dict[str, Any] = {"plans": PLANS, "brand_id": brand_id, "ent": None, "wa": None}
    if brand_id:
        ctx["ent"] = await api.get(f"/admin/entitlements/{brand_id}")
        ctx["wa"] = await _optional(api, f"/brands/{brand_id}/whatsapp/account")
        # SPRINT-13 A-2: ``suspended`` comes from the brand read (§3-9); admins who are not
        # members of the brand see no card (the API answers 404 ⇒ ``None``).
        ctx["brand"] = await _optional(api, f"/brands/{brand_id}")
    return ctx


async def _admin_ent_page(
    request: Request, settings: Settings, user: AppUser, brand_id: str, flash: str | None = None
) -> Response:
    try:
        user, data = await _call(
            request, settings, user, lambda api: _admin_ent_ctx(api, brand_id)
        )
    except ApiError as exc:
        if exc.status == 401:
            return _denied(settings, request)
        data = {"plans": PLANS, "brand_id": brand_id, "ent": None, "wa": None, "brand": None}
        flash = flash or _error_text(exc.code)
    ctx = {**_base_ctx(user, settings), **data, "flash": flash}
    return _with_session(settings, user, _render(request, "admin_entitlements.html", ctx))


@router.get("/admin/entitlements", response_class=HTMLResponse)
async def admin_entitlements(request: Request, brand_id: str = "") -> Response:
    settings = get_settings()
    gate = await _gate_admin(request, settings)
    if gate.response is not None:
        return gate.response
    return await _admin_ent_page(request, settings, gate.user, brand_id.strip())


@router.post("/admin/entitlements/{action}", response_class=HTMLResponse)
async def admin_entitlements_action(request: Request, action: str) -> Response:
    """``activate`` ⇒ ``POST /admin/entitlements/activate`` (plan + months + InstaPay note);
    ``wa-caps`` ⇒ ``PATCH /admin/entitlements/wa-caps/{account_id}`` (cap / kill switch)."""
    settings = get_settings()
    form = await _admin_form(request)
    gate = await _gate_admin(request, settings, form.get(CSRF_FIELD))
    if gate.response is not None:
        return gate.response
    brand_id = form.get("brand_id", "").strip()
    spec: tuple[str, str, Any] | None = None
    if action == "activate":
        body: dict[str, Any] = {"brand_id": brand_id, "plan": form.get("plan", "")}
        if form.get("months"):
            body["months"] = int(form["months"])
        if form.get("note"):
            body["note"] = form["note"]
        spec = ("POST", "/admin/entitlements/activate", body)
    elif action == "wa-caps" and form.get("account_id"):
        caps: dict[str, Any] = {}
        if form.get("daily_reply_cap"):
            caps["daily_reply_cap"] = int(form["daily_reply_cap"])
        if form.get("kill_switch") in ("true", "false"):
            caps["kill_switch"] = form["kill_switch"] == "true"
        spec = ("PATCH", f"/admin/entitlements/wa-caps/{form['account_id']}", caps)
    elif action in ("suspend", "unsuspend") and brand_id:  # SPRINT-13 A-2 kill switch
        spec = ("POST", f"/admin/brands/{brand_id}/{action}", None)
    if spec is None:
        return _with_session(settings, gate.user, _not_found_page(request))
    try:
        user, _ = await _call(
            request, settings, gate.user, lambda api: api.json(spec[0], spec[1], json=spec[2])
        )
    except ApiError as exc:
        if exc.status == 401:
            return _denied(settings, request)
        return await _admin_ent_page(request, settings, gate.user, brand_id, _error_text(exc.code))
    return await _admin_ent_page(request, settings, user, brand_id, t("admin.saved"))


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
