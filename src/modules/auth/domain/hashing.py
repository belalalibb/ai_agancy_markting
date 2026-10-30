"""Password hashing contract (A4). Implemented by ``infrastructure/argon2_hasher.py``."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str, password: str) -> bool:
        """``True`` when ``password`` matches; never raises on mismatch/malformed hash."""
        ...

    def needs_rehash(self, password_hash: str) -> bool: ...
