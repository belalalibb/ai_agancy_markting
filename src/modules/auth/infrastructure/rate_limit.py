"""In-memory rate limiter (A8) — sliding window per (bucket, key) + consecutive-failure blocks.

Process-local by design (single-instance deployment for now); swap for Redis behind the
same ``RateLimiter`` Protocol when scaling out. Never used in tests (fake dependency).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class InMemoryRateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._failures: dict[tuple[str, str], int] = defaultdict(int)
        self._blocked_until: dict[tuple[str, str], float] = {}

    def hit(self, bucket: str, key: str, limit: int, window_seconds: int) -> int | None:
        now = self._clock()
        with self._lock:
            window = self._hits[(bucket, key)]
            while window and window[0] <= now - window_seconds:
                window.popleft()
            if len(window) >= limit:
                return max(1, int(window[0] + window_seconds - now) + 1)
            window.append(now)
            return None

    def record_failure(self, bucket: str, key: str, limit: int, block_seconds: int) -> None:
        with self._lock:
            self._failures[(bucket, key)] += 1
            if self._failures[(bucket, key)] >= limit:
                self._blocked_until[(bucket, key)] = self._clock() + block_seconds
                self._failures[(bucket, key)] = 0

    def blocked_for(self, bucket: str, key: str) -> int | None:
        with self._lock:
            until = self._blocked_until.get((bucket, key))
            if until is None:
                return None
            remaining = until - self._clock()
            if remaining <= 0:
                del self._blocked_until[(bucket, key)]
                return None
            return max(1, int(remaining) + 1)

    def reset_failures(self, bucket: str, key: str) -> None:
        with self._lock:
            self._failures.pop((bucket, key), None)
            self._blocked_until.pop((bucket, key), None)
