"""Rate limiting contract (A8). In-memory implementation in ``infrastructure/rate_limit.py``."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RateLimiter(Protocol):
    def hit(self, bucket: str, key: str, limit: int, window_seconds: int) -> int | None:
        """Record one hit; return ``None`` when allowed, else seconds until the bucket frees."""
        ...

    def record_failure(self, bucket: str, key: str, limit: int, block_seconds: int) -> None:
        """Count a consecutive failure; after ``limit`` failures the key is blocked."""
        ...

    def blocked_for(self, bucket: str, key: str) -> int | None:
        """Seconds remaining on an active block for ``key``, or ``None``."""
        ...

    def reset_failures(self, bucket: str, key: str) -> None: ...
