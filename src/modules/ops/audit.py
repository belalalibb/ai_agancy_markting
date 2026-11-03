"""A-1 ``admin_actions`` — one audit row per administrative act, written in the **same**
transaction as the act (a failed act ⇒ no row; D-055). ``before``/``after`` carry ids and
counts only — never customer text (D-069).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import String, select, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base

MAX_LIST_LIMIT = 200


class AdminAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    actor_user_id: uuid.UUID | None  # ``None`` = system (retention sweep)
    action: str
    target_kind: str
    target_id: str
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class AdminActionRow(Base):
    __tablename__ = "admin_actions"

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


class AuditWriter(Protocol):
    async def record(self, action: AdminAction) -> AdminAction: ...

    async def list_actions(
        self, *, target_kind: str | None, target_id: str | None, limit: int
    ) -> list[AdminAction]: ...


def _to_domain(row: AdminActionRow) -> AdminAction:
    return AdminAction(
        id=row.id,
        actor_user_id=row.actor_user_id,
        action=row.action,
        target_kind=row.target_kind,
        target_id=row.target_id,
        before=dict(row.before or {}),
        after=dict(row.after or {}),
        at=row.at,
    )


class PgAuditWriter:
    """Writes through the *request* session — commit/rollback is the caller's (same tx)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, action: AdminAction) -> AdminAction:
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
            min(max(limit, 1), MAX_LIST_LIMIT)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(r) for r in rows]


__all__ = ["MAX_LIST_LIMIT", "AdminAction", "AdminActionRow", "AuditWriter", "PgAuditWriter"]
