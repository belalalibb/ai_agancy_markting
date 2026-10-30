"""In-memory rate limiter (A8) — sliding window per (bucket, key) + consecutive-failure blocks.

Process-local by design (single-instance deployment for now); swap for Redis behind the
same ``RateLimiter`` Protocol when scaling out. Never used in tests (fake dependency).

Memory bound (SPRINT-04.1 H8): every ``hit`` / ``record_failure`` runs a lazy sweep every
``SWEEP_EVERY`` calls (drops keys whose window is empty and whose block has elapsed), and
the number of tracked keys is capped at ``max_keys`` — on overflow the **least recently
seen** key is evicted (``OrderedDict`` move-to-end on every touch).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

SWEEP_EVERY = 256
DEFAULT_MAX_KEYS = 10_000

Key = tuple[str, str]


@dataclass(slots=True)
class _Entry:
    hits: deque[float] = field(default_factory=deque)
    window_seconds: int = 0
    failures: int = 0
    blocked_until: float | None = None

    def is_idle(self, now: float) -> bool:
        """Nothing left to remember: empty window, no failures, block elapsed."""
        return (
            not self.hits
            and self.failures == 0
            and (self.blocked_until is None or self.blocked_until <= now)
        )


class InMemoryRateLimiter:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        *,
        max_keys: int = DEFAULT_MAX_KEYS,
    ) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._max_keys = max(1, max_keys)
        self._entries: OrderedDict[Key, _Entry] = OrderedDict()
        self._ops = 0

    # ------------------------------------------------------------ public ---
    def hit(self, bucket: str, key: str, limit: int, window_seconds: int) -> int | None:
        now = self._clock()
        with self._lock:
            entry = self._touch((bucket, key), now)
            entry.window_seconds = window_seconds
            window = entry.hits
            while window and window[0] <= now - window_seconds:
                window.popleft()
            if len(window) >= limit:
                return max(1, int(window[0] + window_seconds - now) + 1)
            window.append(now)
            return None

    def record_failure(self, bucket: str, key: str, limit: int, block_seconds: int) -> None:
        now = self._clock()
        with self._lock:
            entry = self._touch((bucket, key), now)
            entry.failures += 1
            if entry.failures >= limit:
                entry.blocked_until = now + block_seconds
                entry.failures = 0

    def blocked_for(self, bucket: str, key: str) -> int | None:
        with self._lock:
            entry = self._entries.get((bucket, key))
            if entry is None or entry.blocked_until is None:
                return None
            remaining = entry.blocked_until - self._clock()
            if remaining <= 0:
                entry.blocked_until = None
                return None
            return max(1, int(remaining) + 1)

    def reset_failures(self, bucket: str, key: str) -> None:
        with self._lock:
            entry = self._entries.get((bucket, key))
            if entry is not None:
                entry.failures = 0
                entry.blocked_until = None

    # ---------------------------------------------------------- internals --
    @property
    def tracked_keys(self) -> int:
        """Number of (bucket, key) pairs currently held (observability / tests)."""
        with self._lock:
            return len(self._entries)

    def _touch(self, key: Key, now: float) -> _Entry:
        """Return the entry for ``key`` (creating it), mark it most-recent, run maintenance."""
        self._ops += 1
        if self._ops % SWEEP_EVERY == 0:
            self._sweep(now)
        entry = self._entries.get(key)
        if entry is None:
            entry = _Entry()
            self._entries[key] = entry
            while len(self._entries) > self._max_keys:
                self._entries.popitem(last=False)  # evict the least recently seen key
        else:
            self._entries.move_to_end(key)
        return entry

    def _sweep(self, now: float) -> None:
        for key in list(self._entries):
            entry = self._entries[key]
            window = entry.hits
            while window and window[0] <= now - entry.window_seconds:
                window.popleft()
            if entry.blocked_until is not None and entry.blocked_until <= now:
                entry.blocked_until = None
            if entry.is_idle(now):
                del self._entries[key]

    def sweep_now(self) -> None:
        """Force the lazy maintenance pass (tests)."""
        with self._lock:
            self._sweep(self._clock())
