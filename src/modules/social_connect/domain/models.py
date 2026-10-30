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
    # ``None`` = no Page token stored (never an empty-string ciphertext).
    encrypted_page_token: str | None = Field(default=None, repr=False)
    instagram: InstagramAsset | None = None
    # SPRINT-03 B6: explicit selection. Invariant: ``is_selected ⇒ token IS NOT NULL`` (DB
    # CHECK) and ``NOT is_selected ⇒ token IS NULL`` (enforced by the service).
    is_selected: bool = False


class ConnectedAccount(BaseModel):
    """Aggregate root: one provider user connected to one *brand* (SPRINT-03 B2).

    ``connected_by_tenant_id`` records who performed the OAuth (the brand owner or an
    agency with a grant); it drives the B8 purge when a grant is revoked.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    brand_id: UUID
    connected_by_tenant_id: UUID
    provider: Provider = Provider.META
    provider_user_id: str = Field(..., min_length=1, max_length=64)
    provider_user_name: str | None = None
    encrypted_user_token: str = Field(..., repr=False)
    token_expires_at: datetime | None = Field(
        default=None, description="NULL means *unknown*, not *never expires* (D-055)."
    )
    granted_scopes: list[str] = Field(default_factory=list)
    # SPRINT-06 A-1: ``True`` iff ``granted_scopes`` came from a *successful* ``/debug_token``
    # answer (an empty list is then a verified "no scopes"); ``False`` = unknown/unverified.
    scopes_verified: bool = False
    status: AccountStatus = AccountStatus.ACTIVE
    connected_at: datetime = Field(default_factory=_utcnow)
    last_synced_at: datetime | None = None
    updated_at: datetime = Field(default_factory=_utcnow)
    pages: list[PageAsset] = Field(default_factory=list)

    def violates_selection_invariant(self) -> list[str]:
        """Page ids with ``is_selected=False`` but a stored token (must always be empty)."""
        return [p.page_id for p in self.pages if not p.is_selected and p.encrypted_page_token]
