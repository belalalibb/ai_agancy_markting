"""Pydantic v2 schemas (API contracts) for the Meta (Facebook / Instagram) connector."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    computed_field,
    field_serializer,
    field_validator,
)

TokenType = Literal["bearer"]


class MetaAuthRequest(BaseModel):
    """Payload sent by the frontend after Meta redirects back with an auth ``code``."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(..., min_length=1, description="Authorization code returned by Meta.")
    redirect_uri: AnyHttpUrl = Field(
        ..., description="Exact redirect URI used when starting the OAuth dialog."
    )
    state: str | None = Field(
        default=None,
        description="Opaque CSRF state echoed back by Meta; validated by the caller.",
    )


class MetaTokenResponse(BaseModel):
    """Result of exchanging an authorization code for user access tokens.

    ``short_lived_token`` is kept for traceability only and is *never* persisted.
    """

    model_config = ConfigDict(extra="forbid")

    short_lived_token: SecretStr | None = Field(default=None, repr=False)
    long_lived_token: SecretStr = Field(..., repr=False)
    token_type: TokenType = "bearer"  # noqa: S105 - OAuth token *type*, not a secret
    expires_in: int = Field(..., ge=0, description="Lifetime in seconds (0 = never expires).")
    issued_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def expires_at(self) -> datetime | None:
        if self.expires_in == 0:
            return None
        return self.issued_at + timedelta(seconds=self.expires_in)

    @field_serializer("short_lived_token", "long_lived_token", when_used="json")
    def _mask_tokens(self, value: SecretStr | None) -> str | None:
        return "***" if value is not None else None


class MetaPageInfo(BaseModel):
    """A Facebook Page the user manages plus its linked Instagram Business account."""

    model_config = ConfigDict(extra="ignore")

    # ``validation_alias`` accepts raw Graph field names on input while the API
    # response (and ``model_dump``) always uses the explicit, self-describing names.
    page_id: str = Field(..., validation_alias=AliasChoices("page_id", "id"))
    page_name: str = Field(..., validation_alias=AliasChoices("page_name", "name"))
    category: str | None = None
    instagram_business_account_id: str | None = None
    permissions: list[str] = Field(
        default_factory=list, validation_alias=AliasChoices("permissions", "tasks")
    )
    page_access_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("page_access_token", "access_token"),
        repr=False,
    )

    @field_validator("permissions", mode="before")
    @classmethod
    def _coerce_permissions(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list | tuple | set):
            return [str(item) for item in value]
        msg = "permissions must be a list of strings"
        raise ValueError(msg)

    @field_serializer("page_access_token", when_used="json")
    def _mask_page_token(self, value: SecretStr | None) -> str | None:
        return "***" if value is not None else None

    @classmethod
    def from_graph(cls, node: dict[str, object]) -> MetaPageInfo:
        """Build from a raw ``/me/accounts`` node (flattens ``instagram_business_account``)."""
        data = dict(node)
        ig = data.pop("instagram_business_account", None)
        if isinstance(ig, dict):
            data["instagram_business_account_id"] = ig.get("id")
        return cls.model_validate(data)


class MetaConnectionResult(BaseModel):
    """Response returned by the OAuth callback endpoint (tokens are already encrypted)."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["meta"] = "meta"
    encrypted_user_token: str = Field(..., description="Fernet ciphertext; store as-is.")
    token_expires_at: datetime | None
    pages: list[MetaPageInfo]
    instagram_accounts: list[str] = Field(default_factory=list)


class MetaAuthorizeUrlResponse(BaseModel):
    """URL the frontend should redirect the user to in order to start OAuth."""

    authorize_url: str
    state: str


class MetaDebugTokenInfo(BaseModel):
    """Subset of ``/debug_token`` used to validate a stored token."""

    model_config = ConfigDict(extra="ignore")

    app_id: str
    is_valid: bool
    user_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    expires_at: int | None = None
