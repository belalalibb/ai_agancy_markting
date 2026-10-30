"""Domain models for tenancy (SPRINT-03 Part B, owner decision "option C simplified").

``Tenant`` → owns ``Brand``s → a brand may be shared with other tenants through a
``BrandAccessGrant`` (role ``manager`` or ``viewer``). Framework-free, Pydantic only.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{1,62}[a-z0-9])?$"
SLUG_REGEX = re.compile(SLUG_PATTERN)

# 3–64 chars, lowercase alphanumerics and dashes, no leading/trailing dash.
Slug = Annotated[str, StringConstraints(min_length=3, max_length=64, pattern=SLUG_PATTERN)]


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class GrantRole(StrEnum):
    """Roles that can be *granted* (the owner role is implicit, never granted)."""

    MANAGER = "manager"
    VIEWER = "viewer"


class Tenant(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    slug: Slug
    name: str = Field(..., min_length=1, max_length=255)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class Brand(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    owner_tenant_id: UUID
    slug: Slug
    name: str = Field(..., min_length=1, max_length=255)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class BrandAccessGrant(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    brand_id: UUID
    grantee_tenant_id: UUID
    role: GrantRole
    granted_by_tenant_id: UUID
    granted_at: datetime = Field(default_factory=_utcnow)
    revoked_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
