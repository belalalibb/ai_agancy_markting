"""Kill-switch guards (SPRINT-13 A-2) — two FastAPI dependencies mounted by ``src/main.py``.

* :func:`platform_readonly_guard` (on the whole ``/api/v1`` router): ``platform_readonly``
  ⇒ every ``POST/PUT/PATCH/DELETE`` answers **503 ``platform_readonly``** +
  ``Retry-After: 300`` **except** ``/api/v1/auth/*`` and ``/api/v1/admin/*`` (so the switch
  can be lifted). The flag comes through the 30-second in-process cache; the DB row wins and
  ``Settings`` is the fallback, so nothing changes until an admin flips the key.
* :func:`require_brand_not_suspended` (on every brand-scoped router): a write on a brand
  with ``suspended_at IS NOT NULL`` ⇒ **403 ``brand_suspended``**; reads stay allowed and an
  unknown brand falls through to the handler's own 404.
* :func:`feature_flag` / :func:`require_feature`: the ``feature_pulse`` /
  ``feature_lead_loop`` keys as request dependencies — a DB row set to ``false`` switches the
  feature **off** without a redeploy; with no row the ``Settings`` value stands.

Both read through :func:`get_ops_repository`; the DB-less suite overrides it with a fake.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.database import get_db_session
from src.modules.ops.domain import FlagKey
from src.modules.ops.exceptions import BrandSuspendedError, PlatformReadonlyError
from src.modules.ops.infrastructure.postgres_repository import PgOpsRepository
from src.modules.ops.service import PLATFORM_FLAGS, PlatformFlags

logger = logging.getLogger(__name__)

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
READONLY_RETRY_AFTER = "300"
EXEMPT_PREFIXES = ("/auth/", "/admin/")


def get_ops_repository(session: Annotated[AsyncSession, Depends(get_db_session)]) -> Any:
    return PgOpsRepository(session)


OpsRepo = Annotated[Any, Depends(get_ops_repository)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _exempt(path: str, prefix: str) -> bool:
    rest = path[len(prefix) :] if path.startswith(prefix) else path
    return any(rest.startswith(p) for p in EXEMPT_PREFIXES)


async def flag_active(
    repo: Any, settings: Settings, key: FlagKey, *, flags: PlatformFlags = PLATFORM_FLAGS
) -> bool:
    try:
        await flags.refresh(repo.get_flags)
    except Exception:  # noqa: BLE001 - a DB blip must never become a global 503
        logger.warning("ops: platform_flags unreadable; using Settings fallback")
        flags.mark_loaded()
    return flags.resolve(key, settings).value


async def readonly_active(
    repo: Any, settings: Settings, *, flags: PlatformFlags = PLATFORM_FLAGS
) -> bool:
    return await flag_active(repo, settings, "platform_readonly", flags=flags)


def feature_flag(key: FlagKey) -> Callable[..., Awaitable[bool]]:
    """Dependency factory: ``True`` when the feature is on (DB row first, ``Settings`` next)."""

    async def enabled(repo: OpsRepo, settings: SettingsDep) -> bool:
        return await flag_active(repo, settings, key)

    return enabled


def require_feature(key: FlagKey, *, error: str) -> Callable[..., Awaitable[None]]:
    """Router-level dependency (mounted by ``src/main.py``): feature off ⇒ **404** ``error``,
    the same answer the feature's own ``Settings`` check gives."""

    async def guard(repo: OpsRepo, settings: SettingsDep) -> None:
        if not await flag_active(repo, settings, key):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail={"error": error, "message": f"{key} is off"}
            )

    return guard


async def platform_readonly_guard(request: Request, repo: OpsRepo, settings: SettingsDep) -> None:
    if request.method not in WRITE_METHODS or _exempt(request.url.path, settings.api_v1_prefix):
        return
    if await readonly_active(repo, settings):
        exc = PlatformReadonlyError("platform is in read-only mode; retry later")
        raise HTTPException(
            exc.status,
            detail={"error": exc.error, "message": exc.message},
            headers={"Retry-After": READONLY_RETRY_AFTER},
        )


async def require_brand_not_suspended(request: Request, repo: OpsRepo) -> None:
    if request.method not in WRITE_METHODS:
        return
    raw = request.path_params.get("brand_id")
    if raw is None:
        return
    try:
        brand_id = UUID(str(raw))
    except ValueError:
        return
    state = await repo.brand_suspension(brand_id)
    if state is not None and state.suspended_at is not None:
        exc = BrandSuspendedError("this brand is suspended; writes are refused")
        raise HTTPException(exc.status, detail={"error": exc.error, "message": exc.message})


__all__ = [
    "EXEMPT_PREFIXES",
    "READONLY_RETRY_AFTER",
    "WRITE_METHODS",
    "feature_flag",
    "flag_active",
    "get_ops_repository",
    "platform_readonly_guard",
    "readonly_active",
    "require_brand_not_suspended",
    "require_feature",
]
