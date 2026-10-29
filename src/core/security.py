"""Token encryption primitives.

``TokenEncryptionService`` wraps ``cryptography.fernet`` (AES-128-CBC + HMAC-SHA256,
authenticated encryption) so that third-party access tokens are **never** persisted
in plaintext. Key material is supplied via ``TOKEN_ENCRYPTION_KEY`` and never logged.
"""

from __future__ import annotations

import base64
import binascii

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from src.core.config import Settings, get_settings


class TokenEncryptionError(Exception):
    """Raised when a token cannot be encrypted or decrypted."""


class EncryptionKeyMissingError(TokenEncryptionError):
    """Raised when no encryption key is configured."""


class TokenEncryptionService:
    """Symmetric, authenticated encryption for sensitive tokens.

    Supports key rotation: pass several keys — the first is used to encrypt,
    all of them are tried (in order) when decrypting.
    """

    __slots__ = ("_fernet",)

    def __init__(self, keys: str | bytes | list[str | bytes]) -> None:
        raw_keys = keys if isinstance(keys, list) else [keys]
        if not raw_keys:
            raise EncryptionKeyMissingError("At least one encryption key is required.")
        fernets: list[Fernet] = []
        for key in raw_keys:
            try:
                fernets.append(Fernet(key))
            except (ValueError, binascii.Error, TypeError) as exc:
                msg = "Invalid Fernet key: expected 32 url-safe base64-encoded bytes."
                raise TokenEncryptionError(msg) from exc
        self._fernet = MultiFernet(fernets)

    # ------------------------------------------------------------------ API --
    def encrypt_token(self, raw_token: str) -> str:
        """Encrypt ``raw_token`` and return a URL-safe ciphertext string."""
        if not isinstance(raw_token, str) or raw_token == "":
            raise TokenEncryptionError("raw_token must be a non-empty string.")
        return self._fernet.encrypt(raw_token.encode("utf-8")).decode("ascii")

    def decrypt_token(self, cipher_token: str) -> str:
        """Decrypt a ciphertext produced by :meth:`encrypt_token`.

        Raises:
            TokenEncryptionError: if the ciphertext is malformed, tampered with,
                or encrypted with an unknown key.
        """
        if not isinstance(cipher_token, str) or cipher_token == "":
            raise TokenEncryptionError("cipher_token must be a non-empty string.")
        try:
            return self._fernet.decrypt(cipher_token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise TokenEncryptionError(
                "Token could not be decrypted (invalid or tampered)."
            ) from exc

    def rotate(self, cipher_token: str) -> str:
        """Re-encrypt an existing ciphertext with the primary (newest) key."""
        try:
            return self._fernet.rotate(cipher_token.encode("ascii")).decode("ascii")
        except InvalidToken as exc:
            raise TokenEncryptionError(
                "Token could not be rotated (invalid or tampered)."
            ) from exc

    # -------------------------------------------------------------- helpers --
    @staticmethod
    def generate_key() -> str:
        """Generate a fresh Fernet key (for ``.env`` provisioning)."""
        return Fernet.generate_key().decode("ascii")

    @staticmethod
    def is_valid_key(key: str) -> bool:
        try:
            return len(base64.urlsafe_b64decode(key.encode("ascii"))) == 32
        except (binascii.Error, ValueError, UnicodeEncodeError):
            return False


def get_token_encryption_service(settings: Settings | None = None) -> TokenEncryptionService:
    """Factory used by FastAPI dependencies; fails fast if the key is absent."""
    settings = settings or get_settings()
    if settings.token_encryption_key is None:
        raise EncryptionKeyMissingError(
            "TOKEN_ENCRYPTION_KEY is not configured. "
            "Generate one with TokenEncryptionService.generate_key()."
        )
    return TokenEncryptionService(settings.token_encryption_key.get_secret_value())
