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

    async def is_active(self, session_id: UUID, now: datetime) -> bool:
        """Single query (JOIN users) used by ``CurrentUserFresh`` (H6):
        ``revoked_at IS NULL AND expires_at > now AND users.is_active``."""
        ...

    async def revoke(self, session_id: UUID, at: datetime, replaced_by: UUID | None = None) -> int:
        """Revoke if still active; returns the number of rows updated (0 or 1) — H7 relies
        on exactly one winner when two refreshes race on the same session."""
        ...

    async def link_replacement(self, session_id: UUID, replaced_by: UUID) -> None:
        """Audit link set *after* the atomic revoke (H7): the new session id is only known
        once it has been issued."""
        ...

    async def revoke_all_for_user(self, user_id: UUID, at: datetime) -> int:
        """Revoke every *active* session of the user; returns the number revoked."""
        ...
