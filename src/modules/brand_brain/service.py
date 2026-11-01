"""Brand Brain application service (SPRINT-10 B-1).

Rules enforced here (directive §2-0): tenant scope on every write (``brand_id`` comes from
the authorised route), ``If-Match`` compare-and-set on the profile, ≤3 competitors per
brand (lock + count + unique name), evidence only from the closed metric dictionary, the
OWNER-only cost aggregation, and the internal evidence collection (``measured/internal_db``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from src.modules.auth.domain.rate_limit import RateLimiter
from src.modules.brand_brain.domain.hosts import is_blocked_platform, normalize_host
from src.modules.brand_brain.domain.metrics import INTERNAL_METRICS, is_known_metric
from src.modules.brand_brain.domain.models import (
    MAX_COMPETITORS_PER_BRAND,
    BrandProfile,
    BrandProfileRevision,
    Competitor,
    CostDay,
    Evidence,
    Tone,
)
from src.modules.brand_brain.domain.repository import BrandBrainRepository, EvidenceCursor
from src.modules.brand_brain.exceptions import (
    BlockedPlatformHostError,
    CompetitorExistsError,
    CompetitorLimitError,
    CompetitorNotFoundError,
    InvalidHostError,
    ProfileVersionConflictError,
    RefreshRateLimitedError,
    UnknownMetricError,
)
from src.modules.brand_brain.schemas import CompetitorCreate, ProfileUpdate

logger = logging.getLogger(__name__)

COST_WINDOW_DAYS = 30
MAX_REVISIONS = 50
EVIDENCE_REFRESH_BUCKET = "brain:evidence-refresh"
EVIDENCE_REFRESH_LIMIT = 60
EVIDENCE_REFRESH_WINDOW_SECONDS = 3600
INTERNAL_CONFIDENCE = Decimal("0.95")


@dataclass(frozen=True)
class InternalMetrics:
    """What :class:`InternalMetricsReader` measured from our own tables (``None`` ⇒ no data)."""

    wa_conversations_7d: int
    wa_approval_rate_30d: float | None
    content_approval_rate_30d: float | None
    posting_frequency_7d: int
    avg_reply_latency_s: float | None
    wa_decisions_30d: int = 0
    content_decisions_30d: int = 0
    reply_samples: int = 0


class InternalMetricsReader(Protocol):
    async def read(self, brand_id: UUID, *, now: datetime) -> InternalMetrics: ...


@dataclass(frozen=True)
class CostSummary:
    days: list[CostDay]
    window_days: int


class EvidenceCollector:
    """``POST /evidence/refresh``: the five internal metrics ⇒ ``measured`` rows from
    ``internal_db`` (D-074). Metrics without data are recorded with ``{"unavailable":
    "no_data"}`` so the diagnosis can say so instead of guessing."""

    def __init__(self, repo: BrandBrainRepository, reader: InternalMetricsReader) -> None:
        self._repo = repo
        self._reader = reader

    async def collect_internal(
        self, brand_id: UUID, *, now: datetime | None = None
    ) -> list[Evidence]:
        now = now or datetime.now(tz=UTC)
        m = await self._reader.read(brand_id, now=now)
        values: dict[str, dict[str, Any]] = {
            "wa_conversations_7d": {"count": m.wa_conversations_7d, "window_days": 7},
            "wa_approval_rate_30d": _ratio(m.wa_approval_rate_30d, m.wa_decisions_30d, 30),
            "content_approval_rate_30d": _ratio(
                m.content_approval_rate_30d, m.content_decisions_30d, 30
            ),
            "posting_frequency_7d": {"count": m.posting_frequency_7d, "window_days": 7},
            "avg_reply_latency_s": (
                {"seconds": round(m.avg_reply_latency_s, 1), "samples": m.reply_samples}
                if m.avg_reply_latency_s is not None
                else {"unavailable": "no_data"}
            ),
        }
        rows = [
            Evidence(
                brand_id=brand_id,
                subject_kind="brand",
                subject_id=brand_id,
                evidence_kind="measured",
                metric=metric,
                value=values[metric],
                confidence=INTERNAL_CONFIDENCE,
                source_kind="internal_db",
                source_ref=f"internal_db:{metric}",
                observed_at=now,
                created_at=now,
            )
            for metric in INTERNAL_METRICS
        ]
        return await self._repo.add_evidence(rows)


def _ratio(rate: float | None, samples: int, window_days: int) -> dict[str, Any]:
    if rate is None:
        return {"unavailable": "no_data", "window_days": window_days}
    return {"ratio": round(rate, 3), "samples": samples, "window_days": window_days}


class BrandBrainService:
    def __init__(
        self,
        repo: BrandBrainRepository,
        *,
        limiter: RateLimiter,
        collector: EvidenceCollector,
    ) -> None:
        self._repo = repo
        self._limiter = limiter
        self._collector = collector

    # --------------------------------------------------------------- profile ----
    async def get_profile(self, brand_id: UUID) -> BrandProfile:
        """Reads never create rows: a brand without a profile gets the empty default
        (``version=1``) so the first ``PUT`` can use ``If-Match: 1``."""
        profile = await self._repo.get_profile(brand_id)
        return profile if profile is not None else _default_profile(brand_id)

    async def update_profile(
        self,
        brand_id: UUID,
        payload: ProfileUpdate,
        *,
        expected_version: int,
        actor_user_id: UUID,
    ) -> BrandProfile:
        now = datetime.now(tz=UTC)
        current = await self._repo.get_profile(brand_id)
        if current is None:
            if expected_version != 1:
                raise ProfileVersionConflictError(current_version=1)
            created = _apply(_default_profile(brand_id), payload, now=now, actor=actor_user_id)
            try:
                await self._repo.insert_profile(created)
            except IntegrityError as exc:  # a concurrent first PUT won the insert
                raise ProfileVersionConflictError(current_version=1) from exc
            await self._repo.add_revision(_revision(created, actor_user_id, now))
            return created
        if current.version != expected_version:
            raise ProfileVersionConflictError(current_version=current.version)
        updated = _apply(current, payload, now=now, actor=actor_user_id)
        updated = updated.model_copy(update={"version": current.version + 1})
        if not await self._repo.update_profile_cas(updated, expected_version=expected_version):
            fresh = await self._repo.get_profile(brand_id)
            raise ProfileVersionConflictError(
                current_version=fresh.version if fresh else expected_version
            )
        await self._repo.add_revision(_revision(updated, actor_user_id, now))
        logger.info(
            "brain profile updated: brand=%s version=%s by=%s",
            brand_id,
            updated.version,
            actor_user_id,
        )
        return updated

    async def list_revisions(self, brand_id: UUID) -> list[BrandProfileRevision]:
        return await self._repo.list_revisions(brand_id, limit=MAX_REVISIONS)

    # ----------------------------------------------------------- competitors ----
    async def list_competitors(self, brand_id: UUID) -> list[Competitor]:
        return await self._repo.list_competitors(brand_id)

    async def add_competitor(
        self, brand_id: UUID, payload: CompetitorCreate, *, actor_user_id: UUID
    ) -> Competitor:
        website_host = _validated_host(payload.website_host) if payload.website_host else None
        allowed = [_validated_host(h) for h in payload.allowed_hosts]
        if website_host is not None and website_host not in allowed:
            allowed = [website_host, *allowed][:3]
        # Serialise per brand: lock ⇒ count ⇒ insert (race test: [201, 201, 201, 409]).
        await self._repo.lock_brand_for_competitors(brand_id)
        if await self._repo.count_competitors(brand_id) >= MAX_COMPETITORS_PER_BRAND:
            raise CompetitorLimitError()
        competitor = Competitor(
            brand_id=brand_id,
            name=payload.name.strip(),
            kind=payload.kind,
            facebook_page_id=payload.facebook_page_id,
            website_host=website_host,
            allowed_hosts=list(dict.fromkeys(allowed)),
            notes=payload.notes,
            added_by_user_id=actor_user_id,
            created_at=datetime.now(tz=UTC),
        )
        try:
            return await self._repo.add_competitor(competitor)
        except IntegrityError as exc:
            raise CompetitorExistsError() from exc

    async def delete_competitor(self, brand_id: UUID, competitor_id: UUID) -> None:
        if not await self._repo.delete_competitor(brand_id, competitor_id):
            raise CompetitorNotFoundError()

    async def get_competitor(self, brand_id: UUID, competitor_id: UUID) -> Competitor:
        competitor = await self._repo.get_competitor(brand_id, competitor_id)
        if competitor is None:
            raise CompetitorNotFoundError()
        return competitor

    # -------------------------------------------------------------- evidence ----
    async def list_evidence(
        self,
        brand_id: UUID,
        *,
        subject_kind: str | None,
        subject_id: UUID | None,
        metric: str | None,
        evidence_kind: str | None,
        limit: int,
        cursor: EvidenceCursor | None,
    ) -> tuple[list[Evidence], bool]:
        if metric is not None and not is_known_metric(metric):
            raise UnknownMetricError()
        rows = await self._repo.list_evidence(
            brand_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            metric=metric,
            evidence_kind=evidence_kind,
            limit=limit,
            cursor=cursor,
        )
        return rows[:limit], len(rows) > limit

    async def refresh_internal_evidence(self, brand_id: UUID) -> list[Evidence]:
        retry = self._limiter.hit(
            EVIDENCE_REFRESH_BUCKET,
            str(brand_id),
            EVIDENCE_REFRESH_LIMIT,
            EVIDENCE_REFRESH_WINDOW_SECONDS,
        )
        if retry is not None:
            raise RefreshRateLimitedError(retry)
        return await self._collector.collect_internal(brand_id)

    # ------------------------------------------------------------------ cost ----
    async def cost_report(self, brand_id: UUID) -> CostSummary:
        since = datetime.now(tz=UTC) - timedelta(days=COST_WINDOW_DAYS)
        return CostSummary(
            days=await self._repo.cost_by_day(brand_id, since=since), window_days=COST_WINDOW_DAYS
        )


# ------------------------------------------------------------------ helpers --
def _default_profile(brand_id: UUID) -> BrandProfile:
    return BrandProfile(brand_id=brand_id, version=1, updated_at=datetime.now(tz=UTC))


def _apply(
    base: BrandProfile, payload: ProfileUpdate, *, now: datetime, actor: UUID
) -> BrandProfile:
    tone = Tone(
        voice=payload.tone.voice,
        formality=payload.tone.formality,
        emoji_policy=payload.tone.emoji_policy,
        banned_phrases=list(payload.tone.banned_phrases),
        reply_templates=list(base.tone.reply_templates),  # preserved — B-3 accept only
    )
    return base.model_copy(
        update={
            "products": list(payload.products),
            "audience": payload.audience,
            "tone": tone,
            "goals": list(payload.goals),
            "updated_by_user_id": actor,
            "updated_at": now,
        }
    )


def _revision(profile: BrandProfile, actor: UUID, now: datetime) -> BrandProfileRevision:
    return BrandProfileRevision(
        brand_id=profile.brand_id,
        version=profile.version,
        snapshot=profile.snapshot(),
        changed_by_user_id=actor,
        source="human",
        created_at=now,
    )


def _validated_host(raw: str) -> str:
    host = normalize_host(raw)
    if host is None:
        raise InvalidHostError()
    if is_blocked_platform(host):
        raise BlockedPlatformHostError()
    return host
