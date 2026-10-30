"""Application service orchestrating the Meta onboarding flow (SPRINT-02 §5-C, SPRINT-03 B6).

Responsibilities
----------------
1. ``start_connection``    — issue a signed CSRF ``state`` (tenant + brand) and the Login URL.
2. ``complete_connection`` — verify ``state`` → exchange ``code`` → identify the user via
   ``GET /me`` (mandatory, trusted identity) → validate the token via ``/debug_token``
   (optional: when present it must be valid, issued to *our* app and belong to the same
   user as ``/me``) → list Pages (+ IG accounts) → apply the **page selection policy**
   (B6) → encrypt → persist. No derived / fallback identity (SPRINT-02.1 F3).
3. ``select_pages``        — B6 explicit selection: re-fetch Pages, store tokens only for the
   selected ones, wipe tokens of deselected ones. **Connection owner only** (H1).
4. ``list/get/disconnect`` — brand-scoped reads/deletes through the repository; ``disconnect``
   is allowed to the connection owner **or** the brand OWNER (H1).

Ownership (SPRINT-04.1 H1): the Pages Meta returns belong to the *person* who ran OAuth,
not to the brand. Unselected Pages are therefore visible only to ``connected_by_tenant_id``
(filtering happens in ``ConnectedAccountPublic.from_domain``); only that tenant may change
the selection. A reconnect by a different tenant transfers ownership to the reconnecting
tenant (they now own the token).

Status persistence (H3): ``reauth_required`` is written through an out-of-band
``StatusMarker`` because the request transaction is rolled back when we re-raise.

Page selection policy (B6 + owner note on reconnect):
* exactly one Page returned ⇒ stored **selected** with its token (no mixing possible);
* two or more Pages ⇒ metadata only (``is_selected=false``, token ``NULL``, IG kept) —
  **except** when a connection for the same ``(brand, provider, provider_user_id)`` already
  exists: the previously selected Pages keep their selection and get **fresh** encrypted
  tokens (token renewal must not wipe the brand's selection); unselected Pages stay
  token-less;
* zero Pages ⇒ the connection is stored without Pages.

Invariant after every operation: no Page with ``is_selected=false`` and a stored token.

The service never touches SQLAlchemy (ARCHITECTURE_LOCK D-013). Plaintext tokens exist
only transiently here and are never logged or returned. B7: ``provider_user_name`` /
``provider_user_id`` are never written to logs either.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.core.config import Settings
from src.core.metrics import counters
from src.core.oauth_state import OAuthStateSigner
from src.core.security import TokenEncryptionService
from src.modules.social_connect.domain.models import (
    AccountStatus,
    ConnectedAccount,
    InstagramAsset,
    PageAsset,
    Provider,
)
from src.modules.social_connect.domain.repository import ConnectedAccountRepository
from src.modules.social_connect.domain.status_marker import StatusMarker
from src.modules.social_connect.exceptions import (
    ConnectionRevokedError,
    InstagramNotLinkedError,
    MetaGraphError,
    MetaNetworkError,
    MetaRateLimitError,
    MetaTokenExpiredError,
    MetaTokenForeignAppError,
    MetaTokenInvalidError,
    MetaTransientError,
    NotConnectionOwnerError,
    PageNotSelectableError,
    PageNotSelectedError,
    ReauthRequiredError,
    UnknownPageIdsError,
)
from src.modules.social_connect.meta_client import MetaGraphClient
from src.modules.social_connect.schemas import (
    InstagramMediaPage,
    InstagramMediaPublic,
    MetaAuthorizeUrlResponse,
    MetaDebugTokenInfo,
    MetaPageInfo,
    PagePostPublic,
    PagePostsPage,
)
from src.modules.tenancy.domain.access import BrandRole

logger = logging.getLogger(__name__)


class SelectionInvariantError(RuntimeError):
    """Defensive guard: an unselected Page would have been stored with a token."""


@dataclass(frozen=True, slots=True)
class ResyncResult:
    """B2: refreshed aggregate + the selected Page ids Meta no longer returned."""

    account: ConnectedAccount
    removed_selected_page_ids: list[str]


class MetaConnectService:
    """Use-case layer for connecting a Meta (Facebook/Instagram) account to a brand."""

    def __init__(
        self,
        client: MetaGraphClient,
        encryption: TokenEncryptionService,
        repository: ConnectedAccountRepository,
        state_signer: OAuthStateSigner,
        settings: Settings,
        status_marker: StatusMarker | None = None,
    ) -> None:
        self._client = client
        self._encryption = encryption
        self._repository = repository
        self._state_signer = state_signer
        self._settings = settings
        # H3: ``None`` ⇒ fall back to the (rollback-prone) request session. Production and
        # the contract suite always inject one; the fallback keeps the bare service usable.
        self._status_marker = status_marker

    # ------------------------------------------------------------- start ---
    def start_connection(
        self, tenant_id: UUID, brand_id: UUID, user_id: UUID | None = None
    ) -> MetaAuthorizeUrlResponse:
        state = self._state_signer.issue(tenant_id, brand_id, user_id)
        url, _ = self._client.build_authorize_url(
            self._settings.meta_oauth_redirect_uri, state=state
        )
        return MetaAuthorizeUrlResponse(authorization_url=url, state=state)

    def verify_state(self, state: str) -> tuple[UUID, UUID]:
        """Expose ``(tenant_id, brand_id)`` so the router can authorise *before* any Graph call."""
        return self._state_signer.verify(state)

    # ---------------------------------------------------------- complete ---
    async def complete_connection(
        self, tenant_id: UUID, brand_id: UUID, code: str
    ) -> ConnectedAccount:
        """Full callback flow (state already verified + authorised by the caller).

        Raises ``MetaGraphError`` subclasses on provider failures.
        """
        token = await self._client.exchange_code_for_token(
            code=code, redirect_uri=self._settings.meta_oauth_redirect_uri
        )
        long_lived = token.long_lived_token.get_secret_value()

        # Step 3 (SPRINT-02.1 F2/F3): the identity is *always* the token owner per ``/me``.
        me = await self._client.get_me(long_lived)

        # ``/debug_token`` is best-effort for scopes/expiry, but when it does answer its
        # verdict is binding (F5): the token must be valid, ours, and belong to ``me``.
        debug = await self._inspect_token(long_lived)
        if debug is not None:
            if not debug.is_valid:
                raise MetaTokenInvalidError(
                    "debug_token reports the access token as invalid",
                    code=debug.error_code or 190,
                    error_type="OAuthException",
                    http_status=200,
                )
            if debug.app_id and debug.app_id != self._settings.meta_app_id:
                raise MetaTokenForeignAppError(
                    "debug_token app_id does not match this application",
                    error_type="OAuthException",
                    http_status=200,
                )
            if debug.user_id and debug.user_id != me.id:
                raise MetaTokenForeignAppError(
                    "debug_token user_id does not match /me",
                    error_type="OAuthException",
                    http_status=200,
                )
        granted_scopes = list(debug.scopes) if debug else []
        token_expires_at = _expiry_from_debug(debug) if debug else None

        pages = await self._client.get_user_pages(long_lived)

        # B6 (+ owner note): preserve an existing selection on reconnect.
        existing = await self._repository.find_by_identity(brand_id, Provider.META.value, me.id)
        keep_selected = (
            {p.page_id for p in existing.pages if p.is_selected} if existing is not None else set()
        )
        page_assets = self._apply_selection_policy(pages, keep_selected)
        if existing is not None and existing.connected_by_tenant_id != tenant_id:
            # H1 (3): whoever re-ran OAuth holds the new token ⇒ becomes the connection owner.
            logger.info(
                "meta connection ownership transferred: brand=%s connection=%s from=%s to=%s",
                brand_id,
                existing.id,
                existing.connected_by_tenant_id,
                tenant_id,
            )

        account = ConnectedAccount(
            brand_id=brand_id,
            connected_by_tenant_id=tenant_id,
            provider=Provider.META,
            provider_user_id=me.id,
            provider_user_name=me.name,
            encrypted_user_token=self._encryption.encrypt_token(long_lived),
            token_expires_at=token_expires_at,
            granted_scopes=granted_scopes,
            status=AccountStatus.ACTIVE,
            connected_at=existing.connected_at if existing else datetime.now(tz=UTC),
            last_synced_at=datetime.now(tz=UTC),
            pages=page_assets,
        )
        _assert_invariant(account.pages)
        stored = await self._repository.upsert(account)
        logger.info(
            "meta connection stored: brand=%s pages=%d selected=%d reconnect=%s",
            brand_id,
            len(stored.pages),
            sum(1 for p in stored.pages if p.is_selected),
            existing is not None,
        )
        return stored

    # --------------------------------------------------------- selection ---
    async def select_pages(
        self,
        brand_id: UUID,
        account_id: UUID,
        page_ids: Iterable[str],
        *,
        actor_tenant_id: UUID,
    ) -> ConnectedAccount | None:
        """B6: make exactly ``page_ids`` the selected Pages of the connection.

        * actor is not ``connected_by_tenant_id`` ⇒ ``NotConnectionOwnerError`` (403, H1) —
          checked **before** any Graph call;
        * unknown ids ⇒ ``UnknownPageIdsError`` and **no** DB change;
        * requested Page without a Page token ⇒ ``PageNotSelectableError`` (422, H3);
        * expired user token (190) ⇒ ``MetaTokenExpiredError`` after marking the
          connection ``reauth_required`` out of band (caller maps to 401);
        * returns ``None`` when the connection does not exist in this brand.
        """
        account = await self._repository.get(brand_id, account_id)
        if account is None:
            return None
        if account.connected_by_tenant_id != actor_tenant_id:
            raise NotConnectionOwnerError()
        requested = list(dict.fromkeys(page_ids))

        user_token = self._encryption.decrypt_token(account.encrypted_user_token)
        try:
            fresh = await self._client.get_user_pages(user_token)
        except MetaTokenExpiredError:
            await self._mark_reauth_required(brand_id, account_id)
            raise
        finally:
            del user_token

        known = {p.page_id for p in fresh}
        unknown = [pid for pid in requested if pid not in known]
        if unknown:
            raise UnknownPageIdsError(unknown)
        tokenless = [
            p.page_id for p in fresh if p.page_id in requested and p.page_access_token is None
        ]
        if tokenless:
            raise PageNotSelectableError(tokenless)

        new_pages = [self._to_page_asset(p, selected=p.page_id in requested) for p in fresh]
        _assert_invariant(new_pages)
        await self._repository.replace_pages(brand_id, account_id, new_pages)
        updated = await self._repository.get(brand_id, account_id)
        logger.info(
            "meta page selection updated: brand=%s selected=%d of %d",
            brand_id,
            len(requested),
            len(new_pages),
        )
        return updated

    # ------------------------------------------------------------- reads ---
    async def list_connections(self, brand_id: UUID) -> list[ConnectedAccount]:
        return await self._repository.list_by_brand(brand_id)

    async def get_connection(self, brand_id: UUID, account_id: UUID) -> ConnectedAccount | None:
        return await self._repository.get(brand_id, account_id)

    async def disconnect(
        self,
        brand_id: UUID,
        account_id: UUID,
        *,
        actor_tenant_id: UUID,
        actor_role: BrandRole,
    ) -> bool:
        """Remove the stored connection (DB only — no Meta call in this sprint).

        H1: allowed to the brand OWNER (may evict an agency) and to the connection owner;
        a MANAGER may only remove connections it made itself ⇒ ``NotConnectionOwnerError``.
        """
        account = await self._repository.get(brand_id, account_id)
        if account is None:
            return False
        if actor_role != BrandRole.OWNER and account.connected_by_tenant_id != actor_tenant_id:
            raise NotConnectionOwnerError()
        return await self._repository.delete(brand_id, account_id)

    def decrypt_stored_token(self, encrypted_token: str) -> str:
        """Helper for downstream modules that need the raw token transiently."""
        return self._encryption.decrypt_token(encrypted_token)

    # ------------------------------------------------- SPRINT-05 B2: resync ---
    async def resync(
        self, brand_id: UUID, account_id: UUID, *, actor_tenant_id: UUID
    ) -> ResyncResult | None:
        """B2: re-validate the stored user token and refresh the Page set from Meta.

        Order (no Graph call before the ownership check):
        1. ``None`` when the connection is not in this brand (caller ⇒ 404);
        2. actor ≠ ``connected_by_tenant_id`` ⇒ ``NotConnectionOwnerError`` (403);
        3. ``REVOKED`` ⇒ ``ConnectionRevokedError`` (409) — never resurrected;
        4. ``/debug_token`` (graceful) ⇒ ``token_expires_at`` / ``granted_scopes`` (``None``
           keeps the stored scopes); ``is_valid == False`` ⇒ ``reauth_required`` out of band
           + ``MetaTokenExpiredError`` (401 ``reconnect``) — same for a 190 anywhere;
        5. ``/me/accounts`` ⇒ **merge policy**: a Page that was selected *and* is still
           returned with a token stays selected with the **new** token; a selected Page Meta
           no longer returns is dropped and reported in ``removed_selected_page_ids``;
           unselected Pages keep metadata only; a single Page is auto-selected (B6).
        ``status`` is left as is (``ACTIVE`` stays ``ACTIVE``; ``REAUTH_REQUIRED`` is only
        cleared by a fresh OAuth). The user token plaintext lives only inside this method.
        """
        account = await self._repository.get(brand_id, account_id)
        if account is None:
            return None
        if account.connected_by_tenant_id != actor_tenant_id:
            raise NotConnectionOwnerError()
        if account.status is AccountStatus.REVOKED:
            raise ConnectionRevokedError()

        user_token = self._encryption.decrypt_token(account.encrypted_user_token)
        try:
            try:
                debug = await self._inspect_token(user_token)
                if debug is not None and not debug.is_valid:
                    raise MetaTokenExpiredError(
                        "debug_token reports the stored access token as invalid",
                        code=debug.error_code or 190,
                        error_type="OAuthException",
                        http_status=200,
                    )
                fresh = await self._client.get_user_pages(user_token)
            except MetaTokenExpiredError:
                await self._mark_reauth_required(brand_id, account_id)
                raise
        finally:
            del user_token

        previously_selected = {p.page_id for p in account.pages if p.is_selected}
        new_pages = self._apply_selection_policy(fresh, previously_selected)
        _assert_invariant(new_pages)
        still_selected = {p.page_id for p in new_pages if p.is_selected}
        removed = sorted(previously_selected - still_selected)
        if removed:
            logger.warning(
                "meta resync dropped selected pages no longer returned by Meta: "
                "brand=%s connection=%s page_ids=%s",
                brand_id,
                account_id,
                removed,
            )

        now = datetime.now(tz=UTC)
        await self._repository.update_token_metadata(
            brand_id,
            account_id,
            token_expires_at=_expiry_from_debug(debug) if debug else account.token_expires_at,
            granted_scopes=list(debug.scopes) if debug and debug.scopes else None,
            last_synced_at=now,
        )
        await self._repository.replace_pages(brand_id, account_id, new_pages)
        updated = await self._repository.get(brand_id, account_id)
        if updated is None:  # pragma: no cover - row existed a moment ago in this transaction
            return None
        logger.info(
            "meta connection resynced: brand=%s connection=%s pages=%d selected=%d removed=%d",
            brand_id,
            account_id,
            len(new_pages),
            len(still_selected),
            len(removed),
        )
        return ResyncResult(account=updated, removed_selected_page_ids=removed)

    # ------------------------------------- SPRINT-05 B3/B4: operational reads ---
    async def get_page_posts(
        self,
        brand_id: UUID,
        account_id: UUID,
        page_id: str,
        *,
        limit: int,
        after: str | None,
    ) -> PagePostsPage | None:
        """B3: one page of posts of a **selected** Page (read-through, never cached).

        ``None`` ⇒ connection not in brand (404). ``PageNotSelectedError`` (404) for any
        Page that is not selected-with-token — identical for every role. ``REAUTH_REQUIRED``
        ⇒ ``ReauthRequiredError`` (409) and ``REVOKED`` ⇒ ``ConnectionRevokedError`` (409)
        *before* any Graph call. A 190 during the read marks ``reauth_required`` out of band.
        """
        account = await self._repository.get(brand_id, account_id)
        if account is None:
            return None
        page = _selected_page(account, page_id)
        payload = await self._read_with_page_token(
            account,
            page,
            lambda token: self._client.get_page_posts(token, page_id, limit=limit, after=after),
        )
        return PagePostsPage(
            items=[PagePostPublic.model_validate(n) for n in _nodes(payload)],
            **_cursor(payload),
        )

    async def get_instagram_media(
        self,
        brand_id: UUID,
        account_id: UUID,
        page_id: str,
        *,
        limit: int,
        after: str | None,
    ) -> InstagramMediaPage | None:
        """B4: one page of media of the Instagram account linked to a **selected** Page.

        Same guards as :meth:`get_page_posts`; a selected Page without a linked Instagram
        Business account ⇒ ``InstagramNotLinkedError`` (404 ``instagram_not_linked``).
        """
        account = await self._repository.get(brand_id, account_id)
        if account is None:
            return None
        page = _selected_page(account, page_id)
        if page.instagram is None:
            raise InstagramNotLinkedError(page_id)
        ig_user_id = page.instagram.ig_user_id
        payload = await self._read_with_page_token(
            account,
            page,
            lambda token: self._client.get_instagram_media(
                token, ig_user_id, limit=limit, after=after
            ),
        )
        return InstagramMediaPage(
            items=[InstagramMediaPublic.model_validate(n) for n in _nodes(payload)],
            **_cursor(payload),
        )

    async def _read_with_page_token(
        self,
        account: ConnectedAccount,
        page: PageAsset,
        call: Callable[[str], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """B5: decrypt the Page token at the point of use, never keep it beyond the call;
        status guards run *before* decryption so no Graph request happens for a dead
        connection (B3-U5). A 190 ⇒ ``reauth_required`` persisted out of band (H3)."""
        if account.status is AccountStatus.REAUTH_REQUIRED:
            raise ReauthRequiredError()
        if account.status is AccountStatus.REVOKED:
            raise ConnectionRevokedError()
        assert page.encrypted_page_token is not None  # noqa: S101 - guaranteed by _selected_page
        page_token = self._encryption.decrypt_token(page.encrypted_page_token)
        try:
            return await call(page_token)
        except MetaTokenExpiredError:
            await self._mark_reauth_required(account.brand_id, account.id)
            raise
        finally:
            del page_token

    # ----------------------------------------------------------- helpers ---
    async def _mark_reauth_required(self, brand_id: UUID, account_id: UUID) -> None:
        """H3: persist ``reauth_required`` so it survives the request's rollback."""
        if self._status_marker is not None:
            await self._status_marker.mark_out_of_band(
                brand_id, account_id, AccountStatus.REAUTH_REQUIRED
            )
        else:  # no marker wired (bare service): best effort in the caller's session
            await self._repository.set_status(brand_id, account_id, AccountStatus.REAUTH_REQUIRED)

    async def _inspect_token(self, long_lived: str) -> MetaDebugTokenInfo | None:
        """``/debug_token`` with graceful degradation (SPRINT-02 §5-C step 3).

        A 190 (invalid/expired token) is fatal — the token we just received is unusable.
        Any other Graph failure is logged (without the token) and treated as *unknown*
        scopes/expiry; identity is unaffected because it comes from ``/me``.

        SPRINT-05 A-0: *operational* failures (network, transient, rate limit) stay a
        WARNING — they heal by themselves. Code 100 (``GraphMethodException`` — wrong HTTP
        method / unsupported call) or any unclassified ``MetaGraphError`` means **our**
        request is wrong: ERROR log + ``counters["meta_debug_token_failures"]`` so a
        misconfiguration is visible instead of silently degrading every onboarding.
        """
        try:
            return await self._client.debug_token(long_lived)
        except MetaTokenExpiredError:
            raise
        except (MetaNetworkError, MetaTransientError, MetaRateLimitError) as exc:
            logger.warning(
                "debug_token failed; continuing with unknown identity/expiry: %s "
                "(code=%s, http=%s)",
                type(exc).__name__,
                exc.code,
                exc.http_status,
            )
            return None
        except MetaGraphError as exc:
            counters.increment("meta_debug_token_failures")
            logger.error(
                # ``graph code`` (not ``code=``) so the SecretRedactingFilter leaves it readable.
                "debug_token misconfiguration: %s (graph code %s, http %s) — continuing with "
                "unknown scopes/expiry; check the /debug_token transport (method=GET override)",
                type(exc).__name__,
                exc.code,
                exc.http_status,
            )
            return None

    def _apply_selection_policy(
        self, pages: list[MetaPageInfo], keep_selected: set[str]
    ) -> list[PageAsset]:
        if len(pages) == 1:
            return [self._to_page_asset(pages[0], selected=True)]
        return [self._to_page_asset(p, selected=p.page_id in keep_selected) for p in pages]

    def _to_page_asset(self, page: MetaPageInfo, *, selected: bool) -> PageAsset:
        # Only a *selected* Page keeps its token. A Page without ``access_token`` (user
        # lacks a Page role, F4) can never be selected — nothing to encrypt
        # (``encrypt_token("")`` would raise) and nothing to act with.
        raw_page_token = (
            page.page_access_token.get_secret_value() if page.page_access_token else None
        )
        is_selected = selected and raw_page_token is not None
        encrypted_page_token = (
            self._encryption.encrypt_token(raw_page_token)
            if is_selected and raw_page_token
            else None
        )
        instagram = (
            InstagramAsset(
                ig_user_id=page.instagram_business_account_id,
                username=page.instagram_username,
                name=page.instagram_name,
                profile_picture_url=page.instagram_profile_picture_url,
            )
            if page.instagram_business_account_id
            else None
        )
        return PageAsset(
            page_id=page.page_id,
            page_name=page.page_name,
            category=page.category,
            tasks=list(page.permissions),
            encrypted_page_token=encrypted_page_token,
            is_selected=is_selected,
            instagram=instagram,
        )


def _selected_page(account: ConnectedAccount, page_id: str) -> PageAsset:
    """§2-0(b): only a *selected* Page with a stored token is a brand resource; anything else
    (unknown id, unselected, token-less) is the same 404 for every role."""
    for page in account.pages:
        if page.page_id == page_id and page.is_selected and page.encrypted_page_token:
            return page
    raise PageNotSelectedError(page_id)


def _nodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    return [n for n in data if isinstance(n, dict)] if isinstance(data, list) else []


def _cursor(payload: dict[str, Any]) -> dict[str, Any]:
    """Opaque pagination only: ``paging.cursors.after`` when (and only when) Meta says there
    is a next page. The ``paging.next`` URL (carrying ``access_token``) is dropped here and
    never logged, followed or returned (§2-0(a))."""
    paging = payload.get("paging")
    if not isinstance(paging, dict) or not paging.get("next"):
        return {"next_cursor": None, "has_more": False}
    cursors = paging.get("cursors")
    after = cursors.get("after") if isinstance(cursors, dict) else None
    if not isinstance(after, str) or not after:
        return {"next_cursor": None, "has_more": False}
    return {"next_cursor": after, "has_more": True}


def _assert_invariant(pages: list[PageAsset]) -> None:
    """B6 invariant: ``NOT is_selected ⇒ encrypted_page_token IS NULL``."""
    offenders = [p.page_id for p in pages if not p.is_selected and p.encrypted_page_token]
    if offenders:  # pragma: no cover - construction above makes this unreachable
        raise SelectionInvariantError(f"unselected pages with token: {offenders}")


def _expiry_from_debug(debug: MetaDebugTokenInfo) -> datetime | None:
    """``expires_at`` of 0 / missing means *unknown* (D-055) → ``None``."""
    if not debug.expires_at:
        return None
    return datetime.fromtimestamp(debug.expires_at, tz=UTC)
