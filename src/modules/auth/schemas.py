"""Pydantic v2 API contracts for auth (A7). Zero-Secret: the only secret-bearing output
field is ``access_token``; the refresh secret travels exclusively in an HttpOnly cookie."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from src.modules.auth.domain.models import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH
from src.modules.tenancy.domain.models import Slug


class RegisterRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: EmailStr = Field(..., max_length=320)
    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    tenant_slug: Slug
    tenant_name: str = Field(..., min_length=1, max_length=255)

    @model_validator(mode="after")
    def _password_not_email(self) -> RegisterRequest:
        if self.password.strip().lower() == self.email.lower():
            msg = "password must not equal the email address"
            raise ValueError(msg)
        return self


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: EmailStr = Field(..., max_length=320)
    # No lower bound: a too-short value must yield the same 401 as a wrong password.
    password: str = Field(..., min_length=1, max_length=PASSWORD_MAX_LENGTH)


class UserOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    email: str
    tenant_id: UUID


class TokenOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: str
    token_type: str = "bearer"  # noqa: S105 — OAuth2 token type, not a secret
    expires_in: int
    user: UserOut


class TenantSummaryOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    slug: str
    name: str


class MeOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    email: str
    tenant_id: UUID
    tenant: TenantSummaryOut
