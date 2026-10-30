"""PostgreSQL implementations of the auth repositories (never commit — session owns the tx).

SPRINT-04.1 H4: ``PostgresUserRepository.add`` is ``INSERT … ON CONFLICT DO NOTHING RETURNING
id`` — a concurrent registration with the same e-mail (or tenant) raises ``EmailTakenError``
(409) instead of an ``IntegrityError`` 500, and the transaction stays usable.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.domain.models import RefreshSession, User
from src.modules.auth.exceptions import EmailTakenError
from src.modules.auth.infrastructure.orm import RefreshSessionRow, UserRow


class PostgresUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> User:
        stmt = (
            pg_insert(UserRow)
            .values(
                id=user.id,
                tenant_id=user.tenant_id,
                email=user.email,
                password_hash=user.password_hash,
                is_active=user.is_active,
            )
            .on_conflict_do_nothing()
            .returning(UserRow.id)
        )
        inserted = (await self._session.execute(stmt)).scalar_one_or_none()
        if inserted is None:
            raise EmailTakenError("email is already registered")
        row = await self._session.get(UserRow, inserted)
        if row is None:  # pragma: no cover - the row was just inserted in this transaction
            raise RuntimeError("inserted user vanished within the transaction")
        return _user(row)

    async def get(self, user_id: UUID) -> User | None:
        row = await self._session.get(UserRow, user_id)
        return _user(row) if row else None

    async def get_by_email(self, email: str) -> User | None:
        row = (
            await self._session.execute(
                select(UserRow).where(func.lower(UserRow.email) == email.strip().lower())
            )
        ).scalar_one_or_none()
        return _user(row) if row else None

    async def update_password_hash(self, user_id: UUID, password_hash: str) -> None:
        await self._session.execute(
            update(UserRow).where(UserRow.id == user_id).values(password_hash=password_hash)
        )

    async def touch_last_login(self, user_id: UUID, at: datetime) -> None:
        await self._session.execute(
            update(UserRow).where(UserRow.id == user_id).values(last_login_at=at)
        )


class PostgresRefreshSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, session: RefreshSession) -> RefreshSession:
        row = RefreshSessionRow(
            id=session.id,
            user_id=session.user_id,
            token_hash=session.token_hash,
            expires_at=session.expires_at,
            revoked_at=session.revoked_at,
            replaced_by=session.replaced_by,
            user_agent=session.user_agent,
            ip=session.ip,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return _refresh(row)

    async def get(self, session_id: UUID) -> RefreshSession | None:
        row = await self._session.get(RefreshSessionRow, session_id)
        return _refresh(row) if row else None

    async def is_active(self, session_id: UUID) -> bool:
        result = await self._session.execute(
            select(RefreshSessionRow.id).where(
                RefreshSessionRow.id == session_id, RefreshSessionRow.revoked_at.is_(None)
            )
        )
        return result.scalar_one_or_none() is not None

    async def revoke(
        self, session_id: UUID, at: datetime, replaced_by: UUID | None = None
    ) -> None:
        await self._session.execute(
            update(RefreshSessionRow)
            .where(RefreshSessionRow.id == session_id, RefreshSessionRow.revoked_at.is_(None))
            .values(revoked_at=at, replaced_by=replaced_by)
        )

    async def revoke_all_for_user(self, user_id: UUID, at: datetime) -> int:
        result = await self._session.execute(
            update(RefreshSessionRow)
            .where(RefreshSessionRow.user_id == user_id, RefreshSessionRow.revoked_at.is_(None))
            .values(revoked_at=at)
        )
        return int(result.rowcount or 0)


def _user(row: UserRow) -> User:
    return User(
        id=row.id,
        tenant_id=row.tenant_id,
        email=row.email,
        password_hash=row.password_hash,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_login_at=row.last_login_at,
    )


def _refresh(row: RefreshSessionRow) -> RefreshSession:
    return RefreshSession(
        id=row.id,
        user_id=row.user_id,
        token_hash=row.token_hash,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        replaced_by=row.replaced_by,
        user_agent=row.user_agent,
        ip=str(row.ip) if row.ip is not None else None,
        created_at=row.created_at,
    )
