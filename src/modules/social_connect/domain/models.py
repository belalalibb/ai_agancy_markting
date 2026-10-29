"""Domain models for connected social accounts (framework-free, Pydantic only).

Tokens appear here **only in encrypted form** (``encrypted_*``). Plaintext tokens never
leave the service layer, and the public API schemas (``schemas.py``) drop even the
encrypted fields entirely (Zero-Secret).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class Provider(StrEnum):
    META = "meta"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    REAUTH_REQUIRED = "reauth_required"
    REVOKED = "revoked"


class InstagramAsset(BaseModel):
    """Instagram Business/Creator account linked to a Facebook Page."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    ig_user_id: str
    username: str | None = None
    name: str | None = None
    profile_picture_url: str | None = None


class PageAsset(BaseModel):
    """A Facebook Page the connected user manages (token stored encrypted)."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    page_id: str
    page_name: str
    category: str | None = None
    tasks: list[str] = Field(default_factory=list)
    # ``None`` = no Page token was granted (never an empty-string ciphertext).
    encrypted_page_token: str | None = Field(default=None, repr=False)
    instagram: InstagramAsset | None = None


class ConnectedAccount(BaseModel):
    """Aggregate root: one provider user connected to one tenant."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    tenant_id: str = Field(..., min_length=1, max_length=64)
    provider: Provider = Provider.META
    provider_user_id: str = Field(..., min_length=1, max_length=64)
    provider_user_name: str | None = None
    encrypted_user_token: str = Field(..., repr=False)
    token_expires_at: datetime | None = Field(
        default=None, description="NULL means *unknown*, not *never expires* (D-055)."
    )
    granted_scopes: list[str] = Field(default_factory=list)
    status: AccountStatus = AccountStatus.ACTIVE
    connected_at: datetime = Field(default_factory=_utcnow)
    last_synced_at: datetime | None = None
    updated_at: datetime = Field(default_factory=_utcnow)
    pages: list[PageAsset] = Field(default_factory=list)
