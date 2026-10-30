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

from src.modules.social_connect.domain.models import AccountStatus, ConnectedAccount, PageAsset

TokenType = Literal["bearer"]

# SPRINT-05 B1: derived, client-facing health of the stored credential (no token exposed).
TokenHealth = Literal["valid", "expiring_soon", "expired", "unknown", "reauth_required"]
EXPIRING_SOON_WINDOW = timedelta(days=7)


def compute_token_health(account: ConnectedAccount, *, now: datetime) -> TokenHealth:
    """B1 rules, in priority order: ``reauth_required`` status wins; unknown expiry ⇒
    ``unknown``; past ⇒ ``expired``; within 7 days ⇒ ``expiring_soon``; else ``valid``."""
    if account.status == AccountStatus.REAUTH_REQUIRED:
        return "reauth_required"
    expires_at = account.token_expires_at
    if expires_at is None:
        return "unknown"
    if expires_at <= now:
        return "expired"
    if expires_at - now <= EXPIRING_SOON_WINDOW:
        return "expiring_soon"
    return "valid"


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
    # SPRINT-05 B1: derived from ``status`` + ``expires_at`` at render time (visible to all).
    token_health: TokenHealth = "unknown"  # noqa: S105 - health *label*, not a secret
    connected_at: datetime
    last_synced_at: datetime | None = Field(
        default=None, description="Last successful sync with Meta (OAuth, selection, resync)."
    )
    updated_at: datetime
    # H1: ``True`` when the viewer is ``connected_by_tenant_id`` — only then may the client
    # offer the selection UI (and only then does ``pages`` include unselected Pages).
    is_connection_owner: bool = False
    pages: list[PageAssetPublic] = Field(default_factory=list)

    @classmethod
    def from_domain(
        cls,
        account: ConnectedAccount,
        *,
        viewer_tenant_id: UUID,
        now: datetime | None = None,
    ) -> ConnectedAccountPublic:
        """Public view **for a given viewer** (SPRINT-04.1 H1).

        Unselected Pages are personal data of the Meta account that ran OAuth (an agency's
        other clients, for instance). They are shown only to the connection owner; every
        other role on the brand sees the selected Pages alone. ``now`` is injectable for a
        deterministic ``token_health`` (B1).
        """
        is_owner = account.connected_by_tenant_id == viewer_tenant_id
        data = account.model_dump(exclude={"pages", *_SENSITIVE_ACCOUNT_FIELDS})
        data["is_connection_owner"] = is_owner
        data["token_health"] = compute_token_health(account, now=now or datetime.now(tz=UTC))
        data["pages"] = [
            PageAssetPublic.from_domain(page)
            for page in account.pages
            if page.is_selected or is_owner
        ]
        return cls.model_validate(data)


ConnectedAccountOut = ConnectedAccountPublic


# ------------------------------------------------ SPRINT-05 operational reads ---
class ResyncResponse(ConnectedAccountPublic):
    """B2: the refreshed connection plus the selected Pages Meta no longer returned."""

    removed_selected_page_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_resync(
        cls,
        account: ConnectedAccount,
        *,
        viewer_tenant_id: UUID,
        removed_selected_page_ids: list[str],
        now: datetime | None = None,
    ) -> ResyncResponse:
        base = ConnectedAccountPublic.from_domain(
            account, viewer_tenant_id=viewer_tenant_id, now=now
        )
        return cls.model_validate(
            {**base.model_dump(), "removed_selected_page_ids": list(removed_selected_page_ids)}
        )


class PagePostPublic(BaseModel):
    """B3: one Page post (``GET /{page_id}/posts``) — only the whitelisted fields."""

    model_config = ConfigDict(extra="ignore")

    id: str
    message: str | None = None
    created_time: datetime
    permalink_url: str | None = None
    status_type: str | None = None
    full_picture: str | None = None
    is_published: bool | None = None


class InstagramMediaPublic(BaseModel):
    """B4: one Instagram media node (``GET /{ig_user_id}/media``)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    caption: str | None = None
    media_type: str
    media_url: str | None = None
    thumbnail_url: str | None = None
    permalink: str | None = None
    timestamp: datetime
    like_count: int | None = None
    comments_count: int | None = None


class _CursorPage(BaseModel):
    """Opaque cursor pagination. ``next_cursor`` is ``paging.cursors.after`` **only** — the
    raw Graph paging URL (which embeds the Page token) is never exposed (§2-0(a))."""

    model_config = ConfigDict(extra="forbid")

    next_cursor: str | None = None
    has_more: bool = False


class PagePostsPage(_CursorPage):
    items: list[PagePostPublic] = Field(default_factory=list)


class InstagramMediaPage(_CursorPage):
    items: list[InstagramMediaPublic] = Field(default_factory=list)
