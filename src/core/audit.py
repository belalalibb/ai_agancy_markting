"""Consumer-owned audit port (SPRINT-13 A-1, D-016 pattern of ``counter_guard_dependency``).

Admin routers of other modules call ``audit.record(...)`` through this protocol; ``src/main.py``
binds the ``ops`` PostgreSQL writer (same request session ⇒ same transaction, D-055). Without
``ops`` the dependency yields ``None`` and nothing is recorded — see ops/REMOVABILITY.md.
Payloads carry ids and counts only, never customer text or secrets (D-069).
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID


class AuditPort(Protocol):
    async def record(
        self,
        *,
        actor_user_id: UUID | None,
        action: str,
        target_kind: str,
        target_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None: ...


def get_audit_port() -> AuditPort | None:
    """FastAPI dependency — overridden from ``src/main.py``; ``None`` ⇒ no audit row."""
    return None


async def audit(port: AuditPort | None, **fields: Any) -> None:
    """``await audit(port, actor_user_id=…, action=…, target_kind=…, target_id=…)``."""
    if port is not None:
        await port.record(**fields)


__all__ = ["AuditPort", "audit", "get_audit_port"]
