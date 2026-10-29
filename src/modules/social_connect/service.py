"""Application service orchestrating the Meta onboarding flow.

Responsibilities
----------------
1. Produce the Facebook Login authorize URL (with CSRF ``state``).
2. Exchange the returned ``code`` for a long-lived user token.
3. Fetch the user's Pages + linked Instagram Business accounts.
4. Encrypt the token **before** it leaves this layer (nothing plaintext is returned).

Persistence of the resulting ``MetaConnectionResult`` is intentionally out of scope
for this sprint and will be added behind a repository interface.
"""

from __future__ import annotations

from src.core.security import TokenEncryptionService
from src.modules.social_connect.meta_client import MetaGraphClient
from src.modules.social_connect.schemas import (
    MetaAuthorizeUrlResponse,
    MetaAuthRequest,
    MetaConnectionResult,
    MetaPageInfo,
    MetaTokenResponse,
)


class MetaConnectService:
    """Use-case layer for connecting a Meta (Facebook/Instagram) account."""

    def __init__(self, client: MetaGraphClient, encryption: TokenEncryptionService) -> None:
        self._client = client
        self._encryption = encryption

    def build_authorize_url(
        self, redirect_uri: str, state: str | None = None
    ) -> MetaAuthorizeUrlResponse:
        url, state_value = self._client.build_authorize_url(redirect_uri, state=state)
        return MetaAuthorizeUrlResponse(authorize_url=url, state=state_value)

    async def exchange_code(self, request: MetaAuthRequest) -> MetaTokenResponse:
        return await self._client.exchange_code_for_token(
            code=request.code, redirect_uri=str(request.redirect_uri)
        )

    async def list_pages(self, user_access_token: str) -> list[MetaPageInfo]:
        return await self._client.get_user_pages(user_access_token)

    async def connect(self, request: MetaAuthRequest) -> MetaConnectionResult:
        """Full flow: code → long-lived token → pages → encrypted result."""
        token = await self.exchange_code(request)
        raw_long_lived = token.long_lived_token.get_secret_value()
        pages = await self.list_pages(raw_long_lived)
        encrypted = self._encryption.encrypt_token(raw_long_lived)
        return MetaConnectionResult(
            encrypted_user_token=encrypted,
            token_expires_at=token.expires_at,
            pages=pages,
            instagram_accounts=[
                page.instagram_business_account_id
                for page in pages
                if page.instagram_business_account_id
            ],
        )

    def decrypt_stored_token(self, encrypted_token: str) -> str:
        """Helper for downstream modules that need the raw token transiently."""
        return self._encryption.decrypt_token(encrypted_token)
