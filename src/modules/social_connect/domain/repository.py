"""Storage contract for connected accounts (ARCHITECTURE_LOCK D-013).

The service layer depends only on this ``Protocol``; the PostgreSQL implementation
lives in ``infrastructure/postgres_repository.py``. Every operation is scoped by
``brand_id`` (SPRINT-03: brand isolation — F1 / D-014).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from src.modules.social_connect.domain.models import AccountStatus, ConnectedAccount, PageAsset


@runtime_checkable
class ConnectedAccountRepository(Protocol):
    async def upsert(self, account: ConnectedAccount) -> ConnectedAccount:
        """Insert or update on ``(brand_id, provider, provider_user_id)``.

        Pages are replaced wholesale (old rows deleted, new rows inserted) in the
        same transaction. Returns the persisted aggregate (with the stored ``id``).
        """
        ...

    async def find_by_identity(
        self, brand_id: UUID, provider: str, provider_user_id: str
    ) -> ConnectedAccount | None:
        """The existing row for this Meta user in this brand (reconnect detection, B6)."""
        ...

    async def get(self, brand_id: UUID, account_id: UUID) -> ConnectedAccount | None: ...

    async def list_by_brand(self, brand_id: UUID) -> list[ConnectedAccount]: ...

    async def set_status(self, brand_id: UUID, account_id: UUID, status: AccountStatus) -> bool:
        """Return ``True`` when a row was updated, ``False`` when not found."""
        ...

    async def replace_pages(
        self, brand_id: UUID, account_id: UUID, pages: list[PageAsset]
    ) -> bool:
        """B6 selection: replace the page set atomically. ``False`` when not found."""
        ...

    async def delete(self, brand_id: UUID, account_id: UUID) -> bool:
        """Return ``True`` when a row was deleted (pages cascade), ``False`` when not found."""
        ...

    async def update_token_metadata(
        self,
        brand_id: UUID,
        account_id: UUID,
        *,
        token_expires_at: datetime | None,
        granted_scopes: list[str] | None,
        last_synced_at: datetime,
    ) -> bool:
        """SPRINT-05 B2 (resync): refresh expiry / scopes / ``last_synced_at`` of the stored
        user token **without** touching the ciphertext, the status or the Pages.
        ``granted_scopes=None`` keeps the stored scopes. ``False`` when not found."""
        ...
