"""Learning-consent gate for ``wa_learning_signals`` (SPRINT-11 C-6, D-080).

``BrandConsentReader`` is **defined here** and implemented by ``tenancy``
(``PostgresBrandConsentReader``) — the consumer owns the contract (D-016/D-076).
``none`` ⇒ no new signal row is written for that brand; ``signals_only`` / ``full`` ⇒ written.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

CONSENT_WRITE_ALLOWED: frozenset[str] = frozenset({"signals_only", "full"})


class BrandConsentReader(Protocol):
    async def learning_consent(self, brand_id: UUID) -> str:
        """``signals_only`` (default) / ``none`` / ``full``; unknown brand ⇒ ``signals_only``."""
        ...


class AlwaysConsentReader:
    """Default when no reader is injected (unit fakes, legacy callers) — writes allowed."""

    async def learning_consent(self, brand_id: UUID) -> str:
        return "signals_only"


async def may_write_signal(reader: BrandConsentReader | None, brand_id: UUID) -> bool:
    if reader is None:
        return True
    return (await reader.learning_consent(brand_id)) in CONSENT_WRITE_ALLOWED
