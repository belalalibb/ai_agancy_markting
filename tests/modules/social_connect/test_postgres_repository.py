"""Unit tests — ``PostgresConnectedAccountRepository`` against a scripted stub session.

Statement composition (brand scoping, ON CONFLICT target, page replacement) and
row→domain mapping. The integration suite runs the same adapter on PostgreSQL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from src.modules.social_connect.domain.models import (
    AccountStatus,
    ConnectedAccount,
    InstagramAsset,
    PageAsset,
)
from src.modules.social_connect.infrastructure.orm import (
    SocialConnectionPageRow,
    SocialConnectionRow,
)
from src.modules.social_connect.infrastructure.postgres_repository import (
    PostgresConnectedAccountRepository,
)
from src.modules.social_connect.router import get_repository

from tests.conftest import BRAND_ID, OWNER_ID
from tests.fakes import StubResult, StubSession, compiled

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _account(pages: list[PageAsset] | None = None) -> ConnectedAccount:
    return ConnectedAccount(
        brand_id=BRAND_ID,
        connected_by_tenant_id=OWNER_ID,
        provider_user_id="u-1",
        provider_user_name="Nile",
        encrypted_user_token="gAAAAA-user",
        granted_scopes=["pages_show_list"],
        connected_at=NOW,
        updated_at=NOW,
        pages=pages
        if pages is not None
        else [
            PageAsset(
                page_id="1001",
                page_name="Nile",
                encrypted_page_token="gAAAAA-page",
                is_selected=True,
                instagram=InstagramAsset(ig_user_id="178", username="nile"),
            ),
            PageAsset(page_id="1002", page_name="Cairo"),
        ],
    )


def _row(account: ConnectedAccount) -> SocialConnectionRow:
    row = SocialConnectionRow(
        id=account.id,
        brand_id=account.brand_id,
        connected_by_tenant_id=account.connected_by_tenant_id,
        provider=account.provider.value,
        provider_user_id=account.provider_user_id,
        provider_user_name=account.provider_user_name,
        encrypted_user_token=account.encrypted_user_token,
        token_expires_at=account.token_expires_at,
        granted_scopes=list(account.granted_scopes),
        status=account.status.value,
        connected_at=account.connected_at,
        last_synced_at=account.last_synced_at,
        updated_at=account.updated_at,
    )
    row.pages = [
        SocialConnectionPageRow(
            id=uuid4(),
            connection_id=account.id,
            page_id=p.page_id,
            page_name=p.page_name,
            category=p.category,
            tasks=list(p.tasks),
            encrypted_page_token=p.encrypted_page_token,
            is_selected=p.is_selected,
            ig_user_id=p.instagram.ig_user_id if p.instagram else None,
            ig_username=p.instagram.username if p.instagram else None,
            ig_name=p.instagram.name if p.instagram else None,
            ig_profile_picture_url=p.instagram.profile_picture_url if p.instagram else None,
        )
        for p in account.pages
    ]
    return row


async def test_upsert_targets_brand_identity_constraint_and_rewrites_pages() -> None:
    account = _account()
    session = StubSession(
        [
            StubResult(scalar=account.id),  # INSERT … ON CONFLICT … RETURNING id
            StubResult(),  # DELETE pages
            StubResult(),  # INSERT pages
            StubResult(scalar=_row(account)),  # re-read
        ]
    )
    repo = PostgresConnectedAccountRepository(session)  # type: ignore[arg-type]

    stored = await repo.upsert(account)

    insert_sql = compiled(session.executed[0][0])
    assert "INSERT INTO social_connections" in insert_sql
    assert "ON CONFLICT ON CONSTRAINT uq_social_connections_brand_identity" in insert_sql
    set_clause = insert_sql.split("DO UPDATE SET", 1)[1]
    assert "connected_by_tenant_id =" in set_clause  # reconnect by a grantee re-owns the row
    assert "encrypted_user_token =" in set_clause
    assert "connected_at" not in set_clause  # first connection timestamp is preserved
    assert "RETURNING social_connections.id" in insert_sql
    assert compiled(session.executed[1][0]).startswith("DELETE FROM social_connection_pages")
    assert "INSERT INTO social_connection_pages" in compiled(session.executed[2][0])
    read_sql = compiled(session.executed[3][0])
    assert "social_connections.brand_id =" in read_sql

    assert stored.id == account.id
    assert stored.brand_id == BRAND_ID
    assert stored.connected_by_tenant_id == OWNER_ID
    assert [p.page_id for p in stored.pages] == ["1001", "1002"]
    assert stored.pages[0].is_selected is True
    assert stored.pages[0].instagram is not None
    assert stored.pages[0].instagram.username == "nile"
    assert stored.pages[1].instagram is None
    assert stored.pages[1].is_selected is False
    assert stored.pages[1].encrypted_page_token is None


async def test_upsert_with_no_pages_skips_page_insert() -> None:
    account = _account(pages=[])
    session = StubSession(
        [StubResult(scalar=account.id), StubResult(), StubResult(scalar=_row(account))]
    )
    repo = PostgresConnectedAccountRepository(session)  # type: ignore[arg-type]

    stored = await repo.upsert(account)

    assert stored.pages == []
    assert len(session.executed) == 3  # insert, delete pages, re-read — no page insert


async def test_replace_pages_requires_brand_scoped_connection() -> None:
    account = _account()
    session = StubSession(
        [
            StubResult(scalar=None),  # not found in this brand
            StubResult(scalar=account.id),  # found
            StubResult(),  # delete pages
            StubResult(),  # insert pages
            StubResult(),  # touch last_synced_at
        ]
    )
    repo = PostgresConnectedAccountRepository(session)  # type: ignore[arg-type]

    assert await repo.replace_pages(BRAND_ID, account.id, account.pages) is False
    assert len(session.executed) == 1

    assert await repo.replace_pages(BRAND_ID, account.id, account.pages) is True
    touch_sql = compiled(session.executed[-1][0])
    assert touch_sql.startswith("UPDATE social_connections")
    assert "last_synced_at" in touch_sql


async def test_set_status_and_delete_are_brand_scoped_and_report_rowcount() -> None:
    account = _account()
    session = StubSession(
        [
            StubResult(rowcount=1),
            StubResult(rowcount=0),
            StubResult(rowcount=1),
            StubResult(rowcount=None),
        ]
    )
    repo = PostgresConnectedAccountRepository(session)  # type: ignore[arg-type]

    assert await repo.set_status(BRAND_ID, account.id, AccountStatus.REAUTH_REQUIRED) is True
    assert await repo.set_status(BRAND_ID, account.id, AccountStatus.ACTIVE) is False
    status_sql = compiled(session.executed[0][0])
    assert status_sql.startswith("UPDATE social_connections")
    assert "social_connections.brand_id =" in status_sql

    assert await repo.delete(BRAND_ID, account.id) is True
    assert await repo.delete(BRAND_ID, account.id) is False
    delete_sql = compiled(session.executed[2][0])
    assert delete_sql.startswith("DELETE FROM social_connections")
    assert "social_connections.brand_id =" in delete_sql


async def test_reads_are_brand_scoped() -> None:
    account = _account()
    row = _row(account)
    session = StubSession(
        [
            StubResult(scalar=None),  # get → miss
            StubResult(scalar=row),  # find_by_identity → hit
            StubResult(scalar=None),  # find_by_identity → miss
            StubResult(scalars=[row]),  # list_by_brand
        ]
    )
    repo = PostgresConnectedAccountRepository(session)  # type: ignore[arg-type]

    assert await repo.get(BRAND_ID, account.id) is None
    found = await repo.find_by_identity(BRAND_ID, "meta", "u-1")
    assert found is not None and found.provider_user_id == "u-1"
    identity_sql = compiled(session.executed[1][0])
    assert "social_connections.brand_id =" in identity_sql
    assert "social_connections.provider =" in identity_sql
    assert "social_connections.provider_user_id =" in identity_sql
    assert await repo.find_by_identity(BRAND_ID, "meta", "nope") is None

    listed = await repo.list_by_brand(BRAND_ID)
    assert [a.id for a in listed] == [account.id]
    list_sql = compiled(session.executed[3][0])
    assert "social_connections.brand_id =" in list_sql
    assert "ORDER BY social_connections.connected_at" in list_sql


def test_get_repository_binds_session_to_postgres_adapter() -> None:
    session = StubSession()
    assert isinstance(
        get_repository(session),  # type: ignore[arg-type]
        PostgresConnectedAccountRepository,
    )
