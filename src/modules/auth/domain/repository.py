"""Storage contracts for auth (ARCHITECTURE_LOCK D-013 — Protocols only)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from src.modules.auth.domain.models import RefreshSession, User


@runtime_checkable
class UserRepository(Protocol):
    async def add(self, user: User) -> User: ...

    async def get(self, user_id: UUID) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None:
        """Case-insensitive lookup (``lower(email)``)."""
        ...

    async def update_password_hash(self, user_id: UUID, password_hash: str) -> None: ...

    async def touch_last_login(self, user_id: UUID, at: datetime) -> None: ...


@runtime_checkable
class RefreshSessionRepository(Protocol):
    async def add(self, session: RefreshSession) -> RefreshSession: ...

    async def get(self, session_id: UUID) -> RefreshSession | None: ...

    async def is_active(self, session_id: UUID) -> bool:
        """Single query used by ``CurrentUserFresh``: exists and ``revoked_at IS NULL``."""
        ...

    async def revoke(
        self, session_id: UUID, at: datetime, replaced_by: UUID | None = None
    ) -> None: ...

    async def revoke_all_for_user(self, user_id: UUID, at: datetime) -> int:
        """Revoke every *active* session of the user; returns the number revoked."""
        ...
