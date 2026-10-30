"""Pydantic v2 API contracts for tenancy (SPRINT-03 B9). Zero-Secret: no token fields."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.modules.tenancy.domain.access import BrandRole
from src.modules.tenancy.domain.models import Brand, BrandAccessGrant, GrantRole, Slug, Tenant


class TenantCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    slug: Slug
    name: str = Field(..., min_length=1, max_length=255)


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: UUID
    slug: str
    name: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, tenant: Tenant) -> TenantOut:
        return cls.model_validate(tenant.model_dump())


class BrandCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    # TEMPORARY until auth module (B4): the acting tenant becomes the brand owner.
    tenant_id: UUID
    slug: Slug
    name: str = Field(..., min_length=1, max_length=255)


class BrandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: UUID
    owner_tenant_id: UUID
    slug: str
    name: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, brand: Brand) -> BrandOut:
        return cls.model_validate(brand.model_dump())


class BrandWithRoleOut(BrandOut):
    role: BrandRole

    @classmethod
    def from_domain_with_role(cls, brand: Brand, role: BrandRole) -> BrandWithRoleOut:
        return cls.model_validate({**brand.model_dump(), "role": role})


class GrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # TEMPORARY until auth module (B4): the acting tenant (must be the brand owner).
    tenant_id: UUID
    grantee_tenant_id: UUID
    role: GrantRole


class GrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: UUID
    brand_id: UUID
    grantee_tenant_id: UUID
    role: GrantRole
    granted_by_tenant_id: UUID
    granted_at: datetime
    revoked_at: datetime | None = None

    @classmethod
    def from_domain(cls, grant: BrandAccessGrant) -> GrantOut:
        return cls.model_validate(grant.model_dump())
