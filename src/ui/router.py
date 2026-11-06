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
from src.ui.api import ApiClient, ApiError, error_code
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
from src.ui.slug import derive_slug, slug_retry

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
        ctx = {"error": _error_text(_auth_code(response, code)), "next": next, "email": email}
        return _render(request, "login.html", ctx, status=response.status_code)
    established = _establish_session(response, settings, next)
    if established is None:
        ctx = {"error": _error_text("generic"), "next": next, "email": email}
        return _render(request, "login.html", ctx, status=502)
    return established


# auth's fixed model-validator message (matched, never displayed)
WEAK_PASSWORD_RULE = "password must not equal the email address"  # noqa: S105 - a message


def _auth_code(response: Any, default: str | None) -> str:
    """Error code of an ``/auth/*`` failure: body ``detail.error`` ⇒ 429 ⇒ ``default``."""
    code = "rate_limited" if response.status_code == 429 else default
    payload = response.json() if response.content else {}
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict) and isinstance(detail.get("error"), str):
        code = detail["error"]
    elif isinstance(detail, list) and response.status_code == 422:
        code = "password_too_weak" if _only_weak_password(detail) else "http_422"
    return code or "generic"


def _only_weak_password(detail: list[Any]) -> bool:
    """B-0.3 (S14A-3): ``True`` only when *every* 422 entry is the register password rule —
    ``password`` ``string_too_short`` or the model rule ``password must not equal the email``.
    The message/input is never shown; the code only selects ``error.password_too_weak``."""
    if not detail:
        return False
    for err in detail:
        if not isinstance(err, dict):
            return False
        loc = err.get("loc") or ()
        short = bool(loc) and loc[-1] == "password" and err.get("type") == "string_too_short"
        same = err.get("type") == "value_error" and WEAK_PASSWORD_RULE in str(err.get("msg", ""))
        if not (short or same):
            return False
    return True


def _establish_session(response: Any, settings: Settings, next: str) -> Response | None:  # noqa: A002
    """Directive 4-A: ``TokenOut`` + refresh cookie (from ``/auth/login`` **or**
    ``/auth/register``) ⇒ the ``/app`` cookies + 303; ``None`` when either part is missing."""
    refresh_value = response.cookies.get(settings.auth_refresh_cookie_name)
    access_token = (response.json() or {}).get("access_token")
    if not refresh_value or not access_token:
        return None
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


# ------------------------------------------------------- register (14-A A-1) ---
@router.get("/register", response_class=HTMLResponse)
async def register_form(request: Request) -> Response:
    return _render(request, "register.html", {"error": None, "form": {}})


@router.post("/register", response_class=HTMLResponse)
async def register_submit(  # noqa: PLR0913 - one form field per input
    request: Request,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password_confirm: Annotated[str, Form()] = "",
    tenant_name: Annotated[str, Form()] = "",
    tenant_slug: Annotated[str, Form()] = "",
) -> Response:
    """One ``POST /auth/register`` (201 ``TokenOut`` + refresh cookie) ⇒ session — never a
    second ``/auth/login`` (directive 4-A). Mismatched passwords ⇒ local 400, no API call."""
    settings = get_settings()
    form = {"email": email, "tenant_name": tenant_name, "tenant_slug": tenant_slug}
    if password != password_confirm:
        ctx = {"error": t("register.password_mismatch"), "form": form}
        return _render(request, "register.html", ctx, status=400)
    manual = bool(tenant_slug.strip())
    slug = tenant_slug.strip() or derive_slug(tenant_name, "org")
    api = _api(request, settings)
    try:
        for attempt in range(3):
            body = {
                "email": email,
                "password": password,
                "tenant_slug": slug,
                "tenant_name": tenant_name,
            }
            response = await api.request("POST", "/auth/register", json=body)
            if response.status_code == 201:
                established = _establish_session(response, settings, "")
                if established is None:
                    ctx = {"error": _error_text("generic"), "form": form}
                    return _render(request, "register.html", ctx, status=502)
                return established
            code = _auth_code(response, "generic")
            if code != "tenant_slug_taken" or manual or attempt == 2:
                break
            slug = slug_retry(slug, attempt + 1)  # directive 4-B-4: ``-2`` then ``-<4hex>``
    finally:
        await api.aclose()
    status = response.status_code if response.status_code in (400, 409, 422, 429) else 502
    ctx = {"error": _error_text(code), "form": form}
    return _render(request, "register.html", ctx, status=status)


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
async def pick_brand(request: Request, flash: str = "") -> Response:
    settings = get_settings()
    gate = await _gate_user(request, settings)
    if gate.response is not None:
        return gate.response
    text = _error_text(flash) if flash in META_CALLBACK_CODES else None  # A-3 bridge 4xx
    return await _brands_page(request, settings, gate.user, text)


SECTORS = ("restaurants", "clinics")  # = brand_brain Sector (14-A shows these two only)
ONBOARDING_STEPS = ("account", "brand", "channel", "draft")  # 14-A A-5


async def _onboarding(api: ApiClient, brands: list[Any]) -> list[dict[str, Any]] | None:
    """A-5: 4 steps for the **first** brand; ``None`` once all are done (bar disappears). A
    failing sub-call marks that step *unknown* (``done=None``) — never a 500."""
    if not brands:
        link = f"{APP_PREFIX}/#create"
        return [
            {"step": "account", "done": True, "href": None},
            {"step": "brand", "done": False, "href": link},
            {"step": "channel", "done": None, "href": link},
            {"step": "draft", "done": None, "href": link},
        ]
    bid = brands[0]["id"]
    base = f"{APP_PREFIX}/b/{bid}"
    channel: bool | None
    draft: bool | None
    try:
        connections = await api.get(f"/brands/{bid}/social/meta/connections")
        wa = await _optional(api, f"/brands/{bid}/whatsapp/account")
        channel = (isinstance(wa, dict) and wa.get("status") == "active") or any(
            p.get("is_selected")
            for c in connections
            if c.get("status") == "active"
            for p in c.get("pages", [])
        )
    except ApiError:
        channel = None
    try:
        items = await api.get(f"/brands/{bid}/content/items", limit=1)
        draft = bool(items.get("items"))
    except ApiError:
        draft = None
    steps = [
        {"step": "account", "done": True, "href": None},
        {"step": "brand", "done": True, "href": None},
        {"step": "channel", "done": channel, "href": f"{base}/settings#connect"},
        {"step": "draft", "done": draft, "href": f"{base}/content"},
    ]
    return None if all(s["done"] is True for s in steps) else steps


async def _brands_ctx(api: ApiClient) -> dict[str, Any]:
    brands = await api.get("/brands")
    return {"brands": brands, "onboarding": await _onboarding(api, brands)}


async def _brands_page(
    request: Request,
    settings: Settings,
    user: AppUser,
    flash: str | None = None,
    *,
    form: Mapping[str, str] | None = None,
    status: int = 200,
) -> Response:
    try:
        user, data = await _call(request, settings, user, _brands_ctx)
    except ApiError as exc:
        return _api_failure(request, settings, user, exc)
    brands = data["brands"]
    if len(brands) == 1 and data["onboarding"] is None and flash is None:
        target = f"{APP_PREFIX}/b/{brands[0]['id']}/today"
        return _with_session(settings, user, RedirectResponse(target, status_code=303))
    ctx = {
        **_base_ctx(user, settings),
        **data,
        "flash": flash,
        "form": form or {},
        "sectors": SECTORS,
    }
    return _with_session(settings, user, _render(request, "brands.html", ctx, status=status))


@router.post("/brands", response_class=HTMLResponse)
async def create_first_brand(
    request: Request,
    name: Annotated[str, Form()] = "",
    sector: Annotated[str, Form()] = "",
    slug: Annotated[str, Form()] = "",
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    """A-2: ``POST /brands`` (slug derived per directive 4-B, retried on ``brand_slug_taken``) then
    ``PUT …/brain/profile`` with ``If-Match`` from the GET. The sector write never fails the
    creation — it only adds a flash. 303 ⇒ ``…/settings#connect``."""
    settings = get_settings()
    gate = await _gate_user(request, settings, csrf)
    if gate.response is not None:
        return gate.response
    form = {"name": name, "sector": sector, "slug": slug}
    manual = bool(slug.strip())
    candidate = slug.strip() or derive_slug(name, "brand")

    async def _create(api: ApiClient) -> Any:
        current = candidate
        for attempt in range(3):
            try:
                return await api.post("/brands", {"slug": current, "name": name})
            except ApiError as exc:
                if exc.code != "brand_slug_taken" or manual or attempt == 2:
                    raise
                current = slug_retry(current, attempt + 1)
        return None  # pragma: no cover - loop always returns or raises

    try:
        user, brand = await _call(request, settings, gate.user, _create)
    except ApiError as exc:
        if exc.status == 401:
            return _denied(settings, request)
        code = "slug_invalid" if exc.status == 422 else exc.code
        status = exc.status if exc.status in (400, 403, 409, 422, 429) else 502
        return await _brands_page(
            request, settings, gate.user, _error_text(code), form=form, status=status
        )
    flash: str | None = None
    if sector in SECTORS:

        async def _set_sector(api: ApiClient) -> Any:
            path = f"/brands/{brand['id']}/brain/profile"
            profile = await api.get(path)
            body = {
                "products": profile.get("products", []),
                "audience": profile.get("audience", {}),
                "tone": {
                    k: v
                    for k, v in (profile.get("tone") or {}).items()
                    if k in ("voice", "formality", "emoji_policy", "banned_phrases")
                    and v is not None
                },
                "goals": profile.get("goals", []),
                "sector": sector,
            }
            return await api.put(path, body, headers={"If-Match": str(profile["version"])})

        try:
            user, _ = await _call(request, settings, user, _set_sector)
        except ApiError as exc:
            if exc.status == 401:
                return _denied(settings, request)
            flash = t("brands.sector_failed")
    target = f"{APP_PREFIX}/b/{brand['id']}/settings#connect"
    if flash is not None:
        target = f"{APP_PREFIX}/b/{brand['id']}/settings?flash=sector_failed#connect"
    return _with_session(settings, user, RedirectResponse(target, status_code=303))


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
MIN_IMAGE_URL = 12  # = content.schemas.MediaRef.url min_length
CONTENT_FLASHES = {"draft_created": "content.created"}
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
    can_write = frame["brand"].get("role") in ("owner", "manager")
    targets = await _targets(api, brand_id) if can_write else {"channels": [], "pages": []}
    return {
        **frame,
        "items": page["items"],
        "status": status or "",
        "tabs": CONTENT_TABS,
        "can_write": can_write,
        **targets,
    }


async def _targets(api: ApiClient, brand_id: UUID) -> dict[str, Any]:
    """What a draft may aim at (B-4): the *active* channels of the registry and the pages of
    *active* connections that the owner selected — ``value`` is the Graph ``page_id``."""
    registry = await api.get("/channels")
    accounts = await api.get(f"/brands/{brand_id}/social/meta/connections")
    return {
        "channels": [
            c["channel"] for c in registry["channels"] if c["activation_status"] == "active"
        ],
        "pages": [
            {"page_id": p["page_id"], "name": p["page_name"], "ig": bool(p.get("instagram"))}
            for a in accounts
            if a.get("status") == "active"
            for p in a.get("pages", [])
            if p.get("is_selected")
        ],
    }


def _utc_iso(value: str) -> str | None:
    """``datetime-local`` text (no zone; the form says UTC) ⇒ aware ISO ``…+00:00``."""
    try:
        moment = datetime.fromisoformat(value)
        moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    except (ValueError, OverflowError):
        return None
    return moment.isoformat()


def _draft_problem(targets: Mapping[str, Any], channel: str, page_id: str, image: str) -> str:
    """The first reason a draft must not be sent (``""`` = fine) — decided before the API
    call because ``POST /content/items`` does not look at the target."""
    page = next((p for p in targets["pages"] if p["page_id"] == page_id), None)
    if channel not in targets["channels"]:
        return "channel_not_activated"
    if page_id and page is None:
        return "target_page_not_selected"
    if channel == "instagram" and page_id and not page["ig"]:
        return "instagram_not_linked"
    if image and not (image.startswith("https://") and len(image) >= MIN_IMAGE_URL):
        return "invalid_image_url"
    return ""


async def _content_page(
    request: Request,
    settings: Settings,
    user: AppUser,
    brand_id: UUID,
    status: str | None,
    *,
    flash: str | None = None,
    batch: list[dict[str, str]] | None = None,
    draft: Mapping[str, str] | None = None,
    code: int = 200,
) -> Response:
    try:
        user, data = await _call(
            request, settings, user, lambda api: _content_ctx(api, brand_id, status)
        )
    except ApiError as exc:
        return _api_failure(request, settings, user, exc)
    ctx = {
        **_base_ctx(user, settings, data["brand"]),
        **data,
        "flash": flash,
        "batch": batch,
        "draft": draft or {},
    }
    name = "partials/content_list.html" if _is_partial(request) else "content.html"
    return _with_session(settings, user, _render(request, name, ctx, status=code))


@router.get("/b/{brand_id}/content", response_class=HTMLResponse)
async def content(request: Request, brand_id: UUID, status: str = "", flash: str = "") -> Response:
    settings = get_settings()
    gate = await _gate_user(request, settings)
    if gate.response is not None:
        return gate.response
    key = CONTENT_FLASHES.get(flash)  # closed set: no free text in a query string
    return await _content_page(
        request, settings, gate.user, brand_id, _tab(status), flash=t(key) if key else None
    )


@router.post("/b/{brand_id}/content/new", response_class=HTMLResponse)
async def content_new(  # noqa: PLR0913 - one form field per draft input
    request: Request,
    brand_id: UUID,
    channel: Annotated[str, Form()] = "",
    body: Annotated[str, Form()] = "",
    target_page_id: Annotated[str, Form()] = "",
    image_url: Annotated[str, Form()] = "",
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    """A human drafts a post (D-055: a *draft* only — submit/approve/schedule stay separate
    actions). Bad targets are refused here with the user's text kept; the API decides the rest."""
    settings = get_settings()
    gate = await _gate_user(request, settings, csrf)
    if gate.response is not None:
        return gate.response
    keep = {"channel": channel, "body": body, "target_page_id": target_page_id}
    keep["image_url"] = image_url
    payload: dict[str, Any] = {"channel": channel, "body": body, "media_refs": []}
    if target_page_id:
        payload["target_page_id"] = target_page_id
    if channel == "instagram" and image_url:
        payload["media_refs"] = [{"url": image_url, "kind": "image"}]

    async def create(api: ApiClient) -> str:
        problem = _draft_problem(await _targets(api, brand_id), channel, target_page_id, image_url)
        if not problem:
            await api.json("POST", f"/brands/{brand_id}/content/items", json=payload)
        return problem

    code, status = "", 200
    try:
        user, code = await _call(request, settings, gate.user, create)
    except ApiError as exc:
        if exc.status == 401:
            return _denied(settings, request)
        user, code, status = gate.user, exc.code, exc.status
    if code:
        status = status if status in (400, 403, 404, 409, 422, 429) else 422
        return await _content_page(
            request,
            settings,
            user,
            brand_id,
            None,
            flash=_error_text(code),
            draft=keep,
            code=status,
        )
    target = f"{APP_PREFIX}/b/{brand_id}/content?status=draft&flash=draft_created"
    return _with_session(settings, user, RedirectResponse(target, status_code=303))


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
        when = _utc_iso(scheduled_for)
        if when is None:  # naive/garbled text never reaches the API (and never a 500)
            gate = await _gate_user(request, get_settings(), csrf)
            if gate.response is not None:
                return gate.response
            return await _content_page(
                request, get_settings(), gate.user, brand_id, _tab(status),
                flash=_error_text("invalid_datetime"), code=400,
            )  # fmt: skip
        body: dict[str, Any] = {"scheduled_for": when}
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
    content_plan: Mapping[str, Any] | None = None,
) -> Response:
    try:
        user, data = await _call(
            request, settings, user, lambda api: _diagnosis_ctx(api, brand_id)
        )
    except ApiError as exc:
        return _api_failure(request, settings, user, exc)
    ctx = {
        **_base_ctx(user, settings, data["brand"]),
        **data,
        "flash": flash,
        "share": share,
        "content_plan": content_plan,
    }
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


@router.post("/b/{brand_id}/diagnosis/content-plan", response_class=HTMLResponse)
async def content_plan(
    request: Request, brand_id: UUID, csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None
) -> Response:
    """B-3: ``POST /brain/content-plan`` ⇒ ≤7 **drafts** (D-055: nothing is approved, scheduled
    or queued here — each draft still needs submit ⇒ OWNER approve ⇒ schedule by a human)."""
    path = f"/brands/{brand_id}/brain/content-plan"
    gate, flash, result = await _action(request, csrf, "POST", path, None)
    if gate.response is not None:
        return gate.response
    plan = None
    if flash is None and isinstance(result, dict):
        items = result.get("items", [])
        dropped = int(result.get("items_dropped", 0))
        plan = {"items": items, "dropped": dropped}
        flash = t("plan.done", n=len(items), dropped=dropped)
    return await _diagnosis_page(
        request, get_settings(), gate.user, brand_id, flash=flash, content_plan=plan
    )


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


# ------------------------------------------------------------------- brain ---
EVIDENCE_LIMIT = 20
FORMALITIES = ("casual", "neutral", "formal")  # = brand_brain Tone.formality
EMOJI_POLICIES = ("none", "light", "free")  # = brand_brain Tone.emoji_policy
GOAL_KINDS = ("awareness", "leads", "sales", "retention")  # = brand_brain GoalKind
MAX_PRODUCTS, MAX_GOALS, MAX_COMPETITORS = 20, 5, 3  # = brand_brain.domain.models caps
BRAIN_FLASHES = {  # translated keys for ``?flash=`` after a 303 (no free text)
    "brain_saved": "brain.saved",
    "competitor_added": "brain.competitor_added",
    "competitor_deleted": "brain.competitor_deleted",
    "competitor_refreshed": "brain.competitor_refreshed",
    "competitor_unavailable": "brain.competitor_unavailable",
    "evidence_refreshed": "brain.evidence_refreshed",
}
BRAIN_ERROR_STATUS = (403, 404, 409, 412, 422, 428, 429)


def _lines(text: Any) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _val(form: Any, name: str) -> str:
    return str(form.get(name) or "").strip()


def _vals(form: Any, name: str) -> list[str]:
    return [str(v).strip() for v in form.getlist(name)]


def _profile_body(form: Any) -> dict[str, Any]:
    """The ``PUT /brain/profile`` body — products/audience/tone/goals/sector only; the form
    never carries ``tone.reply_templates`` (filled by accepting a recommendation, B-3)."""
    columns = [_vals(form, n) for n in ("p_name", "p_desc", "p_price", "p_url")]
    products = [
        {"name": n, "description": d, "price_hint": p or None, "url": u or None}
        for n, d, p, u in zip(*columns, strict=False)
        if n or d or p or u
    ]
    goals = [
        {"kind": k, "target": tg, "horizon_days": int(days) if days.isdigit() else days}
        for k, tg, days in zip(
            _vals(form, "g_kind"), _vals(form, "g_target"), _vals(form, "g_days"), strict=False
        )
        if tg or days
    ]
    audience = {k: _lines(form.get(f"a_{k}")) for k in ("segments", "regions", "languages")}
    audience["age_bands"] = _lines(form.get("a_age_bands"))
    return {
        "products": products,
        "audience": audience,
        "tone": {
            "voice": _val(form, "t_voice"),
            "formality": _val(form, "t_formality"),
            "emoji_policy": _val(form, "t_emoji"),
            "banned_phrases": _lines(form.get("t_banned")),
        },
        "goals": goals,
        "sector": _val(form, "sector") or None,
    }


async def _brain_ctx(api: ApiClient, brand_id: UUID) -> dict[str, Any]:
    frame = await _brand_frame(api, brand_id)
    brain = f"/brands/{brand_id}/brain"
    competitors = await api.get(f"{brain}/competitors")
    evidence = await api.get(f"{brain}/evidence", limit=EVIDENCE_LIMIT)
    return {
        **frame,
        "profile": await api.get(f"{brain}/profile"),
        "competitors": competitors["items"],
        "evidence": evidence["items"],
        "names": {c["id"]: c["name"] for c in competitors["items"]},
    }


async def _brain_page(  # noqa: PLR0913 - one argument per rendering input
    request: Request,
    settings: Settings,
    user: AppUser,
    brand_id: UUID,
    *,
    flash: str | None = None,
    draft: Mapping[str, Any] | None = None,
    error: str | None = None,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
) -> Response:
    try:
        user, data = await _call(request, settings, user, lambda api: _brain_ctx(api, brand_id))
    except ApiError as exc:
        return _api_failure(request, settings, user, exc)
    form = dict(draft if draft is not None else data["profile"])  # the user's own inputs win
    form["products"] = [*form.get("products", []), *[{}] * 3][:MAX_PRODUCTS]
    form["goals"] = [*form.get("goals", []), *[{}] * 2][:MAX_GOALS]
    ctx = {
        **_base_ctx(user, settings, data["brand"]),
        **data,
        "form": form,
        "flash": flash,
        "err": error,
        "can_write": data["brand"].get("role") in ("owner", "manager"),
        "formalities": FORMALITIES,
        "emoji_policies": EMOJI_POLICIES,
        "goal_kinds": GOAL_KINDS,
        "sectors": SECTORS,
        "grades": GRADES,
        "max_competitors": MAX_COMPETITORS,
    }
    response = _render(request, "brain.html", ctx, status=status)
    response.headers.update(headers or {})
    return _with_session(settings, user, response)


@router.get("/b/{brand_id}/brain", response_class=HTMLResponse)
async def brain(request: Request, brand_id: UUID, flash: str = "") -> Response:
    settings = get_settings()
    gate = await _gate_user(request, settings)
    if gate.response is not None:
        return gate.response
    key = BRAIN_FLASHES.get(flash)
    return await _brain_page(request, settings, gate.user, brand_id, flash=t(key) if key else None)


async def _raw(api: ApiClient, method: str, path: str, body: Any, headers: Any) -> Any:
    response = await api.request(method, path, json=body, headers=headers)
    if response.status_code == 401:
        raise ApiError(401, "unauthenticated")
    return response


async def _brain_write(  # noqa: PLR0913 - gate + one API write + its two outcomes
    request: Request,
    brand_id: UUID,
    csrf: Any,
    method: str,
    path: str,
    *,
    ok: str,
    body: Any = None,
    headers: Mapping[str, str] | None = None,
    draft: Mapping[str, Any] | None = None,
) -> Response:
    """CSRF 403 before any effect; success ⇒ 303 + closed flash key; failure ⇒ the page again
    with the Arabic message, the user's inputs, the real status and ``Retry-After`` (B-1/B-2)."""
    settings = get_settings()
    gate = await _gate_user(request, settings, None if csrf is None else str(csrf))
    if gate.response is not None:
        return gate.response
    try:
        user, res = await _call(
            request, settings, gate.user, lambda api: _raw(api, method, path, body, headers)
        )
    except ApiError:
        return _denied(settings, request)
    try:
        payload = res.json() if res.content else None
    except ValueError:
        payload = None
    if res.status_code < 400:
        if isinstance(payload, dict) and payload.get("status") == "unavailable":
            ok = "competitor_unavailable"
        target = f"{APP_PREFIX}/b/{brand_id}/brain?flash={ok}"
        return _with_session(settings, user, RedirectResponse(target, status_code=303))
    code = error_code(payload, res.status_code)
    retry = res.headers.get("retry-after")
    extra = {"Retry-After": retry} if res.status_code == 429 and retry else None  # noqa: PLR2004
    status = res.status_code if res.status_code in BRAIN_ERROR_STATUS else 502
    return await _brain_page(
        request, settings, user, brand_id, flash=_error_text(code), draft=draft, error=code,
        status=status, headers=extra,
    )  # fmt: skip


@router.post("/b/{brand_id}/brain", response_class=HTMLResponse)
async def brain_save(request: Request, brand_id: UUID) -> Response:
    """``If-Match`` is the hidden ``version`` of the GET the user saw — never a refetch (a
    concurrent edit ⇒ 412 ⇒ the user's text is kept, no blind retry, no version swap)."""
    form = await request.form()
    version = _val(form, "version")
    body = _profile_body(form)
    return await _brain_write(
        request, brand_id, form.get(CSRF_FIELD), "PUT", f"/brands/{brand_id}/brain/profile",
        ok="brain_saved", body=body, headers={"If-Match": version} if version.isdigit() else None,
        draft={**body, "version": version},
    )  # fmt: skip


@router.post("/b/{brand_id}/brain/competitors", response_class=HTMLResponse)
async def competitor_add(request: Request, brand_id: UUID) -> Response:
    form = await request.form()
    body: dict[str, Any] = {
        "name": _val(form, "c_name"),
        "kind": _val(form, "c_kind") or "direct",
        "notes": _val(form, "c_notes"),
        "allowed_hosts": _lines(form.get("c_hosts")),
    }
    for key, field in (("facebook_page_id", "c_fb"), ("website_host", "c_host")):
        if _val(form, field):
            body[key] = _val(form, field)
    path = f"/brands/{brand_id}/brain/competitors"
    return await _brain_write(
        request, brand_id, form.get(CSRF_FIELD), "POST", path, ok="competitor_added", body=body
    )


@router.post("/b/{brand_id}/brain/competitors/{competitor_id}/delete", response_class=HTMLResponse)
async def competitor_delete(
    request: Request,
    brand_id: UUID,
    competitor_id: UUID,
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    path = f"/brands/{brand_id}/brain/competitors/{competitor_id}"
    return await _brain_write(request, brand_id, csrf, "DELETE", path, ok="competitor_deleted")


@router.post(
    "/b/{brand_id}/brain/competitors/{competitor_id}/refresh", response_class=HTMLResponse
)
async def competitor_refresh(
    request: Request,
    brand_id: UUID,
    competitor_id: UUID,
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    path = f"/brands/{brand_id}/brain/competitors/{competitor_id}/refresh"
    return await _brain_write(request, brand_id, csrf, "POST", path, ok="competitor_refreshed")


@router.post("/b/{brand_id}/brain/evidence/refresh", response_class=HTMLResponse)
async def evidence_refresh(
    request: Request, brand_id: UUID, csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None
) -> Response:
    path = f"/brands/{brand_id}/brain/evidence/refresh"
    return await _brain_write(request, brand_id, csrf, "POST", path, ok="evidence_refreshed")


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


SETTINGS_FLASHES = {  # 14-A: translated keys for ``?flash=`` after a 303 (no free text)
    "sector_failed": "brands.sector_failed",
    "pages_selected": "pages.saved",
    "wa_connected": "wa.connected_ok",
    "wa_rotated": "wa.rotated_ok",
    "wa_disabled": "wa.disabled_ok",
}


@router.get("/b/{brand_id}/settings", response_class=HTMLResponse)
async def brand_settings(request: Request, brand_id: UUID, flash: str = "") -> Response:
    settings = get_settings()
    gate = await _gate_user(request, settings)
    if gate.response is not None:
        return gate.response
    key = SETTINGS_FLASHES.get(flash)
    return await _settings_page(request, settings, gate.user, brand_id, t(key) if key else None)


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


@router.get("/social/meta/callback")
async def meta_callback_bridge(  # noqa: PLR0913 - Meta's query parameters
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> Response:
    """A-3 / directive 4-C: the browser lands here after Facebook Login; the UI forwards the
    **same** query to ``GET /social/meta/callback`` (JSON, unchanged) under the user's token. 201 ⇒
    303 to the page-selection screen; 4xx ⇒ 303 ``/app/`` with the translated code (the
    brand is unknown then). No session ⇒ login with ``next`` carrying ``code``/``state``."""
    settings = get_settings()
    gate = await _gate_user(request, settings)
    if gate.response is not None:
        query = f"?{request.url.query}" if request.url.query else ""
        target = login_target(f"{request.url.path}{query}")
        response: Response = RedirectResponse(target, status_code=303)
        clear_session(response, settings)
        return response
    params = {
        k: v
        for k, v in (
            ("code", code),
            ("state", state),
            ("error", error),
            ("error_description", error_description),
        )
        if v is not None
    }

    async def _complete(api: ApiClient) -> Any:
        return await api.json("GET", "/social/meta/callback", params=params)

    try:
        user, account = await _call(request, settings, gate.user, _complete)
    except ApiError as exc:
        if exc.status == 401:
            return _denied(settings, request)
        code_ = _meta_callback_code(exc)
        target = f"{APP_PREFIX}/?flash={code_}"
        return _with_session(settings, gate.user, RedirectResponse(target, status_code=303))
    target = f"{APP_PREFIX}/b/{account['brand_id']}/settings/connections/{account['id']}/pages"
    return _with_session(settings, user, RedirectResponse(target, status_code=303))


META_CALLBACK_CODES = (
    "user_denied",
    "invalid_oauth_state",
    "provider_error",
    "missing_parameters",
)


def _meta_callback_code(exc: ApiError) -> str:
    """The callback's 400s carry ``detail.error.type`` (not ``detail.error``)."""
    detail = exc.detail.get("detail") if isinstance(exc.detail, dict) else None
    inner = detail.get("error") if isinstance(detail, dict) else None
    kind = inner.get("type") if isinstance(inner, dict) else None
    return kind if kind in META_CALLBACK_CODES else exc.code


async def _pages_ctx(api: ApiClient, brand_id: UUID, connection_id: UUID) -> dict[str, Any]:
    frame = await _brand_frame(api, brand_id)
    connection = await api.get(f"/brands/{brand_id}/social/meta/connections/{connection_id}")
    return {**frame, "connection": connection}


async def _pages_page(
    request: Request,
    settings: Settings,
    user: AppUser,
    brand_id: UUID,
    connection_id: UUID,
    error: str | None = None,
    *,
    status: int = 200,
) -> Response:
    try:
        user, data = await _call(
            request, settings, user, lambda api: _pages_ctx(api, brand_id, connection_id)
        )
    except ApiError as exc:
        return _api_failure(request, settings, user, exc)
    ctx = {**_base_ctx(user, settings, data["brand"]), **data, "error": error}
    return _with_session(settings, user, _render(request, "pages.html", ctx, status=status))


@router.get(
    "/b/{brand_id}/settings/connections/{connection_id}/pages", response_class=HTMLResponse
)
async def select_pages_form(request: Request, brand_id: UUID, connection_id: UUID) -> Response:
    settings = get_settings()
    gate = await _gate_user(request, settings)
    if gate.response is not None:
        return gate.response
    return await _pages_page(request, settings, gate.user, brand_id, connection_id)


@router.post(
    "/b/{brand_id}/settings/connections/{connection_id}/pages", response_class=HTMLResponse
)
async def select_pages_submit(
    request: Request,
    brand_id: UUID,
    connection_id: UUID,
    page_ids: Annotated[list[str], Form()] = [],  # noqa: B006 - FastAPI multi-value form
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    """Empty selection ⇒ local Arabic error, no API call; else ``PUT …/pages/selection``."""
    settings = get_settings()
    gate = await _gate_user(request, settings, csrf)
    if gate.response is not None:
        return gate.response
    chosen = [p.strip() for p in page_ids if p.strip()]
    if not chosen:
        return await _pages_page(
            request, settings, gate.user, brand_id, connection_id, t("pages.none"), status=400
        )
    path = f"/brands/{brand_id}/social/meta/connections/{connection_id}/pages/selection"
    try:
        user, _ = await _call(
            request, settings, gate.user, lambda api: api.put(path, {"page_ids": chosen})
        )
    except ApiError as exc:
        if exc.status == 401:
            return _denied(settings, request)
        status = exc.status if exc.status in (403, 404, 409, 422) else 502
        return await _pages_page(
            request,
            settings,
            gate.user,
            brand_id,
            connection_id,
            _error_text(exc.code),
            status=status,
        )
    target = f"{APP_PREFIX}/b/{brand_id}/settings?flash=pages_selected"
    return _with_session(settings, user, RedirectResponse(target, status_code=303))


@router.post("/b/{brand_id}/settings/connections/{connection_id}/resync")
async def connection_resync(
    request: Request,
    brand_id: UUID,
    connection_id: UUID,
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    path = f"/brands/{brand_id}/social/meta/connections/{connection_id}/resync"
    return await _settings_action(request, brand_id, csrf, "POST", path, None, "settings.resynced")


# ------------------------------------------------- WhatsApp Cloud API (14-A A-4) ---
WA_FIELDS = {  # directive 4-D: neutral form names (templates) ⇒ API body keys (Python only)
    "wa_pnid": "phone_number_id",
    "wa_waba": "waba_id",
    "wa_phone": "display_phone",
    "wa_sys": "system_token",
    "wa_sec": "app_secret",
    "wa_verify": "verify_token",
}
WA_ROTATE_FIELDS = ("wa_sys", "wa_sec")  # ``…/account/rotate`` body (``extra="forbid"``)


def _wa_payload(form: Mapping[str, str], *, rotate: bool = False) -> dict[str, str]:
    """Map the ``wa_*`` inputs to the API body; empty secrets are dropped (rotate sends
    only what was typed; connect lets the API's 422 name the short field)."""
    names = WA_ROTATE_FIELDS if rotate else tuple(WA_FIELDS)
    body = {WA_FIELDS[n]: (form.get(n) or "").strip() for n in names}
    return {k: v for k, v in body.items() if v or k in ("phone_number_id", "waba_id")}


def _wa_422_text(exc: ApiError) -> str:
    """Pydantic 422 ⇒ "field X is too short / invalid" using the ``wa.*`` label of the field."""
    detail = exc.detail.get("detail") if isinstance(exc.detail, dict) else None
    if isinstance(detail, list) and detail:
        loc = detail[0].get("loc") or ()
        field = next((n for n, api in WA_FIELDS.items() if loc and loc[-1] == api), None)
        if field is not None:
            kind = "short" if "short" in str(detail[0].get("type", "")) else "invalid"
            return t(f"wa.field_{kind}", field=t(f"wa.{field[3:]}"))
    return _error_text(exc.code)


async def _wa_action(request: Request, brand_id: UUID, *, action: str) -> Response:
    """``action``: ``connect`` | ``rotate`` | ``disable`` (B-0.2: disable = ``DELETE …/account``,
    which keeps every conversation; it needs the explicit ``wa_confirm`` box)."""
    settings = get_settings()
    form = await _admin_form(request)
    gate = await _gate_user(request, settings, form.get(CSRF_FIELD))
    if gate.response is not None:
        return gate.response
    base = f"/brands/{brand_id}/whatsapp/account"
    if action == "disable" and form.get("wa_confirm") != "yes":
        page = await _settings_page(
            request, settings, gate.user, brand_id, t("wa.disable_confirm_needed")
        )
        page.status_code = 400
        return page

    async def _send(api: ApiClient) -> Any:
        if action == "disable":
            return await api.delete(base)
        rotate = action == "rotate"
        path = f"{base}/rotate" if rotate else base
        return await api.post(path, _wa_payload(form, rotate=rotate))

    try:
        user, _ = await _call(request, settings, gate.user, _send)
    except ApiError as exc:
        if exc.status == 401:
            return _denied(settings, request)
        text = _wa_422_text(exc) if exc.status == 422 else _error_text(exc.code)
        page = await _settings_page(request, settings, gate.user, brand_id, text)
        page.status_code = exc.status if exc.status in (403, 404, 409, 422) else 400
        return page
    flash = {"rotate": "wa_rotated", "disable": "wa_disabled"}.get(action, "wa_connected")
    target = f"{APP_PREFIX}/b/{brand_id}/settings?flash={flash}"
    return _with_session(settings, user, RedirectResponse(target, status_code=303))


@router.post("/b/{brand_id}/settings/whatsapp", response_class=HTMLResponse)
async def whatsapp_connect(request: Request, brand_id: UUID) -> Response:
    """Connect (no account) or reconnect in place (``POST …/account`` answers 200 for a
    disabled number — A-2 of SPRINT-10); the secrets are never echoed back."""
    return await _wa_action(request, brand_id, action="connect")


@router.post("/b/{brand_id}/settings/whatsapp/rotate", response_class=HTMLResponse)
async def whatsapp_rotate(request: Request, brand_id: UUID) -> Response:
    return await _wa_action(request, brand_id, action="rotate")


@router.post("/b/{brand_id}/settings/whatsapp/disable", response_class=HTMLResponse)
async def whatsapp_disable(request: Request, brand_id: UUID) -> Response:
    return await _wa_action(request, brand_id, action="disable")


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


@router.post("/b/{brand_id}/settings/export")
async def export_brand(
    request: Request, brand_id: UUID, csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None
) -> Response:
    """SPRINT-13 C-1 (D-081): request the export, then stream the ZIP back through the
    ApiClient (D-068) — the browser never talks to ``/api`` directly."""
    path = f"/brands/{brand_id}/export"
    gate, flash, created = await _action(request, csrf, "POST", path, None)
    if gate.response is not None:
        return gate.response
    if flash is not None or not isinstance(created, dict):
        return await _settings_page(request, get_settings(), gate.user, brand_id, flash)

    async def _download(api: ApiClient) -> Any:
        return await api.request("GET", f"{path}/{created['export_id']}")

    try:
        user, raw = await _call(request, get_settings(), gate.user, _download)
    except ApiError as exc:
        return _api_failure(request, get_settings(), gate.user, exc)
    if raw.status_code != 200:
        return await _settings_page(
            request, get_settings(), user, brand_id, _error_text("generic")
        )
    response = Response(
        raw.content,
        media_type="application/zip",
        headers={
            "Content-Disposition": raw.headers.get("content-disposition", "attachment"),
            "X-Export-SHA256": raw.headers.get("x-export-sha256", ""),
        },
    )
    return _with_session(get_settings(), user, response)


@router.post("/b/{brand_id}/settings/delete", response_class=HTMLResponse)
async def delete_brand(
    request: Request,
    brand_id: UUID,
    confirm: Annotated[str, Form()] = "",
    csrf: Annotated[str | None, Form(alias=CSRF_FIELD)] = None,
) -> Response:
    """SPRINT-13 C-2 (D-081): ``DELETE /brands/{id}`` with the typed confirmation; on 204
    the brand is gone ⇒ ``HX-Redirect: /app`` (htmx) or a 303 to ``/app``."""
    path = f"/brands/{brand_id}"
    gate, flash, _ = await _action(request, csrf, "DELETE", path, {"confirm": confirm})
    if gate.response is not None:
        return gate.response
    if flash is not None:
        return await _settings_page(request, get_settings(), gate.user, brand_id, flash)
    target = f"{APP_PREFIX}/"
    if _is_partial(request):
        response: Response = Response(status_code=200, headers={"HX-Redirect": target})
    else:
        response = RedirectResponse(target, status_code=303)
    return _with_session(get_settings(), gate.user, response)


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
        # SPRINT-13.1 F-5: admin-scoped reads — a platform admin is rarely a *member* of the
        # brand, so the tenant routes answered 404 (S13-2). ``/admin/brands/{id}`` carries
        # ``suspended``; the WA summary carries the account ``id`` the caps form posts back.
        ctx["wa"] = await _optional(api, f"/admin/brands/{brand_id}/whatsapp/summary")
        ctx["brand"] = await _optional(api, f"/admin/brands/{brand_id}")
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
