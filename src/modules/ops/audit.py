"""``admin_actions`` hook for the *other* admin routers (SPRINT-13 A-1).

``AdminAudit`` is a request-scoped dependency over the **same** ``get_db_session`` the acting
router uses, so the audit row commits or rolls back with the act (a failed act ⇒ 0 rows).
``before``/``after`` must hold identifiers and counts only (D-069) — callers pass ids.
Removing ``ops`` = delete the three ``audit.record(...)`` lines in catalog / entitlements /
whatsapp admin routers plus the mounts in ``src/main.py`` (see REMOVABILITY.md).
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_session
from src.modules.ops.infrastructure.postgres_repository import PgOpsRepository
from src.modules.ops.service import OpsService


class AdminAudit:
    def __init__(self, service: OpsService) -> None:
        self._service = service

    async def record(
        self,
        *,
        actor_user_id: UUID,
        action: str,
        target_kind: str,
        target_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        await self._service.record(
            actor_user_id=actor_user_id,
            action=action,
            target_kind=target_kind,
            target_id=target_id,
            before=before,
            after=after,
        )


def get_admin_audit(session: Annotated[AsyncSession, Depends(get_db_session)]) -> AdminAudit:
    return AdminAudit(OpsService(PgOpsRepository(session)))


Audit = Annotated[AdminAudit, Depends(get_admin_audit)]

__all__ = ["AdminAudit", "Audit", "get_admin_audit"]
