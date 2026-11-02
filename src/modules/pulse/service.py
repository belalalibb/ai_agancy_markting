"""``Pulse.run(now)`` — the eight SQL-only checks, deduplicated against the open alerts.

Idempotency: a finding whose ``(brand_id, kind, payload['ref'])`` matches an **open** alert is
not written again; once acknowledged, the same condition raises a fresh alert next cycle.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.core.metrics import counters
from src.modules.pulse.domain import (
    Alert,
    CounterUsage,
    Finding,
    PlanCounterUsageReader,
    PulseRepository,
)

logger = logging.getLogger(__name__)

WINDOW_CLOSING_SECONDS = 4 * 3600  # (1)
SLOW_REPLY_SECONDS = 2 * 3600  # (2)
NO_PUBLISH_DAYS = 7  # (3)
PUBLISH_FAILED_HOURS = 24  # (4)
TOKEN_EXPIRING_DAYS = 7  # (5)
COUNTER_RATIO = 0.8  # (6)
DAILY_CAP_RATIO = 0.8  # (8)


def counter_findings(rows: list[CounterUsage], *, ratio: float = COUNTER_RATIO) -> list[Finding]:
    """Check (6): ≥ ``ratio`` of a counter ⇒ ``warning`` (``critical`` at 100%)."""
    out: list[Finding] = []
    for row in rows:
        if row.limit <= 0 or row.used < row.limit * ratio:
            continue
        out.append(
            Finding(
                brand_id=row.brand_id,
                kind="plan_counter_80pct",
                severity="critical" if row.used >= row.limit else "warning",
                payload={
                    "ref": row.counter,
                    "counter": row.counter,
                    "used": row.used,
                    "limit": row.limit,
                },
            )
        )
    return out


class Pulse:
    def __init__(
        self, repo: PulseRepository, *, counters_reader: PlanCounterUsageReader | None = None
    ) -> None:
        self._repo = repo
        self._counters = counters_reader

    async def findings(self, now: datetime) -> list[Finding]:
        found: list[Finding] = []
        found += await self._repo.window_closing(now, within_seconds=WINDOW_CLOSING_SECONDS)
        found += await self._repo.slow_replies(now, threshold_seconds=SLOW_REPLY_SECONDS)
        found += await self._repo.no_publish(now, days=NO_PUBLISH_DAYS)
        found += await self._repo.publish_failed(now, hours=PUBLISH_FAILED_HOURS)
        found += await self._repo.token_expiring(now, days=TOKEN_EXPIRING_DAYS)
        if self._counters is not None:
            found += counter_findings(await self._counters.usage(now))
        found += await self._repo.quality_degraded(now)
        found += await self._repo.daily_cap_near(now, ratio=DAILY_CAP_RATIO)
        return found

    async def run(self, now: datetime | None = None) -> list[Alert]:
        """Evaluate every check and persist the findings that are not already open."""
        now = now or datetime.now(tz=UTC)
        found = await self.findings(now)
        open_cache: dict[str, set[tuple[object, str]]] = {}
        fresh: list[Alert] = []
        seen: set[tuple[object, str, str]] = set()
        for finding in found:
            ref = str(finding.payload.get("ref", ""))
            key = (finding.brand_id, finding.kind, ref)
            if key in seen:
                continue
            seen.add(key)
            if finding.kind not in open_cache:
                open_cache[finding.kind] = set(await self._repo.open_refs(finding.kind))
            if (finding.brand_id, ref) in open_cache[finding.kind]:
                continue
            fresh.append(finding.to_alert(now))
        if fresh:
            await self._repo.add_alerts(fresh)
            counters.increment("pulse_alerts_created", len(fresh))
        logger.info("pulse: findings=%d new=%d", len(found), len(fresh))
        return fresh


__all__ = ["Pulse", "counter_findings"]
