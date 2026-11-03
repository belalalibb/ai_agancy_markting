"""``/app`` routes (SPRINT-12.1 B-2/B-3): login, brand pick, today, inbox.

Every screen is rendered server-side (Jinja2, autoescape on) in Arabic/RTL; all copy
comes from ``i18n/ar.json`` (templates contain no literal Arabic). Data is fetched
through :class:`src.ui.api.ApiClient` — the public JSON API, in-process — so tenancy,
roles, entitlements and WA caps are enforced by the owning modules, never re-implemented.
"""

from __future__ import annotations

import json
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
from src.modules.auth.jwt import AccessTokenCodec
from src.ui.api import ApiClient, ApiError
from src.ui.session import (
    APP_PREFIX,
    CSRF_FIELD,
    LOGIN_PATH,
    AppUser,
    clear_session,
    csrf_token,
    read_session,
    redirect_to_login,
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


async def _resolve_user(request: Request, settings: Settings) -> AppUser | None:
    """Exchange the ``app_session`` cookie for an access token (rotating the refresh value)."""
    refresh_value = read_session(request.cookies, settings)
    if refresh_value is None:
        return None
    api = _api(request, settings, refresh_value=refresh_value)
    try:
        response = await api.request("POST", "/auth/refresh")
    finally:
        await api.aclose()
    if response.status_code != 200:
        return None
    payload = response.json()
    rotated = response.cookies.get(settings.auth_refresh_cookie_name) or refresh_value
    try:
        actor = _codec(settings).decode(payload["access_token"])
    except jwt.InvalidTokenError:
        return None
    return AppUser(
        user_id=actor.user_id,
        tenant_id=actor.tenant_id,
        sid=actor.sid,
        email=payload.get("user", {}).get("email"),
        access_token=payload["access_token"],
        refresh_value=rotated,
    )


def _with_session(settings: Settings, user: AppUser, response: Response) -> Response:
    set_session(response, settings, user.refresh_value)
    return response


def _denied(settings: Settings, request: Request) -> RedirectResponse:
    response = redirect_to_login(request.url.path)
    clear_session(response, settings)
    return response


def _base_ctx(user: AppUser, settings: Settings, brand: Mapping[str, Any] | None = None) -> dict:
    return {
        "user": user,
        "brand": brand,
        "csrf": csrf_token(settings, user.user_id),
        "is_admin": user.user_id in settings.platform_admin_user_ids,
        "app_name": settings.app_name,
    }


def _csrf_ok(settings: Settings, user: AppUser, presented: str | None) -> bool:
    return verify_csrf(settings, user.user_id, presented)


def _forbidden(settings: Settings, user: AppUser) -> Response:
    """403 still carries the rotated session cookie — the refresh already happened."""
    response = HTMLResponse(_error_text("csrf_rejected"), status_code=403)
    return _with_session(settings, user, response)


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
    if not refresh_value:
        ctx = {"error": _error_text("generic"), "next": next, "email": email}
        return _render(request, "login.html", ctx, status=502)
    target = next if next.startswith(f"{APP_PREFIX}/") and next != LOGIN_PATH else f"{APP_PREFIX}/"
    redirect = RedirectResponse(target, status_code=303)
    set_session(redirect, settings, refresh_value)
    return redirect


@router.post("/logout")
async def logout(request: Request) -> Response:
    settings = get_settings()
    refresh_value = read_session(request.cookies, settings)
    if refresh_value:
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
    user = await _resolve_user(request, settings)
    if user is None:
        return _denied(settings, request)
    api = _api(request, settings, user.access_token)
    try:
        brands = await api.get("/brands")
    except ApiError as exc:
        return _with_session(settings, user, _api_error_page(request, exc))
    finally:
        await api.aclose()
    if len(brands) == 1:
        target = f"{APP_PREFIX}/b/{brands[0]['id']}/today"
        return _with_session(settings, user, RedirectResponse(target, status_code=303))
    ctx = {**_base_ctx(user, settings), "brands": brands}
    return _with_session(settings, user, _render(request, "brands.html", ctx))


async def _brand_frame(api: ApiClient, brand_id: UUID) -> dict[str, Any]:
    brand = await api.get(f"/brands/{brand_id}")
    plan = await api.get(f"/brands/{brand_id}/entitlement")
    return {"brand": brand, "plan": plan}


# ------------------------------------------------------------------- today ---
async def _today_ctx(api: ApiClient, brand_id: UUID) -> dict[str, Any]:
    now = datetime.now(UTC)
    alerts = await api.get(f"/brands/{brand_id}/pulse/alerts", open="true", limit=TODAY_LIMIT)
    conversations = await api.get(f"/brands/{brand_id}/whatsapp/conversations", limit=INBOX_LIMIT)
    pending = await api.get(
        f"/brands/{brand_id}/content/items", status="pending_approval", limit=TODAY_LIMIT
    )
    return {
        "alerts": alerts["items"],
        "open_count": alerts["open_count"],
        "closing": [c for c in conversations["items"] if _expires_soon(c, now)],
        "pending": pending["items"],
        "now": now,
    }


async def _today_page(
    request: Request, settings: Settings, user: AppUser, brand_id: UUID, flash: str | None = None
) -> Response:
    api = _api(request, settings, user.access_token)
    try:
        frame = await _brand_frame(api, brand_id)
        cards = await _today_ctx(api, brand_id)
    except ApiError as exc:
        return _with_session(settings, user, _api_error_page(request, exc))
    finally:
        await api.aclose()
    ctx = {**_base_ctx(user, settings, frame["brand"]), **frame, **cards, "flash": flash}
    name = "partials/today_cards.html" if _is_partial(request) else "today.html"
    return _with_session(settings, user, _render(request, name, ctx))


@router.get("/b/{brand_id}/today", response_class=HTMLResponse)
async def today(request: Request, brand_id: UUID) -> Response:
    settings = get_settings()
    user = await _resolve_user(request, settings)
    if user is None:
        return _denied(settings, request)
    return await _today_page(request, settings, user, brand_id)


async def _today_action(
    request: Request, brand_id: UUID, csrf: str | None, method: str, path: str, body: Any = None
) -> Response:
    settings = get_settings()
    user = await _resolve_user(request, settings)
    if user is None:
        return _denied(settings, request)
    if not _csrf_ok(settings, user, csrf):
        return _forbidden(settings, user)
    flash = None
    api = _api(request, settings, user.access_token)
    try:
        await api.json(method, path, json=body)
    except ApiError as exc:
        flash = _error_text(exc.code)
    finally:
        await api.aclose()
    return await _today_page(request, settings, user, brand_id, flash)


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
    api = _api(request, settings, user.access_token)
    try:
        frame = await _brand_frame(api, brand_id)
        inbox = await _inbox_ctx(api, brand_id, conversation_id)
    except ApiError as exc:
        return _with_session(settings, user, _api_error_page(request, exc))
    finally:
        await api.aclose()
    ctx = {
        **_base_ctx(user, settings, frame["brand"]),
        **frame,
        **inbox,
        "flash": flash,
        "draft": draft,
    }
    name = "partials/thread.html" if _is_partial(request) and conversation_id else "inbox.html"
    return _with_session(settings, user, _render(request, name, ctx))


@router.get("/b/{brand_id}/inbox", response_class=HTMLResponse)
async def inbox(request: Request, brand_id: UUID, conversation: UUID | None = None) -> Response:
    settings = get_settings()
    user = await _resolve_user(request, settings)
    if user is None:
        return _denied(settings, request)
    return await _inbox_page(request, settings, user, brand_id, conversation)


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
    settings = get_settings()
    user = await _resolve_user(request, settings)
    if user is None:
        return _denied(settings, request)
    if not _csrf_ok(settings, user, csrf):
        return _forbidden(settings, user)
    flash = None
    draft = ""
    api = _api(request, settings, user.access_token)
    try:
        result = await api.json(method, path, json=body)
        if prefill and isinstance(result, dict):
            draft = str(result.get(prefill) or "")
    except ApiError as exc:
        flash = _error_text(exc.code)
    finally:
        await api.aclose()
    return await _inbox_page(
        request, settings, user, brand_id, conversation_id, flash=flash, draft=draft
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
