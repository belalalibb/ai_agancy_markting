"""Consumer-owned port for the ``monthly_wa_conversations`` plan counter (SPRINT-11.1 A-2,
D-016 / D-076).

``whatsapp`` never imports ``entitlements``. The webhook asks *"is this brand over its
monthly conversations?"* when the **first** inbound message of a new conversation arrives;
``suggest-reply`` and the outbound sends ask the same question before acting on a
conversation. ``src/main.py`` binds the concrete adapter built in
``entitlements/dependencies.py`` by overriding :func:`get_counter_guard`; the default
(``None`` ⇒ no check) is what runs when the module is removed.

Meta semantics: a **signed webhook never answers 429** (Meta would retry forever) — the
message is stored, the conversation is flagged ``quota_exceeded`` in the response, and only
the agent-side routes refuse with **402 ``entitlement_quota_exceeded``**.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class CounterQuotaExceededError(Exception):
    """``monthly_wa_conversations`` ceiling reached ⇒ 402 ``entitlement_quota_exceeded``
    ``{counter, limit, used}`` on the agent-side routes (never on the webhook)."""

    def __init__(self, counter: str, *, limit: int, used: int) -> None:
        super().__init__(f"{counter} quota exceeded: {used}/{limit}")
        self.counter = counter
        self.limit = limit
        self.used = used


class CounterGuard(Protocol):
    async def ensure_within_conversation_quota(self, brand_id: UUID) -> None:
        """Raises :class:`CounterQuotaExceededError` when the brand's stored conversations of the
        month already exceed its ceiling (the one just stored included)."""
        ...


def get_counter_guard() -> CounterGuard | None:
    """FastAPI dependency — overridden from ``src/main.py``; ``None`` ⇒ no quota check."""
    return None


__all__ = ["CounterGuard", "CounterQuotaExceededError", "get_counter_guard"]
