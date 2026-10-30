"""Domain models for auth (SPRINT-04 A2/A9).

One user ↔ one tenant (``users.tenant_id`` UNIQUE). Multi-member tenants are deferred
(owner decision, see ``CURRENT_DIRECTIVE.md`` §8).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

# Password policy (A4): length bounds; equality with the email is rejected in the service.
PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 128
EMAIL_MAX_LENGTH = 320


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class User(BaseModel):
    """A login identity bound to exactly one tenant. ``password_hash`` never leaves the API."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    email: str = Field(..., min_length=3, max_length=EMAIL_MAX_LENGTH)
    password_hash: str
    is_active: bool = True
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    last_login_at: datetime | None = None


class RefreshSession(BaseModel):
    """One browser session. ``id`` doubles as the JWT ``sid`` claim.

    Only ``token_hash`` (SHA-256 of the cookie secret) is stored — never the secret.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    token_hash: str = Field(..., min_length=64, max_length=64)
    expires_at: datetime
    revoked_at: datetime | None = None
    replaced_by: UUID | None = None
    user_agent: str | None = Field(default=None, max_length=255)
    ip: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at <= now


class AuthenticatedActor(BaseModel):
    """Verified identity extracted from the access JWT (no database round-trip)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: UUID
    tenant_id: UUID
    sid: UUID
