"""Argon2id password hashing (A4) — ``argon2-cffi`` library defaults (RFC 9106 low-memory).

A module-level ``DUMMY_HASH`` lets the service run a real verification when the user
does not exist, so "unknown email" and "wrong password" take the same time (A-U4).
"""

from __future__ import annotations

from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


class Argon2PasswordHasher:
    """``PasswordHasher`` Protocol implementation backed by ``argon2.PasswordHasher()``."""

    def __init__(self) -> None:
        self._hasher = _Argon2()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True


# Hash of an unguessable random value, computed once per process (used for timing parity).
DUMMY_HASH: str = Argon2PasswordHasher().hash("dummy-password-never-matches-\x00")
