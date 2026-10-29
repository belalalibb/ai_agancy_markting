"""Async client for the Meta Graph API (Facebook Login + Pages + Instagram Business).

Design notes
------------
* Pure transport layer — no persistence, no encryption (those live in the service).
* All calls go through :meth:`MetaGraphClient._request`, which centralises:
  - timeout handling,
  - bounded exponential back-off for transient / throttled responses,
  - mapping of Graph API error payloads to typed exceptions.
* An ``httpx.AsyncClient`` may be injected (tests inject a mocked transport).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlencode

import httpx

from src.core.config import Settings, get_settings
from src.modules.social_connect.exceptions import (
    MetaGraphError,
    MetaNetworkError,
    MetaRateLimitError,
    MetaTransientError,
    map_graph_error,
)
from src.modules.social_connect.schemas import (
    MetaDebugTokenInfo,
    MetaPageInfo,
    MetaTokenResponse,
)

PAGE_FIELDS = "id,name,category,tasks,access_token,instagram_business_account{id,username}"

SleepFn = Callable[[float], Awaitable[None]]


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
        """Authorization code → short-lived token → long-lived token (~60 days)."""
        short = await self._request(
            "GET",
            "/oauth/access_token",
            params={
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
            "GET",
            "/oauth/access_token",
            params={
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
        app_secret = self._settings.meta_app_secret.get_secret_value()
        app_token = f"{self._settings.meta_app_id}|{app_secret}"
        payload = await self._request(
            "GET",
            "/debug_token",
            params={"input_token": input_token, "access_token": app_token},
        )
        data = payload.get("data") or {}
        scopes = data.get("scopes") or []
        return MetaDebugTokenInfo(
            app_id=str(data.get("app_id", "")),
            is_valid=bool(data.get("is_valid", False)),
            user_id=str(data["user_id"]) if data.get("user_id") is not None else None,
            scopes=[str(s) for s in scopes],
            expires_at=int(data["expires_at"]) if data.get("expires_at") is not None else None,
        )

    # ------------------------------------------------------------- Pages ----
    async def get_user_pages(self, user_access_token: str) -> list[MetaPageInfo]:
        """``GET /me/accounts`` — follows cursor pagination until exhausted."""
        pages: list[MetaPageInfo] = []
        params: dict[str, Any] = {
            "access_token": user_access_token,
            "appsecret_proof": self.appsecret_proof(user_access_token),
            "fields": PAGE_FIELDS,
            "limit": 100,
        }
        next_url: str | None = None
        while True:
            payload = (
                await self._request("GET", "/me/accounts", params=params)
                if next_url is None
                else await self._request_absolute("GET", next_url)
            )
            for node in payload.get("data") or []:
                if isinstance(node, dict):
                    pages.append(MetaPageInfo.from_graph(node))
            next_url = ((payload.get("paging") or {}).get("next")) or None
            if not next_url:
                break
        return pages

    # ---------------------------------------------------------- transport ---
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        return await self._request_absolute(method, url, params=params, json=json)

    async def _request_absolute(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                response = await self._http.request(method, url, params=params, json=json)
            except httpx.TimeoutException as exc:
                if attempt < self._max_retries:
                    await self._backoff(attempt)
                    attempt += 1
                    continue
                raise MetaNetworkError(f"Meta Graph API timeout: {exc}") from exc
            except httpx.HTTPError as exc:
                raise MetaNetworkError(f"Meta Graph API network error: {exc}") from exc

            payload = _safe_json(response)
            if response.is_success and "error" not in payload:
                return payload

            error = map_graph_error(
                payload,
                response.status_code,
                retry_after_seconds=_retry_after(response),
            )
            retryable = isinstance(error, MetaRateLimitError | MetaTransientError)
            if retryable and attempt < self._max_retries:
                hint = error.retry_after_seconds if isinstance(error, MetaRateLimitError) else None
                await self._backoff(attempt, hint=hint)
                attempt += 1
                continue
            raise error

    async def _backoff(self, attempt: int, hint: float | None = None) -> None:
        delay = hint if hint is not None else min(0.5 * (2**attempt), 8.0)
        await self._sleep(delay)


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


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise MetaGraphError(
            f"Malformed Graph API response: missing '{key}'.",
            http_status=200,
            raw=payload,
        )
    return value
