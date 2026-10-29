"""PostgreSQL implementation of :class:`ConnectedAccountRepository` (SQLAlchemy async).

The repository never commits: the FastAPI dependency ``get_db_session`` owns the
transaction boundary, so a whole request (e.g. callback → upsert) is atomic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.social_connect.domain.models import (
    AccountStatus,
    ConnectedAccount,
    InstagramAsset,
    PageAsset,
    Provider,
)
from src.modules.social_connect.infrastructure.orm import (
    SocialConnectionPageRow,
    SocialConnectionRow,
)


class PostgresConnectedAccountRepository:
    """Tenant-scoped persistence for connected accounts and their pages."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------ writes ---
    async def upsert(self, account: ConnectedAccount) -> ConnectedAccount:
        now = datetime.now(tz=UTC)
        stmt = (
            pg_insert(SocialConnectionRow)
            .values(
                id=account.id,
                tenant_id=account.tenant_id,
                provider=account.provider.value,
                provider_user_id=account.provider_user_id,
                provider_user_name=account.provider_user_name,
                encrypted_user_token=account.encrypted_user_token,
                token_expires_at=account.token_expires_at,
                granted_scopes=list(account.granted_scopes),
                status=account.status.value,
                connected_at=account.connected_at,
                last_synced_at=account.last_synced_at,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_social_connections_identity",
                set_={
                    "provider_user_name": account.provider_user_name,
                    "encrypted_user_token": account.encrypted_user_token,
                    "token_expires_at": account.token_expires_at,
                    "granted_scopes": list(account.granted_scopes),
                    "status": account.status.value,
                    "last_synced_at": account.last_synced_at,
                    "updated_at": now,
                },
            )
            .returning(SocialConnectionRow.id)
        )
        connection_id: UUID = (await self._session.execute(stmt)).scalar_one()

        # Replace pages wholesale inside the same transaction.
        await self._session.execute(
            delete(SocialConnectionPageRow).where(
                SocialConnectionPageRow.connection_id == connection_id
            )
        )
        if account.pages:
            await self._session.execute(
                pg_insert(SocialConnectionPageRow).values(
                    [_page_values(connection_id, page) for page in account.pages]
                )
            )
        await self._session.flush()

        stored = await self.get(account.tenant_id, connection_id)
        if stored is None:  # pragma: no cover - defensive; the row was just written
            msg = "Upserted connection vanished within the transaction."
            raise RuntimeError(msg)
        return stored

    async def set_status(self, tenant_id: str, account_id: UUID, status: AccountStatus) -> bool:
        result = await self._session.execute(
            update(SocialConnectionRow)
            .where(
                SocialConnectionRow.tenant_id == tenant_id,
                SocialConnectionRow.id == account_id,
            )
            .values(status=status.value, updated_at=datetime.now(tz=UTC))
        )
        return (result.rowcount or 0) > 0

    async def delete(self, tenant_id: str, account_id: UUID) -> bool:
        result = await self._session.execute(
            delete(SocialConnectionRow).where(
                SocialConnectionRow.tenant_id == tenant_id,
                SocialConnectionRow.id == account_id,
            )
        )
        return (result.rowcount or 0) > 0

    # ------------------------------------------------------------- reads ---
    async def get(self, tenant_id: str, account_id: UUID) -> ConnectedAccount | None:
        row = (
            await self._session.execute(
                select(SocialConnectionRow)
                .where(
                    SocialConnectionRow.tenant_id == tenant_id,
                    SocialConnectionRow.id == account_id,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        return _to_domain(row) if row is not None else None

    async def list_by_tenant(self, tenant_id: str) -> list[ConnectedAccount]:
        result = await self._session.execute(
            select(SocialConnectionRow)
            .where(SocialConnectionRow.tenant_id == tenant_id)
            .order_by(SocialConnectionRow.connected_at, SocialConnectionRow.id)
            .execution_options(populate_existing=True)
        )
        return [_to_domain(row) for row in result.scalars().all()]


# ------------------------------------------------------------------ mappers --
def _page_values(connection_id: UUID, page: PageAsset) -> dict[str, object]:
    ig = page.instagram
    return {
        "id": uuid.uuid4(),
        "connection_id": connection_id,
        "page_id": page.page_id,
        "page_name": page.page_name,
        "category": page.category,
        "tasks": list(page.tasks),
        "encrypted_page_token": page.encrypted_page_token,
        "ig_user_id": ig.ig_user_id if ig else None,
        "ig_username": ig.username if ig else None,
        "ig_name": ig.name if ig else None,
        "ig_profile_picture_url": ig.profile_picture_url if ig else None,
    }


def _to_domain(row: SocialConnectionRow) -> ConnectedAccount:
    return ConnectedAccount(
        id=row.id,
        tenant_id=row.tenant_id,
        provider=Provider(row.provider),
        provider_user_id=row.provider_user_id,
        provider_user_name=row.provider_user_name,
        encrypted_user_token=row.encrypted_user_token,
        token_expires_at=row.token_expires_at,
        granted_scopes=list(row.granted_scopes or []),
        status=AccountStatus(row.status),
        connected_at=row.connected_at,
        last_synced_at=row.last_synced_at,
        updated_at=row.updated_at,
        pages=[_page_to_domain(page) for page in row.pages],
    )


def _page_to_domain(row: SocialConnectionPageRow) -> PageAsset:
    instagram = (
        InstagramAsset(
            ig_user_id=row.ig_user_id,
            username=row.ig_username,
            name=row.ig_name,
            profile_picture_url=row.ig_profile_picture_url,
        )
        if row.ig_user_id
        else None
    )
    return PageAsset(
        page_id=row.page_id,
        page_name=row.page_name,
        category=row.category,
        tasks=list(row.tasks or []),
        encrypted_page_token=row.encrypted_page_token,
        instagram=instagram,
    )
