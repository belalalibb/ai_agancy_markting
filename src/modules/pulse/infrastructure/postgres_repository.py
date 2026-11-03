"""PostgreSQL ``PulseRepository`` — ``pulse_alerts`` persistence + the eight SQL-only checks.

Every check is one read-only statement over tables other modules own (``wa_conversations``,
``wa_messages``, ``publish_jobs``, ``social_connections``, ``whatsapp_accounts``,
``wa_number_caps``). No model, no network. Check (7) reads ``quality_rating`` /
``messaging_limit`` **only if the columns exist** (``whatsapp_accounts`` has neither in 0009 ⇒
the check is a documented no-op; recorded as a deviation in the executor report).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import CheckConstraint, Index, String, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base
from src.modules.pulse.domain import ALERT_KINDS, Alert, Finding

DEGRADED_QUALITY = ("RED", "YELLOW", "red", "yellow")
DEFAULT_DAILY_REPLY_CAP = 300  # mirrors whatsapp/inbox.py (D-082) without importing it


class PulseAlertRow(Base):
    __tablename__ = "pulse_alerts"
    __table_args__ = (
        CheckConstraint(
            "kind IN (" + ", ".join(repr(k) for k in ALERT_KINDS) + ")",
            name="ck_pulse_alerts_kind",
        ),
        CheckConstraint(
            "severity IN ('info', 'warning', 'critical')", name="ck_pulse_alerts_severity"
        ),
        Index(
            "ix_pulse_alerts_brand_open",
            "brand_id",
            "kind",
            postgresql_where=text("acknowledged_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    brand_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    acknowledged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )


def _alert(row: PulseAlertRow) -> Alert:
    return Alert(
        id=row.id,
        brand_id=row.brand_id,
        kind=row.kind,  # type: ignore[arg-type]
        severity=row.severity,  # type: ignore[arg-type]
        payload=dict(row.payload or {}),
        created_at=row.created_at,
        acknowledged_at=row.acknowledged_at,
        acknowledged_by_user_id=row.acknowledged_by_user_id,
    )


# ------------------------------------------------------------------ check SQL ----
_WINDOW_CLOSING = text(
    "SELECT id, brand_id, window_expires_at FROM wa_conversations "
    "WHERE status = 'open' AND window_expires_at IS NOT NULL "
    "AND window_expires_at > :now AND window_expires_at <= :until "
    "AND last_inbound_at IS NOT NULL "
    "AND (last_outbound_at IS NULL OR last_outbound_at < last_inbound_at)"
)
_SLOW_REPLIES = text(
    "SELECT i.brand_id, avg(EXTRACT(EPOCH FROM (o.created_at - i.created_at))) AS latency, "
    "count(*) AS samples "
    "FROM wa_messages i JOIN LATERAL ("
    "  SELECT m.created_at FROM wa_messages m "
    "  WHERE m.conversation_id = i.conversation_id AND m.direction = 'outbound' "
    "  AND m.created_at > i.created_at ORDER BY m.created_at LIMIT 1"
    ") o ON true "
    "WHERE i.direction = 'inbound' AND i.created_at >= :since "
    "GROUP BY i.brand_id HAVING avg(EXTRACT(EPOCH FROM (o.created_at - i.created_at))) > :thr"
)
_NO_PUBLISH = text(
    "SELECT b.id FROM brands b "
    "WHERE EXISTS (SELECT 1 FROM publish_jobs j WHERE j.brand_id = b.id) "
    "AND NOT EXISTS (SELECT 1 FROM publish_jobs j WHERE j.brand_id = b.id "
    "  AND j.status = 'succeeded' AND j.updated_at >= :since)"
)
_PUBLISH_FAILED = text(
    "SELECT id, brand_id, last_error_class FROM publish_jobs "
    "WHERE status = 'failed' AND updated_at >= :since"
)
_TOKEN_EXPIRING = text(
    "SELECT id, brand_id, token_expires_at, status FROM social_connections "
    "WHERE (status = 'active' AND token_expires_at IS NOT NULL AND token_expires_at <= :until) "
    "OR status = 'reauth_required'"  # SPRINT-13 A-4 (a): a refresh/validation that failed
)
_ACCOUNT_COLUMNS = text(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_name = 'whatsapp_accounts' "
    "AND column_name IN ('quality_rating', 'messaging_limit')"
)
_QUALITY = text(
    "SELECT id, brand_id, quality_rating FROM whatsapp_accounts "
    "WHERE status = 'active' AND quality_rating IN ('RED', 'YELLOW', 'red', 'yellow')"
)
_DAILY_CAP = text(
    "SELECT a.id, a.brand_id, coalesce(c.daily_reply_cap, :default_cap) AS cap, "
    "(SELECT count(*) FROM wa_messages m JOIN wa_conversations v ON v.id = m.conversation_id "
    " WHERE v.account_id = a.id AND m.direction = 'outbound' AND m.created_at >= :day_start) "
    "AS used "
    "FROM whatsapp_accounts a LEFT JOIN wa_number_caps c ON c.account_id = a.id "
    "WHERE a.status = 'active' AND (c.kill_switch IS NULL OR c.kill_switch = false)"
)


class PgPulseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----------------------------------------------------------------- alerts ----
    async def open_refs(self, kind: str) -> set[tuple[uuid.UUID, str]]:
        rows = await self._session.execute(
            select(PulseAlertRow.brand_id, PulseAlertRow.payload["ref"].astext).where(
                PulseAlertRow.kind == kind, PulseAlertRow.acknowledged_at.is_(None)
            )
        )
        return {(brand_id, ref or "") for brand_id, ref in rows.all()}

    async def add_alerts(self, alerts: list[Alert]) -> list[Alert]:
        for alert in alerts:
            self._session.add(
                PulseAlertRow(
                    id=alert.id,
                    brand_id=alert.brand_id,
                    kind=alert.kind,
                    severity=alert.severity,
                    payload=dict(alert.payload),
                    created_at=alert.created_at,
                )
            )
        await self._session.flush()
        return alerts

    async def list_alerts(
        self, brand_id: uuid.UUID, *, open_only: bool, limit: int
    ) -> list[Alert]:
        stmt = select(PulseAlertRow).where(PulseAlertRow.brand_id == brand_id)
        if open_only:
            stmt = stmt.where(PulseAlertRow.acknowledged_at.is_(None))
        rows = await self._session.scalars(
            stmt.order_by(PulseAlertRow.created_at.desc(), PulseAlertRow.id.desc()).limit(limit)
        )
        return [_alert(r) for r in rows]

    async def acknowledge(
        self, brand_id: uuid.UUID, alert_id: uuid.UUID, *, actor_user_id: uuid.UUID, now: datetime
    ) -> Alert | None:
        row = await self._session.scalar(
            update(PulseAlertRow)
            .where(
                PulseAlertRow.id == alert_id,
                PulseAlertRow.brand_id == brand_id,
                PulseAlertRow.acknowledged_at.is_(None),
            )
            .values(acknowledged_at=now, acknowledged_by_user_id=actor_user_id)
            .returning(PulseAlertRow)
        )
        if row is not None:
            return _alert(row)
        existing = await self._session.scalar(
            select(PulseAlertRow).where(
                PulseAlertRow.id == alert_id, PulseAlertRow.brand_id == brand_id
            )
        )
        return _alert(existing) if existing is not None else None

    # ----------------------------------------------------------------- checks ----
    async def window_closing(self, now: datetime, *, within_seconds: int) -> list[Finding]:
        rows = await self._session.execute(
            _WINDOW_CLOSING, {"now": now, "until": now + timedelta(seconds=within_seconds)}
        )
        return [
            Finding(
                brand_id=brand_id,
                kind="wa_window_closing",
                severity="critical",
                payload={
                    "ref": str(conv_id),
                    "conversation_id": str(conv_id),
                    "window_expires_at": expires.isoformat(),
                },
            )
            for conv_id, brand_id, expires in rows.all()
        ]

    async def slow_replies(self, now: datetime, *, threshold_seconds: int) -> list[Finding]:
        rows = await self._session.execute(
            _SLOW_REPLIES, {"since": now - timedelta(days=7), "thr": threshold_seconds}
        )
        return [
            Finding(
                brand_id=brand_id,
                kind="wa_slow_replies",
                severity="warning",
                payload={
                    "ref": now.date().isoformat(),
                    "avg_reply_seconds": int(latency),
                    "samples": int(samples),
                },
            )
            for brand_id, latency, samples in rows.all()
        ]

    async def no_publish(self, now: datetime, *, days: int) -> list[Finding]:
        rows = await self._session.execute(_NO_PUBLISH, {"since": now - timedelta(days=days)})
        return [
            Finding(
                brand_id=brand_id,
                kind="no_publish_7d",
                severity="warning",
                payload={"ref": f"{days}d", "days": days},
            )
            for (brand_id,) in rows.all()
        ]

    async def publish_failed(self, now: datetime, *, hours: int) -> list[Finding]:
        rows = await self._session.execute(
            _PUBLISH_FAILED, {"since": now - timedelta(hours=hours)}
        )
        return [
            Finding(
                brand_id=brand_id,
                kind="publish_failed_24h",
                severity="warning",
                payload={"ref": str(job_id), "job_id": str(job_id), "error_class": error_class},
            )
            for job_id, brand_id, error_class in rows.all()
        ]

    async def token_expiring(self, now: datetime, *, days: int) -> list[Finding]:
        rows = await self._session.execute(_TOKEN_EXPIRING, {"until": now + timedelta(days=days)})
        out: list[Finding] = []
        for conn_id, brand_id, expires, conn_status in rows.all():
            failed = conn_status == "reauth_required"  # A-4 (a): reason ``refresh_failed``
            out.append(
                Finding(
                    brand_id=brand_id,
                    kind="meta_token_expiring",
                    severity="critical" if failed or expires <= now else "warning",
                    payload={
                        "ref": str(conn_id),
                        "connection_id": str(conn_id),
                        "token_expires_at": expires.isoformat() if expires else None,
                        "reason": "refresh_failed" if failed else "expiring",
                    },
                )
            )
        return out

    async def quality_degraded(self, now: datetime) -> list[Finding]:
        columns = {c for (c,) in (await self._session.execute(_ACCOUNT_COLUMNS)).all()}
        if "quality_rating" not in columns:
            return []  # 0009 has no such column — documented deviation (§6)
        rows = await self._session.execute(_QUALITY)
        return [
            Finding(
                brand_id=brand_id,
                kind="wa_quality_degraded",
                severity="critical" if str(rating).upper() == "RED" else "warning",
                payload={"ref": str(account_id), "account_id": str(account_id), "rating": rating},
            )
            for account_id, brand_id, rating in rows.all()
        ]

    async def daily_cap_near(self, now: datetime, *, ratio: float) -> list[Finding]:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rows = await self._session.execute(
            _DAILY_CAP, {"default_cap": DEFAULT_DAILY_REPLY_CAP, "day_start": day_start}
        )
        out: list[Finding] = []
        for account_id, brand_id, cap, used in rows.all():
            if used < cap * ratio:
                continue
            out.append(
                Finding(
                    brand_id=brand_id,
                    kind="wa_daily_cap_80pct",
                    severity="critical" if used >= cap else "warning",
                    payload={
                        "ref": f"{account_id}:{day_start.date().isoformat()}",
                        "account_id": str(account_id),
                        "cap": int(cap),
                        "used": int(used),
                    },
                )
            )
        return out


__all__ = ["PgPulseRepository", "PulseAlertRow"]
