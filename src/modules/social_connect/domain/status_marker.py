"""Out-of-band status writer (SPRINT-04.1 H3).

``select_pages`` must persist ``reauth_required`` *and then* raise so the caller maps the
failure to 401 — but the request-scoped session is rolled back on any exception, which
silently discarded the status in SPRINT-03. The service therefore writes that one status
through a :class:`StatusMarker` that uses its **own** short transaction, independent of the
request session. Tests inject a fake; production wires ``PostgresStatusMarker``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from src.modules.social_connect.domain.models import AccountStatus


@runtime_checkable
class StatusMarker(Protocol):
    async def mark_out_of_band(
        self, brand_id: UUID, account_id: UUID, status: AccountStatus
    ) -> bool:
        """Persist ``status`` in a transaction that survives the caller's rollback."""
        ...
