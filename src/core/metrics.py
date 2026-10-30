"""Minimal in-process counters (SPRINT-05 A-0).

A thread-safe ``dict`` of named integer counters — no new dependency, no exporter. The
first consumer is ``meta_debug_token_failures``: a misconfigured ``/debug_token`` call
(Graph code 100 or an unclassified error) must be *visible* instead of silently degrading
the onboarding flow to "unknown scopes/expiry". A later sprint may expose these through a
``/metrics`` endpoint or forward them to a real metrics backend.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Final

_DEFAULT_COUNTERS: Final[tuple[str, ...]] = ("meta_debug_token_failures",)


class Counters:
    """Thread-safe mapping ``name -> int`` with ``increment`` / ``get`` / ``reset``."""

    def __init__(self, names: tuple[str, ...] = _DEFAULT_COUNTERS) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, int] = dict.fromkeys(names, 0)

    def increment(self, name: str, amount: int = 1) -> int:
        """Add ``amount`` to ``name`` (created at 0 if unknown) and return the new value."""
        with self._lock:
            value = self._values.get(name, 0) + amount
            self._values[name] = value
            return value

    def get(self, name: str) -> int:
        with self._lock:
            return self._values.get(name, 0)

    def __getitem__(self, name: str) -> int:
        return self.get(name)

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(list(self._values))

    def reset(self) -> None:
        """Zero every counter (tests only)."""
        with self._lock:
            for key in self._values:
                self._values[key] = 0

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._values)


counters = Counters()
"""Process-wide counters (``counters["meta_debug_token_failures"]``)."""
