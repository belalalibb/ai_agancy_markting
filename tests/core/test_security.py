"""Unit tests — TokenEncryptionService (Fernet / authenticated encryption)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from src.core.config import Settings
from src.core.security import (
    EncryptionKeyMissingError,
    TokenEncryptionError,
    TokenEncryptionService,
    get_token_encryption_service,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "raw",
    [
        "EAABsbCS1iHgBO7ZCZCZA...",
        "a",
        "x" * 4096,
        "توكن-عربي-🔐-unicode",
    ],
)
def test_encrypt_decrypt_roundtrip(encryption: TokenEncryptionService, raw: str) -> None:
    cipher = encryption.encrypt_token(raw)
    assert cipher != raw
    assert encryption.decrypt_token(cipher) == raw


def test_ciphertext_is_non_deterministic(encryption: TokenEncryptionService) -> None:
    """Fernet uses a random IV — identical plaintexts must not yield identical ciphertexts."""
    c1 = encryption.encrypt_token("same-token")
    c2 = encryption.encrypt_token("same-token")
    assert c1 != c2
    assert encryption.decrypt_token(c1) == encryption.decrypt_token(c2) == "same-token"


def test_ciphertext_does_not_leak_plaintext(encryption: TokenEncryptionService) -> None:
    raw = "SUPER_SECRET_TOKEN_VALUE"
    assert raw not in encryption.encrypt_token(raw)


def test_tampered_ciphertext_is_rejected(encryption: TokenEncryptionService) -> None:
    cipher = encryption.encrypt_token("token")
    tampered = cipher[:-4] + ("AAAA" if not cipher.endswith("AAAA") else "BBBB")
    with pytest.raises(TokenEncryptionError):
        encryption.decrypt_token(tampered)


def test_wrong_key_cannot_decrypt(encryption: TokenEncryptionService) -> None:
    other = TokenEncryptionService(Fernet.generate_key())
    cipher = encryption.encrypt_token("token")
    with pytest.raises(TokenEncryptionError):
        other.decrypt_token(cipher)


@pytest.mark.parametrize("bad", ["", None, 123])
def test_encrypt_rejects_invalid_input(encryption: TokenEncryptionService, bad: object) -> None:
    with pytest.raises(TokenEncryptionError):
        encryption.encrypt_token(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["", None, "not-a-fernet-token"])
def test_decrypt_rejects_invalid_input(encryption: TokenEncryptionService, bad: object) -> None:
    with pytest.raises(TokenEncryptionError):
        encryption.decrypt_token(bad)  # type: ignore[arg-type]


def test_invalid_key_raises() -> None:
    with pytest.raises(TokenEncryptionError):
        TokenEncryptionService("definitely-not-a-valid-key")


def test_empty_key_list_raises() -> None:
    with pytest.raises(EncryptionKeyMissingError):
        TokenEncryptionService([])


def test_key_rotation_decrypts_old_and_reencrypts_with_new() -> None:
    old_key = Fernet.generate_key()
    new_key = Fernet.generate_key()
    legacy = TokenEncryptionService(old_key)
    cipher_old = legacy.encrypt_token("token")

    rotated_service = TokenEncryptionService([new_key, old_key])
    assert rotated_service.decrypt_token(cipher_old) == "token"
    cipher_new = rotated_service.rotate(cipher_old)
    assert TokenEncryptionService(new_key).decrypt_token(cipher_new) == "token"
    with pytest.raises(TokenEncryptionError):
        TokenEncryptionService(new_key).decrypt_token(cipher_old)


def test_rotate_rejects_invalid_ciphertext(encryption: TokenEncryptionService) -> None:
    with pytest.raises(TokenEncryptionError):
        encryption.rotate("garbage")


def test_generate_key_is_valid() -> None:
    key = TokenEncryptionService.generate_key()
    assert TokenEncryptionService.is_valid_key(key)
    assert not TokenEncryptionService.is_valid_key("short")
    assert not TokenEncryptionService.is_valid_key("not base64 !!!")
    TokenEncryptionService(key).encrypt_token("ok")


def test_factory_uses_settings_key(settings: Settings) -> None:
    service = get_token_encryption_service(settings)
    assert service.decrypt_token(service.encrypt_token("abc")) == "abc"


def test_factory_fails_fast_without_key(settings: Settings) -> None:
    no_key = settings.model_copy(update={"token_encryption_key": None})
    with pytest.raises(EncryptionKeyMissingError):
        get_token_encryption_service(no_key)
