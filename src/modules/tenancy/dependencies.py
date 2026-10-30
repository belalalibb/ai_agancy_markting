"""Actor identity for tenancy routes (SPRINT-04 B1).

:data:`ActorTenantId` is derived from the **verified access JWT** (``tid`` claim) via
``auth.dependencies.CurrentUser`` — never from a query string or request body. This is
the single place where the acting tenant is read; any other read of ``tenant_id`` from a
request is a violation of the directive.

Import direction (ARCHITECTURE_LOCK D-013): ``tenancy.dependencies`` → ``auth.dependencies``
is allowed; ``auth`` only imports ``tenancy.domain`` / ``tenancy.service`` / ``tenancy.router``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db_session
from src.modules.auth.dependencies import CurrentUser
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


def _actor_tenant_id(actor: CurrentUser) -> UUID:
    return actor.tenant_id


ActorTenantId = Annotated[UUID, Depends(_actor_tenant_id)]


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
