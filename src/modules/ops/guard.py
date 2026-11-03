"""A-2 request guard — ``platform_readonly`` ⇒ 503 and ``brands.suspended_at`` ⇒ 403.

Pure-ASGI middleware (no ``BaseHTTPMiddleware`` — streaming bodies stay untouched):

* ``platform_readonly = true`` ⇒ every ``POST/PUT/PATCH/DELETE`` under ``/api/v1`` answers
  **503 ``platform_readonly``** + ``Retry-After: 300`` — **except** ``/api/v1/auth/*`` and
  ``/api/v1/admin/*`` (so the switch can be lifted). The ``/app`` UI is not touched here: it
  calls the API in-process and surfaces the translated 503 itself.
* a write under ``/api/v1/brands/{id}/…`` on a brand with ``suspended_at IS NOT NULL`` ⇒
  **403 ``brand_suspended``**; reads stay (criterion 9). Unknown brand ⇒ pass through (the
  route answers its own 404). ``DELETE /brands/{id}`` itself is exempt (C-2 must still work).

The flag source is ``app.state.flag_source`` (:class:`FlagSource`) — ``src/main.py`` installs
:class:`PgFlagSource` (30 s cache, one independent session per miss — D-072; a DB failure is
cached as "no flags" for 30 s and the request passes: the guard fails **open**, it never
closes the platform). The fake-wired unit app installs an in-memory source; no source ⇒ the
guard is inert.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.config import Settings
from src.core.database import get_session_factory, independent_session
from src.modules.ops.flags import (
    CACHE_SECONDS,
    READONLY_RETRY_AFTER,
    FlagCache,
    PgFlagRepository,
    effective,
)

logger = logging.getLogger(__name__)

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_BRAND_PATH = re.compile(r"^/api/v1/brands/([0-9a-fA-F-]{36})(/.*)?$")
SessionFactory = Callable[[], async_sessionmaker[AsyncSession]]


class FlagSource(Protocol):
    async def flags(self) -> dict[str, bool]: ...

    async def suspended_at(self, brand_id: uuid.UUID) -> datetime | None | bool: ...

    def invalidate(self) -> None: ...


class PgFlagSource:
    """:class:`FlagCache` over independent sessions; failures are negative-cached."""

    def __init__(
        self, cache: FlagCache, session_factory: SessionFactory = get_session_factory
    ) -> None:
        self._cache = cache
        self._factory = session_factory
        self._failed_at: float | None = None

    def invalidate(self) -> None:
        self._failed_at = None
        self._cache.invalidate()

    def _degraded(self) -> bool:
        return self._failed_at is not None and (time.monotonic() - self._failed_at) < CACHE_SECONDS

    async def flags(self) -> dict[str, bool]:
        if self._degraded():
            return {}
        try:
            async with independent_session(self._factory()) as session:
                return await self._cache.flags(PgFlagRepository(session))
        except Exception:  # noqa: BLE001 - fail open (see module docstring)
            self._failed_at = time.monotonic()
            logger.exception("ops guard: platform_flags unavailable — guard inert for 30s")
            return {}

    async def suspended_at(self, brand_id: uuid.UUID) -> datetime | None | bool:
        if self._degraded():
            return None
        try:
            async with independent_session(self._factory()) as session:
                return await self._cache.suspended_at(PgFlagRepository(session), brand_id)
        except Exception:  # noqa: BLE001
            self._failed_at = time.monotonic()
            logger.exception("ops guard: brands.suspended_at unavailable — passing through")
            return None


def _json(status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> tuple:
    raw = json.dumps(body).encode()
    hdrs = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]
    for k, v in (headers or {}).items():
        hdrs.append((k.lower().encode(), v.encode()))
    return status, hdrs, raw


READONLY_RESPONSE = _json(
    503,
    {"detail": {"error": "platform_readonly", "message": "platform is temporarily read-only"}},
    {"Retry-After": str(READONLY_RETRY_AFTER)},
)
SUSPENDED_RESPONSE = _json(
    403, {"detail": {"error": "brand_suspended", "message": "this brand is suspended"}}
)


def brand_write_target(method: str, path: str) -> uuid.UUID | None:
    """The brand a write addresses, or ``None`` (not a brand write / brand delete itself)."""
    if method not in WRITE_METHODS:
        return None
    match = _BRAND_PATH.match(path)
    if match is None:
        return None
    if method == "DELETE" and not match.group(2):
        return None  # C-2: deleting a suspended brand must remain possible
    try:
        return uuid.UUID(match.group(1))
    except ValueError:
        return None


def readonly_applies(method: str, path: str, prefix: str = "/api/v1") -> bool:
    if method not in WRITE_METHODS or not path.startswith(prefix + "/"):
        return False
    tail = path[len(prefix) :]
    return not (tail.startswith("/auth/") or tail.startswith("/admin/"))


async def decide(
    source: FlagSource | None, settings: Settings, method: str, path: str
) -> tuple | None:
    """``None`` ⇒ let the request through; else the canned ``(status, headers, body)``."""
    if source is None or not readonly_applies(method, path, settings.api_v1_prefix):
        return None
    if effective(await source.flags(), settings, "platform_readonly"):
        return READONLY_RESPONSE
    brand_id = brand_write_target(method, path)
    if brand_id is not None and isinstance(await source.suspended_at(brand_id), datetime):
        return SUSPENDED_RESPONSE
    return None


class KillSwitchGuard:
    """ASGI middleware; reads ``scope["app"].state.flag_source`` on every write."""

    def __init__(self, app: Any, *, settings: Settings) -> None:
        self._app = app
        self._settings = settings

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        source = getattr(getattr(scope.get("app"), "state", None), "flag_source", None)
        verdict = await decide(source, self._settings, scope["method"], scope["path"])
        if verdict is None:
            await self._app(scope, receive, send)
            return
        status, headers, body = verdict
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


__all__ = [
    "READONLY_RESPONSE",
    "SUSPENDED_RESPONSE",
    "WRITE_METHODS",
    "FlagSource",
    "KillSwitchGuard",
    "PgFlagSource",
    "brand_write_target",
    "decide",
    "readonly_applies",
]
