"""Application service orchestrating the Meta onboarding flow (SPRINT-02 §5-C).

Responsibilities
----------------
1. ``start_connection``    — issue a signed CSRF ``state`` and the Facebook Login URL.
2. ``complete_connection`` — verify ``state`` → exchange ``code`` → identify the user via
   ``/debug_token`` → list Pages (+ IG accounts) → encrypt every token → persist.
3. ``list/get/disconnect`` — tenant-scoped reads/deletes through the repository.

The service never touches SQLAlchemy (ARCHITECTURE_LOCK D-013); persistence is behind
:class:`ConnectedAccountRepository`. Plaintext tokens exist only inside
``complete_connection`` and are never logged or returned.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from src.core.config import Settings
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
from src.modules.social_connect.exceptions import MetaGraphError, MetaTokenExpiredError
from src.modules.social_connect.meta_client import MetaGraphClient
from src.modules.social_connect.schemas import (
    MetaAuthorizeUrlResponse,
    MetaDebugTokenInfo,
    MetaPageInfo,
)

logger = logging.getLogger(__name__)


class MetaConnectService:
    """Use-case layer for connecting a Meta (Facebook/Instagram) account to a tenant."""

    def __init__(
        self,
        client: MetaGraphClient,
        encryption: TokenEncryptionService,
        repository: ConnectedAccountRepository,
        state_signer: OAuthStateSigner,
        settings: Settings,
    ) -> None:
        self._client = client
        self._encryption = encryption
        self._repository = repository
        self._state_signer = state_signer
        self._settings = settings

    # ------------------------------------------------------------- start ---
    def start_connection(self, tenant_id: str) -> MetaAuthorizeUrlResponse:
        state = self._state_signer.issue(tenant_id)
        url, _ = self._client.build_authorize_url(
            self._settings.meta_oauth_redirect_uri, state=state
        )
        return MetaAuthorizeUrlResponse(authorization_url=url, state=state)

    # ---------------------------------------------------------- complete ---
    async def complete_connection(self, state: str, code: str) -> ConnectedAccount:
        """Full callback flow. Raises ``InvalidOAuthStateError`` / ``MetaGraphError``."""
        tenant_id = self._state_signer.verify(state)

        token = await self._client.exchange_code_for_token(
            code=code, redirect_uri=self._settings.meta_oauth_redirect_uri
        )
        long_lived = token.long_lived_token.get_secret_value()

        # Step 3 (§5-C): identity, scopes and expiry come from ``/debug_token``; when it is
        # unavailable we deliberately record *unknown* (``None`` / ``[]``) rather than guess.
        debug = await self._inspect_token(long_lived)
        provider_user_id = debug.user_id if debug and debug.user_id else None
        granted_scopes = list(debug.scopes) if debug else []
        token_expires_at = _expiry_from_debug(debug) if debug else None

        pages = await self._client.get_user_pages(long_lived)

        if provider_user_id is None:
            # ``/debug_token`` did not identify the user; fall back to a stable, derived
            # identity so the unique constraint still de-duplicates re-connections.
            provider_user_id = _fallback_identity(pages)

        account = ConnectedAccount(
            tenant_id=tenant_id,
            provider=Provider.META,
            provider_user_id=provider_user_id,
            provider_user_name=None,
            encrypted_user_token=self._encryption.encrypt_token(long_lived),
            token_expires_at=token_expires_at,
            granted_scopes=granted_scopes,
            status=AccountStatus.ACTIVE,
            connected_at=datetime.now(tz=UTC),
            last_synced_at=datetime.now(tz=UTC),
            pages=[self._to_page_asset(page) for page in pages],
        )
        return await self._repository.upsert(account)

    # ------------------------------------------------------------- reads ---
    async def list_connections(self, tenant_id: str) -> list[ConnectedAccount]:
        return await self._repository.list_by_tenant(tenant_id)

    async def get_connection(self, tenant_id: str, account_id: UUID) -> ConnectedAccount | None:
        return await self._repository.get(tenant_id, account_id)

    async def disconnect(self, tenant_id: str, account_id: UUID) -> bool:
        """Remove the stored connection (DB only — no Meta call in this sprint)."""
        return await self._repository.delete(tenant_id, account_id)

    def decrypt_stored_token(self, encrypted_token: str) -> str:
        """Helper for downstream modules that need the raw token transiently."""
        return self._encryption.decrypt_token(encrypted_token)

    # ----------------------------------------------------------- helpers ---
    async def _inspect_token(self, long_lived: str) -> MetaDebugTokenInfo | None:
        """``/debug_token`` with graceful degradation (SPRINT-02 §5-C step 3).

        A 190 (invalid/expired token) is fatal — the token we just received is unusable.
        Any other Graph failure is logged (without the token) and treated as *unknown*.
        """
        try:
            return await self._client.debug_token(long_lived)
        except MetaTokenExpiredError:
            raise
        except MetaGraphError as exc:
            logger.warning(
                "debug_token failed; continuing with unknown identity/expiry: %s "
                "(code=%s, http=%s)",
                type(exc).__name__,
                exc.code,
                exc.http_status,
            )
            return None

    def _to_page_asset(self, page: MetaPageInfo) -> PageAsset:
        # A Page without ``access_token`` (user lacks a Page role) is stored with an empty
        # ciphertext so the Page is still visible; nothing can be decrypted from it.
        raw_page_token = (
            page.page_access_token.get_secret_value() if page.page_access_token else ""
        )
        encrypted_page_token = (
            self._encryption.encrypt_token(raw_page_token) if raw_page_token else ""
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
            instagram=instagram,
        )


def _expiry_from_debug(debug: MetaDebugTokenInfo) -> datetime | None:
    """``expires_at`` of 0 / missing means *unknown* (D-055) → ``None``."""
    if not debug.expires_at:
        return None
    return datetime.fromtimestamp(debug.expires_at, tz=UTC)


def _fallback_identity(pages: list[MetaPageInfo]) -> str:
    """Derive a deterministic provider identity when ``/debug_token`` is unavailable."""
    if pages:
        return "pages:" + ",".join(sorted(p.page_id for p in pages))[:58]
    return "unknown"
