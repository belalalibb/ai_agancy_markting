"""Brand access resolution contract (SPRINT-03 B5).

``social_connect`` depends on *this* module only (``social_connect ⇒ tenancy.domain``);
the PostgreSQL implementation lives in ``tenancy.infrastructure``.

Response policy (enforced by the callers):

* ``resolve(...) is None``  ⇒ **404 brand_not_found** — never 403, so a stranger cannot
  enumerate brands.
* role insufficient         ⇒ **403 insufficient_role**.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID


class BrandRole(StrEnum):
    OWNER = "owner"
    MANAGER = "manager"
    VIEWER = "viewer"


# Permission matrix (B5): read = all roles; write on connections = owner+manager;
# grants = owner only.
READ_ROLES: frozenset[BrandRole] = frozenset(
    {BrandRole.OWNER, BrandRole.MANAGER, BrandRole.VIEWER}
)
CONNECTION_WRITE_ROLES: frozenset[BrandRole] = frozenset({BrandRole.OWNER, BrandRole.MANAGER})
GRANT_ROLES: frozenset[BrandRole] = frozenset({BrandRole.OWNER})


@runtime_checkable
class BrandAccessResolver(Protocol):
    async def resolve(self, tenant_id: UUID, brand_id: UUID) -> BrandRole | None:
        """Owner ⇒ OWNER; active grant ⇒ its role; otherwise ``None``."""
        ...
