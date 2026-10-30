"""Pydantic v2 schemas (API contracts) for the Meta (Facebook / Instagram) connector."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    computed_field,
    field_validator,
)

from src.modules.social_connect.domain.models import ConnectedAccount, PageAsset

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


class MetaPageInfo(BaseModel):
    """A Facebook Page the user manages plus its linked Instagram Business account."""

    model_config = ConfigDict(extra="ignore")

    # ``validation_alias`` accepts raw Graph field names on input while the API
    # response (and ``model_dump``) always uses the explicit, self-describing names.
    page_id: str = Field(..., validation_alias=AliasChoices("page_id", "id"))
    page_name: str = Field(..., validation_alias=AliasChoices("page_name", "name"))
    category: str | None = None
    instagram_business_account_id: str | None = None
    instagram_username: str | None = None
    instagram_name: str | None = None
    instagram_profile_picture_url: str | None = None
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

    @classmethod
    def from_graph(cls, node: dict[str, object]) -> MetaPageInfo:
        """Build from a raw ``/me/accounts`` node (flattens ``instagram_business_account``)."""
        data = dict(node)
        ig = data.pop("instagram_business_account", None)
        if isinstance(ig, dict):
            data["instagram_business_account_id"] = ig.get("id")
            data["instagram_username"] = ig.get("username")
            data["instagram_name"] = ig.get("name")
            data["instagram_profile_picture_url"] = ig.get("profile_picture_url")
        return cls.model_validate(data)


class MetaAuthorizeUrlResponse(BaseModel):
    """URL the frontend should redirect the user to in order to start OAuth."""

    authorization_url: str
    state: str


class ConnectionStartRequest(BaseModel):
    """Body of ``POST /brands/{brand_id}/social/meta/connections/start`` — intentionally empty.

    The acting tenant/user come from the Bearer JWT (SPRINT-04 B2); any body key ⇒ 422.
    """

    model_config = ConfigDict(extra="forbid")


class PageSelectionRequest(BaseModel):
    """Body of ``PUT …/connections/{id}/pages/selection`` (B6): 1–50 unique Page ids."""

    model_config = ConfigDict(extra="forbid")

    page_ids: list[str] = Field(..., min_length=1, max_length=50)

    @field_validator("page_ids")
    @classmethod
    def _unique_non_empty(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value]
        if any(not v for v in cleaned):
            msg = "page_ids must not contain empty values"
            raise ValueError(msg)
        if len(set(cleaned)) != len(cleaned):
            msg = "page_ids must be unique"
            raise ValueError(msg)
        return cleaned


class MetaUserInfo(BaseModel):
    """Trusted identity from ``GET /me`` (SPRINT-02.1 F2) — the token's own user."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str | None = None


class MetaDebugTokenInfo(BaseModel):
    """Subset of ``/debug_token`` used to validate a stored token."""

    model_config = ConfigDict(extra="ignore")

    app_id: str
    is_valid: bool
    user_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    expires_at: int | None = None
    # ``data.error.code`` when Meta explains *why* ``is_valid`` is false (e.g. 190).
    error_code: int | None = None


# ------------------------------------------------------------ public views ---
# Zero-Secret: these are the ONLY shapes returned by the API. They have no
# ``*_token`` / ``encrypted_*`` fields at all — not masked, simply absent.
_SENSITIVE_ACCOUNT_FIELDS = frozenset({"encrypted_user_token"})
_SENSITIVE_PAGE_FIELDS = frozenset({"encrypted_page_token"})


class InstagramAssetPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    ig_user_id: str
    username: str | None = None
    name: str | None = None
    profile_picture_url: str | None = None


class PageAssetPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    page_id: str
    page_name: str
    category: str | None = None
    tasks: list[str] = Field(default_factory=list)
    instagram: InstagramAssetPublic | None = None
    # B6: explicit selection flag — the selection UI needs *all* Pages with both flags.
    is_selected: bool = False
    # Derived flag only — tells the client whether this Page can be acted upon; the
    # ciphertext itself is never exposed (Zero-Secret).
    has_page_token: bool = False

    @classmethod
    def from_domain(cls, page: PageAsset) -> PageAssetPublic:
        data = page.model_dump(exclude=set(_SENSITIVE_PAGE_FIELDS))
        data["has_page_token"] = page.encrypted_page_token is not None
        return cls.model_validate(data)


# Directive naming (B6): ``PageAssetOut`` / ``ConnectedAccountOut`` are the public views.
PageAssetOut = PageAssetPublic


class ConnectedAccountPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: UUID
    brand_id: UUID
    connected_by_tenant_id: UUID
    provider: str
    provider_user_id: str
    provider_user_name: str | None = None
    # Named ``expires_at`` (not ``token_expires_at``) so the public contract has no field
    # whose name even mentions a token. NULL = unknown, not "never expires" (D-055).
    expires_at: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("expires_at", "token_expires_at"),
        description="When the stored credential expires; null = unknown (D-055).",
    )
    granted_scopes: list[str] = Field(default_factory=list)
    status: str
    connected_at: datetime
    last_synced_at: datetime | None = None
    updated_at: datetime
    pages: list[PageAssetPublic] = Field(default_factory=list)

    @classmethod
    def from_domain(cls, account: ConnectedAccount) -> ConnectedAccountPublic:
        data = account.model_dump(exclude={"pages", *_SENSITIVE_ACCOUNT_FIELDS})
        data["pages"] = [PageAssetPublic.from_domain(page) for page in account.pages]
        return cls.model_validate(data)


ConnectedAccountOut = ConnectedAccountPublic
