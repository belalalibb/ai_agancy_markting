"""Account service + token provider for ``whatsapp`` (SPRINT-09 B-1).

* :class:`WhatsAppAccountService` — BYOK account lifecycle: ``connect_account`` encrypts the
  three secrets (system token, app secret, verify token hash) and verifies the token with
  exactly one ``GET /{phone_number_id}`` before any row is written (D-064).
* :class:`PostgresWhatsAppTokenProvider` — resolves an account into a
  :class:`ResolvedWhatsAppTarget`; the decrypted token / secret are ``repr=False`` and the
  caller ``del``-s them in ``finally``.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from src.core.security import TokenEncryptionService
from src.modules.whatsapp.client import WhatsAppCloudClient
from src.modules.whatsapp.domain.models import AccountStatus, WhatsAppAccount
from src.modules.whatsapp.domain.repository import WhatsAppRepository
from src.modules.whatsapp.exceptions import (
    AccountAlreadyExistsError,
    AccountNotFoundError,
    CredentialsInvalidError,
    WhatsAppClientError,
)

logger = logging.getLogger(__name__)


def hash_verify_token(verify_token: str) -> str:
    """SHA-256 hex of the webhook verify token (the plaintext is never stored)."""
    return hashlib.sha256(verify_token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ResolvedWhatsAppTarget:
    account_id: UUID
    brand_id: UUID
    phone_number_id: str
    token: str = field(repr=False)
    app_secret: str = field(repr=False)


class PostgresWhatsAppTokenProvider:
    """``account_id`` ⇒ decrypted credentials of an *active* account (``None`` otherwise)."""

    def __init__(self, repo: WhatsAppRepository, encryption: TokenEncryptionService) -> None:
        self._repo = repo
        self._encryption = encryption

    async def resolve(self, account_id: UUID) -> ResolvedWhatsAppTarget | None:
        account = await self._repo.get_account_by_id(account_id)
        if account is None or account.status is not AccountStatus.ACTIVE:
            return None
        return ResolvedWhatsAppTarget(
            account_id=account.id,
            brand_id=account.brand_id,
            phone_number_id=account.phone_number_id,
            token=self._encryption.decrypt_token(account.encrypted_system_token),
            app_secret=self._encryption.decrypt_token(account.encrypted_app_secret),
        )

    async def resolve_by_phone_number_id(
        self, phone_number_id: str
    ) -> ResolvedWhatsAppTarget | None:
        account = await self._repo.get_account_by_phone_number_id(phone_number_id)
        if account is None or account.status is not AccountStatus.ACTIVE:
            return None
        return await self.resolve(account.id)


class WhatsAppAccountService:
    def __init__(
        self,
        repo: WhatsAppRepository,
        client: WhatsAppCloudClient,
        encryption: TokenEncryptionService,
    ) -> None:
        self._repo = repo
        self._client = client
        self._encryption = encryption

    async def connect_account(
        self,
        brand_id: UUID,
        actor_user_id: UUID,
        *,
        phone_number_id: str,
        waba_id: str,
        display_phone: str,
        system_token: str,
        app_secret: str,
        verify_token: str,
    ) -> WhatsAppAccount:
        """OWNER only (enforced by the router). Verifies the token with one Graph ``GET``;
        any client failure ⇒ 422 ``whatsapp_credentials_invalid`` and **no row**."""
        if await self._repo.get_account(brand_id) is not None:
            raise AccountAlreadyExistsError()
        try:
            info = await self._client.describe_phone_number(system_token, phone_number_id)
        except WhatsAppClientError as exc:
            logger.info(
                "wa connect rejected: brand=%s phone_number_id=%s error=%s code=%s",
                brand_id,
                phone_number_id,
                type(exc).__name__,
                exc.code,
            )
            raise CredentialsInvalidError() from exc
        now = datetime.now(tz=UTC)
        account = WhatsAppAccount(
            brand_id=brand_id,
            phone_number_id=phone_number_id,
            waba_id=waba_id,
            display_phone=display_phone or info.display_phone_number,
            encrypted_system_token=self._encryption.encrypt_token(system_token),
            encrypted_app_secret=self._encryption.encrypt_token(app_secret),
            verify_token_hash=hash_verify_token(verify_token),
            created_by_user_id=actor_user_id,
            created_at=now,
            updated_at=now,
        )
        created = await self._repo.create_account(account)
        logger.info("wa account connected: brand=%s account=%s", brand_id, created.id)
        return created

    async def get_account(self, brand_id: UUID) -> WhatsAppAccount:
        account = await self._repo.get_account(brand_id)
        if account is None:
            raise AccountNotFoundError()
        return account

    async def disable_account(self, brand_id: UUID) -> WhatsAppAccount:
        account = await self.get_account(brand_id)
        now = datetime.now(tz=UTC)
        await self._repo.set_account_status(account.id, AccountStatus.DISABLED, now=now)
        logger.info("wa account disabled: brand=%s account=%s", brand_id, account.id)
        return account.model_copy(update={"status": AccountStatus.DISABLED, "updated_at": now})
