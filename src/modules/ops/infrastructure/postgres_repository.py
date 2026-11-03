"""PostgreSQL ``ops`` repository — owns ``admin_actions`` / ``platform_flags``; everything
else is SQL text over other modules' tables (D-016, same discipline as ``pulse``).

Every write runs on the **request session** so the audit row and the act it records commit
or roll back together (A-1: a failed act leaves zero rows).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Index, String, select, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base
from src.modules.ops.domain import (
    ADMIN_ACTIONS_MAX,
    REQUEUED_BY_ADMIN,
    AdminAction,
    BrandSuspension,
    DeadLetterJob,
    PlatformFlag,
)


class AdminActionRow(Base):
    __tablename__ = "admin_actions"
    __table_args__ = (
        Index("ix_admin_actions_target_at", "target_kind", "target_id", text("at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    before: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    after: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class PlatformFlagRow(Base):
    __tablename__ = "platform_flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


# ------------------------------------------------------------------ SQL text ----
_BRAND_SUSPENSION = text("SELECT suspended_at FROM brands WHERE id = :brand_id")
_SET_SUSPENDED = text("UPDATE brands SET suspended_at = :at WHERE id = :brand_id")
_DEAD_LETTER = text(
    "SELECT id, item_id, brand_id, channel, last_error_class, attempt_count, max_attempts, "
    "updated_at FROM publish_jobs WHERE status = 'failed' ORDER BY updated_at DESC LIMIT :limit"
)
_JOB = text(
    "SELECT id, item_id, brand_id, channel, last_error_class, attempt_count, max_attempts, "
    "updated_at, status, revision_id FROM publish_jobs WHERE id = :job_id"
)
_REQUEUE_JOB = text(
    "UPDATE publish_jobs SET status = 'queued', attempt_count = 0, next_attempt_at = :now, "
    "leased_until = NULL, lease_owner = NULL, updated_at = :now "
    "WHERE id = :job_id AND status = 'failed'"
)
_ITEM_CAS = text(
    "UPDATE content_items SET status = 'scheduled', scheduled_for = :now, published_at = NULL, "
    "updated_at = :now WHERE id = :item_id AND status = 'publish_failed'"
)
_REQUEUE_EVENT = text(
    "INSERT INTO approval_events (id, item_id, revision_id, action, actor_tenant_id, "
    "actor_user_id, note, created_at) VALUES (:id, :item_id, :revision_id, :action, "
    ":tenant_id, :user_id, NULL, :now)"
)
_ADMIN_TENANT = text("SELECT tenant_id FROM users WHERE id = :user_id")
_ADMIN_ALERTS = text(
    "SELECT id, brand_id, kind, severity, payload, created_at, acknowledged_at "
    "FROM pulse_alerts WHERE kind = :kind ORDER BY created_at DESC LIMIT :limit"
)


class PgOpsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- A-1 audit -------------------------------------------------------------------------
    async def add_action(self, action: AdminAction) -> AdminAction:
        self._session.add(
            AdminActionRow(
                id=action.id,
                actor_user_id=action.actor_user_id,
                action=action.action,
                target_kind=action.target_kind,
                target_id=action.target_id,
                before=dict(action.before),
                after=dict(action.after),
                at=action.at,
            )
        )
        await self._session.flush()
        return action

    async def list_actions(
        self, *, target_kind: str | None, target_id: str | None, limit: int
    ) -> list[AdminAction]:
        stmt = select(AdminActionRow)
        if target_kind:
            stmt = stmt.where(AdminActionRow.target_kind == target_kind)
        if target_id:
            stmt = stmt.where(AdminActionRow.target_id == target_id)
        stmt = stmt.order_by(AdminActionRow.at.desc(), AdminActionRow.id.desc()).limit(
            max(1, min(limit, ADMIN_ACTIONS_MAX))
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            AdminAction(
                id=r.id,
                actor_user_id=r.actor_user_id,
                action=r.action,
                target_kind=r.target_kind,
                target_id=r.target_id,
                before=dict(r.before or {}),
                after=dict(r.after or {}),
                at=r.at,
            )
            for r in rows
        ]

    # --- A-2 flags + suspension ------------------------------------------------------------
    async def get_flags(self) -> dict[str, PlatformFlag]:
        rows = (await self._session.execute(select(PlatformFlagRow))).scalars().all()
        return {
            r.key: PlatformFlag(key=r.key, value=r.value, updated_at=r.updated_at)  # type: ignore[arg-type]
            for r in rows
        }

    async def set_flag(self, key: str, value: bool, *, now: datetime) -> PlatformFlag:
        row = await self._session.get(PlatformFlagRow, key)
        if row is None:
            row = PlatformFlagRow(key=key, value=value, updated_at=now)
            self._session.add(row)
        else:
            row.value, row.updated_at = value, now
        await self._session.flush()
        return PlatformFlag(key=key, value=value, updated_at=now)  # type: ignore[arg-type]

    async def brand_suspension(self, brand_id: uuid.UUID) -> BrandSuspension | None:
        row = (await self._session.execute(_BRAND_SUSPENSION, {"brand_id": brand_id})).first()
        return None if row is None else BrandSuspension(brand_id=brand_id, suspended_at=row[0])

    async def set_suspended(self, brand_id: uuid.UUID, at: datetime | None) -> bool:
        result = await self._session.execute(_SET_SUSPENDED, {"brand_id": brand_id, "at": at})
        return (result.rowcount or 0) == 1

    # --- A-3 dead letter ------------------------------------------------------------------
    async def dead_letter(self, *, limit: int) -> list[DeadLetterJob]:
        rows = await self._session.execute(_DEAD_LETTER, {"limit": max(1, min(limit, 200))})
        return [_dead_letter(r) for r in rows.all()]

    async def job_status(self, job_id: uuid.UUID) -> tuple[DeadLetterJob, str, uuid.UUID] | None:
        row = (await self._session.execute(_JOB, {"job_id": job_id})).first()
        return None if row is None else (_dead_letter(row), str(row[8]), row[9])

    async def requeue(
        self,
        job: DeadLetterJob,
        *,
        revision_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        now: datetime,
    ) -> bool:
        """CAS on the job (``failed ⇒ queued``) and on the item (``publish_failed ⇒
        scheduled``) + the D-055 ``requeued_by_admin`` event; ``False`` if the job moved."""
        moved = await self._session.execute(_REQUEUE_JOB, {"job_id": job.job_id, "now": now})
        if (moved.rowcount or 0) != 1:
            return False
        await self._session.execute(_ITEM_CAS, {"item_id": job.item_id, "now": now})
        tenant = (await self._session.execute(_ADMIN_TENANT, {"user_id": actor_user_id})).first()
        await self._session.execute(
            _REQUEUE_EVENT,
            {
                "id": uuid.uuid4(),
                "item_id": job.item_id,
                "revision_id": revision_id,
                "action": REQUEUED_BY_ADMIN,
                "tenant_id": tenant[0] if tenant else None,
                "user_id": actor_user_id,
                "now": now,
            },
        )
        return True

    # --- A-4 admin alerts ------------------------------------------------------------------
    async def admin_alerts(self, *, kind: str, limit: int) -> list[dict[str, Any]]:
        rows = await self._session.execute(
            _ADMIN_ALERTS, {"kind": kind, "limit": max(1, min(limit, 200))}
        )
        return [
            {
                "id": str(r[0]),
                "brand_id": str(r[1]),
                "kind": r[2],
                "severity": r[3],
                "payload": dict(r[4] or {}),
                "created_at": r[5],
                "acknowledged_at": r[6],
            }
            for r in rows.all()
        ]


def _dead_letter(row: Any) -> DeadLetterJob:
    return DeadLetterJob(
        job_id=row[0],
        item_id=row[1],
        brand_id=row[2],
        channel=row[3],
        error_class=row[4],
        attempt_count=row[5],
        max_attempts=row[6],
        failed_at=row[7],
    )


__all__ = ["AdminActionRow", "PgOpsRepository", "PlatformFlagRow"]
