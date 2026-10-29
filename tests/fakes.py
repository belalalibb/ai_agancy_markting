"""Test doubles that live only in ``tests/`` (no InMemory repository in ``src/``)."""

from __future__ import annotations

from uuid import UUID

from src.modules.social_connect.domain.models import AccountStatus, ConnectedAccount


class FakeConnectedAccountRepository:
    """Dict-backed stand-in honouring the ``ConnectedAccountRepository`` contract."""

    def __init__(self) -> None:
        self.rows: dict[UUID, ConnectedAccount] = {}
        self.upsert_calls = 0

    async def upsert(self, account: ConnectedAccount) -> ConnectedAccount:
        self.upsert_calls += 1
        for existing in self.rows.values():
            if (existing.tenant_id, existing.provider, existing.provider_user_id) == (
                account.tenant_id,
                account.provider,
                account.provider_user_id,
            ):
                merged = account.model_copy(update={"id": existing.id})
                self.rows[existing.id] = merged
                return merged
        self.rows[account.id] = account
        return account

    async def get(self, tenant_id: str, account_id: UUID) -> ConnectedAccount | None:
        row = self.rows.get(account_id)
        return row if row is not None and row.tenant_id == tenant_id else None

    async def list_by_tenant(self, tenant_id: str) -> list[ConnectedAccount]:
        return [row for row in self.rows.values() if row.tenant_id == tenant_id]

    async def set_status(self, tenant_id: str, account_id: UUID, status: AccountStatus) -> bool:
        row = await self.get(tenant_id, account_id)
        if row is None:
            return False
        self.rows[account_id] = row.model_copy(update={"status": status})
        return True

    async def delete(self, tenant_id: str, account_id: UUID) -> bool:
        row = await self.get(tenant_id, account_id)
        if row is None:
            return False
        del self.rows[account_id]
        return True
