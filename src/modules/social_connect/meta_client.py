"""Async client for the Meta Graph API (Facebook Login + Pages + Instagram Business).

Design notes
------------
* Pure transport layer — no persistence, no encryption (those live in the service).
* All calls go through :meth:`MetaGraphClient._request`, which centralises:
  - timeout handling,
  - bounded exponential back-off for *transient* failures only (rate limits are
    surfaced immediately — Meta extends the block if you keep calling),
  - mapping of Graph API error payloads to typed exceptions.
* An ``httpx.AsyncClient`` may be injected (tests inject a mocked transport).

Secrets never travel in the URL (SPRINT-04.1 H2): user/app tokens go in
``Authorization: Bearer``; ``client_secret`` / ``input_token`` go in a form body (``POST``).
Only the non-reusable ``appsecret_proof`` is allowed as a query parameter. ``paging.next``
URLs are host-checked (H4) and stripped of any ``access_token`` before being followed.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Any, Self
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from src.core.config import Settings, get_settings
from src.modules.social_connect.exceptions import (
    MetaGraphError,
    MetaNetworkError,
    MetaTransientError,
    map_graph_error,
)
from src.modules.social_connect.schemas import (
    MetaDebugTokenInfo,
    MetaPageInfo,
    MetaTokenResponse,
    MetaUserInfo,
)

PAGE_FIELDS = (
    "id,name,category,tasks,access_token,"
    "instagram_business_account{id,username,name,profile_picture_url}"
)

# F8: hard upper bound on cursor-pagination hops for ``/me/accounts`` (100 Pages per
# hop → 5 000 Pages). A ``paging.next`` that never ends must not loop forever.
MAX_PAGINATION_REQUESTS: int = 50

# H2: query parameters that must never be sent (Meta echoes them in ``paging.next``).
# SPRINT-05 A-0/B5: ``input_token`` / ``method`` belong to the ``/debug_token`` form body only.
_URL_SECRET_PARAMS: frozenset[str] = frozenset(
    {"access_token", "appsecret_proof", "input_token", "method"}
)

# SPRINT-05 B3/B4: operational reads of a selected Page / its Instagram account.
POST_FIELDS = "id,message,created_time,permalink_url,status_type,full_picture,is_published"
IG_MEDIA_FIELDS = (
    "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count"
)
DEFAULT_READ_LIMIT: int = 25
MAX_READ_LIMIT: int = 100

SleepFn = Callable[[float], Awaitable[None]]

logger = logging.getLogger(__name__)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class MetaGraphClient:
    """Thin, typed wrapper over the Graph API endpoints needed for onboarding."""

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
        *,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._settings.meta_http_timeout_seconds),
            headers={"Accept": "application/json", "User-Agent": "ai-growth-os/0.1"},
        )
        self._sleep = sleep
        self._max_retries = max(0, self._settings.meta_http_max_retries)

    # --------------------------------------------------------- lifecycle ----
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    @property
    def is_closed(self) -> bool:
        return self._http.is_closed

    # ------------------------------------------------------------ helpers ---
    @property
    def base_url(self) -> str:
        return self._settings.meta_graph_url

    def build_authorize_url(
        self,
        redirect_uri: str,
        state: str | None = None,
        scopes: list[str] | None = None,
    ) -> tuple[str, str]:
        """Return ``(authorize_url, state)`` for the Facebook Login dialog."""
        state = state or secrets.token_urlsafe(32)
        params = {
            "client_id": self._settings.meta_app_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "scope": ",".join(scopes or self._settings.meta_scopes_list),
        }
        dialog = self._settings.meta_oauth_authorize_url.format(
            version=self._settings.meta_graph_api_version
        )
        return f"{dialog}?{urlencode(params)}", state

    def appsecret_proof(self, access_token: str) -> str:
        """HMAC-SHA256 proof required when *Require App Secret* is enabled."""
        secret = self._settings.meta_app_secret.get_secret_value().encode("utf-8")
        return hmac.new(secret, access_token.encode("utf-8"), hashlib.sha256).hexdigest()

    # ------------------------------------------------------------- OAuth ----
    async def exchange_code_for_token(self, code: str, redirect_uri: str) -> MetaTokenResponse:
        """Authorization code → short-lived token → long-lived token (~60 days).

        H2: ``POST`` with a form body — ``client_secret`` and ``code`` never hit the URL.
        """
        short = await self._request(
            "POST",
            "/oauth/access_token",
            data={
                "client_id": self._settings.meta_app_id,
                "client_secret": self._settings.meta_app_secret.get_secret_value(),
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        short_token = _require_str(short, "access_token")
        long_resp = await self.exchange_for_long_lived_token(short_token)
        return MetaTokenResponse(
            short_lived_token=short_token,
            long_lived_token=long_resp.long_lived_token,
            token_type=long_resp.token_type,
            expires_in=long_resp.expires_in,
        )

    async def exchange_for_long_lived_token(self, short_lived_token: str) -> MetaTokenResponse:
        payload = await self._request(
            "POST",
            "/oauth/access_token",
            data={
                "grant_type": "fb_exchange_token",
                "client_id": self._settings.meta_app_id,
                "client_secret": self._settings.meta_app_secret.get_secret_value(),
                "fb_exchange_token": short_lived_token,
            },
        )
        return MetaTokenResponse(
            short_lived_token=None,
            long_lived_token=_require_str(payload, "access_token"),
            token_type=str(payload.get("token_type", "bearer")).lower(),  # type: ignore[arg-type]
            expires_in=int(payload.get("expires_in", 0) or 0),
        )

    async def debug_token(self, input_token: str) -> MetaDebugTokenInfo:
        """``/debug_token`` — validity, ``app_id``, ``user_id``, scopes and expiry of a token.

        Transport: HTTP ``POST`` with ``Authorization: Bearer {app_id}|{app_secret}`` and the
        form body ``input_token=…&method=GET``. ``/debug_token`` is a *read* endpoint, so the
        Graph method override ``method=GET`` (SPRINT-05 A-0) tells Meta to treat the POST as a
        GET — without it Meta answers code 100 / ``GraphMethodException``. Neither the app
        secret nor the inspected token ever appears in the request line (H2). No scope
        required beyond a valid app token; no pagination.
        """
        app_secret = self._settings.meta_app_secret.get_secret_value()
        app_token = f"{self._settings.meta_app_id}|{app_secret}"
        # H2: the app token (contains the app secret) travels as Bearer, the inspected
        # token in the form body — neither appears in the request line.
        payload = await self._request(
            "POST",
            "/debug_token",
            data={"input_token": input_token, "method": "GET"},
            headers=_bearer(app_token),
        )
        data = payload.get("data") or {}
        scopes = data.get("scopes") or []
        error = data.get("error") if isinstance(data.get("error"), dict) else {}
        return MetaDebugTokenInfo(
            app_id=str(data.get("app_id", "")),
            is_valid=bool(data.get("is_valid", False)),
            user_id=str(data["user_id"]) if data.get("user_id") is not None else None,
            scopes=[str(s) for s in scopes],
            expires_at=int(data["expires_at"]) if data.get("expires_at") is not None else None,
            error_code=_as_optional_int(error.get("code")),
        )

    async def get_me(self, user_access_token: str) -> MetaUserInfo:
        """``GET /me`` — the *trusted* identity of the token's owner (SPRINT-02.1 F2)."""
        payload = await self._request(
            "GET",
            "/me",
            params={
                "fields": "id,name",
                "appsecret_proof": self.appsecret_proof(user_access_token),
            },
            headers=_bearer(user_access_token),
        )
        name = payload.get("name")
        return MetaUserInfo(
            id=_require_str(payload, "id"),
            name=str(name) if isinstance(name, str) and name else None,
        )

    # ------------------------------------------------------------- Pages ----
    async def get_user_pages(self, user_access_token: str) -> list[MetaPageInfo]:
        """``GET /me/accounts`` — follows cursor pagination until exhausted."""
        pages: list[MetaPageInfo] = []
        proof = self.appsecret_proof(user_access_token)
        headers = _bearer(user_access_token)
        params: dict[str, Any] = {
            "appsecret_proof": proof,
            "fields": PAGE_FIELDS,
            "limit": 100,
        }
        next_url: str | None = None
        requests_made = 0
        while True:
            if requests_made >= MAX_PAGINATION_REQUESTS:
                raise MetaGraphError(
                    "Pagination exceeded MAX_PAGINATION_REQUESTS",
                    http_status=200,
                )
            payload = (
                await self._request("GET", "/me/accounts", params=params, headers=headers)
                if next_url is None
                else await self._request_absolute(
                    "GET", self._trusted_pagination_url(next_url, proof), headers=headers
                )
            )
            requests_made += 1
            for node in payload.get("data") or []:
                if isinstance(node, dict):
                    pages.append(MetaPageInfo.from_graph(node))
            next_url = ((payload.get("paging") or {}).get("next")) or None
            if not next_url:
                break
        return pages

    def _trusted_pagination_url(self, next_url: str, appsecret_proof: str) -> str:
        """H4 (SSRF) + H2: only ``https`` on the configured Graph host, secrets stripped.

        Meta echoes ``access_token`` (and ``appsecret_proof``) inside ``paging.next``; both
        are removed here — the token is re-sent as ``Authorization: Bearer`` and the proof
        re-appended explicitly (``httpx`` *replaces* a URL's query when ``params`` is given,
        so the cursor must stay inside the URL).
        """
        parsed = urlparse(next_url)
        expected_host = urlparse(self._settings.meta_graph_base_url).netloc
        if parsed.scheme != "https" or not parsed.netloc or parsed.netloc != expected_host:
            raise MetaGraphError("untrusted pagination url", http_status=200)
        query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key not in _URL_SECRET_PARAMS
        ]
        query.append(("appsecret_proof", appsecret_proof))
        return urlunparse(parsed._replace(query=urlencode(query)))

    # ---------------------------------------------------------- transport ---
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        return await self._request_absolute(method, url, params=params, data=data, headers=headers)

    async def _request_absolute(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                response = await self._http.request(
                    method, url, params=params, data=data, headers=headers
                )
            except httpx.TimeoutException as exc:
                if attempt < self._max_retries:
                    await self._backoff(attempt)
                    attempt += 1
                    continue
                # H2: ``str(exc)`` may embed the request URL — never forward it.
                logger.warning("Meta Graph API timeout: %s", type(exc).__name__)
                raise MetaNetworkError("Meta Graph API timeout") from exc
            except httpx.HTTPError as exc:
                logger.warning("Meta Graph API network error: %s", type(exc).__name__)
                raise MetaNetworkError("Meta Graph API network error") from exc

            payload = _safe_json(response)
            if response.is_success and "error" not in payload:
                return payload

            error = map_graph_error(
                payload,
                response.status_code,
                retry_after_seconds=_retry_after(response),
            )
            # C4: never retry on rate limiting — ``MetaRateLimitError`` is raised at once
            # (its ``retry_after_seconds`` is forwarded as a ``Retry-After`` header upstream).
            retryable = isinstance(error, MetaTransientError)
            if retryable and attempt < self._max_retries:
                await self._backoff(attempt)
                attempt += 1
                continue
            raise error

    async def _backoff(self, attempt: int) -> None:
        await self._sleep(min(0.5 * (2**attempt), 8.0))


# ------------------------------------------------------------------ utils ----
def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {"data": data}


def _retry_after(response: httpx.Response) -> float | None:
    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _as_optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise MetaGraphError(
            f"Malformed Graph API response: missing '{key}'.",
            http_status=200,
            raw=payload,
        )
    return value
