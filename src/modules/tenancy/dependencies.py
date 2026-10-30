"""Actor identity — TEMPORARY until the auth module lands (SPRINT-03 B4).

This is the **single** place where the acting tenant is read from the request. Query
routes use :data:`ActorTenantId`; body routes carry ``tenant_id: UUID`` in their schema.
Any other read of ``tenant_id`` from a request is a violation of the directive.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_session
from src.modules.tenancy.domain.access import BrandAccessResolver
from src.modules.tenancy.domain.repository import (
    BrandAccessGrantRepository,
    BrandRepository,
    TenantRepository,
)
from src.modules.tenancy.infrastructure.postgres_repository import (
    PostgresBrandAccessGrantRepository,
    PostgresBrandAccessResolver,
    PostgresBrandRepository,
    PostgresTenantRepository,
)

ActorTenantId = Annotated[
    UUID, Query(alias="tenant_id", description="TEMPORARY: acting tenant until auth module")
]


def get_tenant_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TenantRepository:
    return PostgresTenantRepository(session)


def get_brand_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BrandRepository:
    return PostgresBrandRepository(session)


def get_grant_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BrandAccessGrantRepository:
    return PostgresBrandAccessGrantRepository(session)


def get_brand_access_resolver(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BrandAccessResolver:
    return PostgresBrandAccessResolver(session)
