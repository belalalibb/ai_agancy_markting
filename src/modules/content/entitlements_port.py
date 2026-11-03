"""Consumer-owned port for the monthly plan counters (SPRINT-11.1 A-2, D-016 / D-076).

``content`` never imports ``entitlements``. It declares what it needs — *"may this brand
approve one more post this month?"* — and ``src/main.py`` binds the concrete adapter built in
``entitlements/dependencies.py`` by overriding :func:`get_counter_guard`. When the
``entitlements`` module is removed, the default below (``None`` ⇒ no check) is what runs.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class CounterQuotaExceededError(Exception):
    """Raised by a :class:`CounterGuard` when the monthly ceiling is reached ⇒ the router
    answers **402 ``entitlement_quota_exceeded``** ``{counter, limit, used}``."""

    def __init__(self, counter: str, *, limit: int, used: int) -> None:
        super().__init__(f"{counter} quota exceeded: {used}/{limit}")
        self.counter = counter
        self.limit = limit
        self.used = used


class CounterPlanInactiveError(Exception):
    """SPRINT-12 A-1 (H1): the brand's plan is suspended / expired ⇒ the router answers
    **402 ``entitlement_plan_inactive``** ``{plan, active_until}`` — never 429 (reserved for
    time-bound limits: the client distinguishes *pay* from *wait*)."""

    def __init__(self, *, plan: str | None = None, active_until: str | None = None) -> None:
        super().__init__("plan inactive")
        self.plan = plan
        self.active_until = active_until


class CounterGuard(Protocol):
    """``await guard.ensure_can_approve_post(brand_id)`` ⇒ returns or raises
    :class:`CounterQuotaExceededError` / :class:`CounterPlanInactiveError`."""

    async def ensure_can_approve_post(self, brand_id: UUID) -> None: ...


def get_counter_guard() -> CounterGuard | None:
    """FastAPI dependency — overridden from ``src/main.py``; ``None`` ⇒ no quota check."""
    return None


__all__ = [
    "CounterGuard",
    "CounterPlanInactiveError",
    "CounterQuotaExceededError",
    "get_counter_guard",
]
