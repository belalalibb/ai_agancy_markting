"""Storage contract for connected accounts (ARCHITECTURE_LOCK D-013).

The service layer depends only on this ``Protocol``; the PostgreSQL implementation
lives in ``infrastructure/postgres_repository.py``. Every operation is scoped by
``tenant_id`` (tenant isolation — F1 / D-014).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from src.modules.social_connect.domain.models import AccountStatus, ConnectedAccount


@runtime_checkable
class ConnectedAccountRepository(Protocol):
    async def upsert(self, account: ConnectedAccount) -> ConnectedAccount:
        """Insert or update on ``(tenant_id, provider, provider_user_id)``.

        Pages are replaced wholesale (old rows deleted, new rows inserted) in the
        same transaction. Returns the persisted aggregate (with the stored ``id``).
        """
        ...

    async def get(self, tenant_id: str, account_id: UUID) -> ConnectedAccount | None: ...

    async def list_by_tenant(self, tenant_id: str) -> list[ConnectedAccount]: ...

    async def set_status(self, tenant_id: str, account_id: UUID, status: AccountStatus) -> bool:
        """Return ``True`` when a row was updated, ``False`` when not found."""
        ...

    async def delete(self, tenant_id: str, account_id: UUID) -> bool:
        """Return ``True`` when a row was deleted (pages cascade), ``False`` when not found."""
        ...
