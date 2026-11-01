"""Learning-consent gate for ``learning_signals`` (SPRINT-11 C-6, D-080).

``BrandConsentReader`` is **defined here** and implemented by ``tenancy``
(``PostgresBrandConsentReader``) — the consumer owns the contract (D-016/D-076).
``none`` ⇒ the advisor does not write a new ``learning_signals`` row for that brand.
Any future *cross-brand* learning reader must filter ``full`` only (see
``entitlements/REMOVABILITY.md`` — there is no such reader yet).
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

CONSENT_WRITE_ALLOWED: frozenset[str] = frozenset({"signals_only", "full"})


class BrandConsentReader(Protocol):
    async def learning_consent(self, brand_id: UUID) -> str: ...


async def may_write_signal(reader: BrandConsentReader | None, brand_id: UUID) -> bool:
    if reader is None:
        return True
    return (await reader.learning_consent(brand_id)) in CONSENT_WRITE_ALLOWED
