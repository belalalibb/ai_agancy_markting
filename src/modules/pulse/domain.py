"""Pulse domain (SPRINT-12 B-3): alert kinds, severities and the ``Alert`` value."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

AlertKind = Literal[
    "wa_window_closing",  # (1) unanswered conversation, 24h window closes within ≤ 4h
    "wa_slow_replies",  # (2) average reply latency > 2h over 7 days
    "no_publish_7d",  # (3) nothing published for 7 days
    "publish_failed_24h",  # (4) a publish job failed in the last 24h
    "meta_token_expiring",  # (5) a Meta token expires within ≤ 7 days
    "plan_counter_80pct",  # (6) ≥ 80% of any plan counter consumed
    "wa_quality_degraded",  # (7) quality_rating / messaging_limit degraded
    "wa_daily_cap_80pct",  # (8) ≥ 80% of the number's daily reply cap
]
ALERT_KINDS: tuple[str, ...] = AlertKind.__args__  # type: ignore[attr-defined]
Severity = Literal["info", "warning", "critical"]


class Alert(BaseModel):
    """One ``pulse_alerts`` row. ``payload['ref']`` is the idempotency key together with
    ``(brand_id, kind)`` while the alert is open; the payload holds ids/counts only — never
    customer text (D-069)."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    brand_id: UUID
    kind: AlertKind
    severity: Severity
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    acknowledged_at: datetime | None = None
    acknowledged_by_user_id: UUID | None = None

    @property
    def ref(self) -> str:
        return str(self.payload.get("ref", ""))

    @property
    def is_open(self) -> bool:
        return self.acknowledged_at is None


class Finding(BaseModel):
    """What a check produces before persistence: kind + severity + payload (with ``ref``)."""

    model_config = ConfigDict(frozen=True)

    brand_id: UUID
    kind: AlertKind
    severity: Severity
    payload: dict[str, Any]

    def to_alert(self, now: datetime) -> Alert:
        return Alert(
            brand_id=self.brand_id,
            kind=self.kind,
            severity=self.severity,
            payload=dict(self.payload),
            created_at=now,
        )


class CounterUsage(BaseModel):
    """Check (6): one plan counter of one brand — ``used / limit``."""

    model_config = ConfigDict(frozen=True)

    brand_id: UUID
    counter: str
    used: int
    limit: int


class PlanCounterUsageReader(Protocol):
    """Consumer-owned port (D-016, same pattern as ``counter_guard_dependency``): the plan
    counters of every active brand. ``src/main.py`` binds the entitlements adapter; ``None``
    ⇒ check (6) is skipped (entitlements removed)."""

    async def usage(self, now: datetime) -> list[CounterUsage]: ...


class PulseRepository(Protocol):
    async def open_refs(self, kind: str) -> set[tuple[UUID, str]]:
        """``(brand_id, payload->>'ref')`` of every **open** alert of ``kind``."""
        ...

    async def add_alerts(self, alerts: list[Alert]) -> list[Alert]: ...

    async def list_alerts(self, brand_id: UUID, *, open_only: bool, limit: int) -> list[Alert]: ...

    async def acknowledge(
        self, brand_id: UUID, alert_id: UUID, *, actor_user_id: UUID, now: datetime
    ) -> Alert | None: ...

    # --- the eight checks (read-only SQL over other modules' tables) ---
    async def window_closing(self, now: datetime, *, within_seconds: int) -> list[Finding]: ...

    async def slow_replies(self, now: datetime, *, threshold_seconds: int) -> list[Finding]: ...

    async def no_publish(self, now: datetime, *, days: int) -> list[Finding]: ...

    async def publish_failed(self, now: datetime, *, hours: int) -> list[Finding]: ...

    async def token_expiring(self, now: datetime, *, days: int) -> list[Finding]: ...

    async def quality_degraded(self, now: datetime) -> list[Finding]: ...

    async def daily_cap_near(self, now: datetime, *, ratio: float) -> list[Finding]: ...


__all__ = [
    "ALERT_KINDS",
    "Alert",
    "AlertKind",
    "CounterUsage",
    "Finding",
    "PlanCounterUsageReader",
    "PulseRepository",
    "Severity",
]
