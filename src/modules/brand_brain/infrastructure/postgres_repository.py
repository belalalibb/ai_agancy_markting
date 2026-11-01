"""PostgreSQL implementation of :class:`BrandBrainRepository` (migration 0011) and the
independent :class:`PgCostLogWriter` (D-072 — one committed row per gateway call)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Date, and_, case, cast, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.database import get_session_factory, independent_session
from src.modules.brand_brain.domain.models import (
    Audience,
    BrandProfile,
    BrandProfileRevision,
    Competitor,
    CostDay,
    CostEntry,
    Evidence,
    Goal,
    Product,
    Tone,
)
from src.modules.brand_brain.domain.repository import EvidenceCursor
from src.modules.brand_brain.infrastructure.orm import (
    AICostLogRow,
    BrandCompetitorRow,
    BrandEvidenceRow,
    BrandProfileRevisionRow,
    BrandProfileRow,
)
from src.modules.tenancy.infrastructure.orm import BrandRow


def _profile(row: BrandProfileRow) -> BrandProfile:
    return BrandProfile(
        brand_id=row.brand_id,
        products=[Product(**p) for p in (row.products or [])],
        audience=Audience(**(row.audience or {})),
        tone=Tone(**(row.tone or {})),
        goals=[Goal(**g) for g in (row.goals or [])],
        version=row.version,
        updated_by_user_id=row.updated_by_user_id,
        updated_at=row.updated_at,
    )


def _revision(row: BrandProfileRevisionRow) -> BrandProfileRevision:
    return BrandProfileRevision(
        id=row.id,
        brand_id=row.brand_id,
        version=row.version,
        snapshot=dict(row.snapshot or {}),
        changed_by_user_id=row.changed_by_user_id,
        source=row.source,  # type: ignore[arg-type]
        created_at=row.created_at,
    )


def _competitor(row: BrandCompetitorRow) -> Competitor:
    return Competitor(
        id=row.id,
        brand_id=row.brand_id,
        name=row.name,
        kind=row.kind,  # type: ignore[arg-type]
        facebook_page_id=row.facebook_page_id,
        website_host=row.website_host,
        allowed_hosts=list(row.allowed_hosts or []),
        notes=row.notes or "",
        added_by_user_id=row.added_by_user_id,
        created_at=row.created_at,
        refreshed_at=row.competitor_refreshed_at,
    )


def _evidence(row: BrandEvidenceRow) -> Evidence:
    return Evidence(
        id=row.id,
        brand_id=row.brand_id,
        subject_kind=row.subject_kind,  # type: ignore[arg-type]
        subject_id=row.subject_id,
        evidence_kind=row.evidence_kind,  # type: ignore[arg-type]
        metric=row.metric,
        value=dict(row.value or {}),
        confidence=Decimal(row.confidence),
        source_kind=row.source_kind,  # type: ignore[arg-type]
        source_ref=row.source_ref,
        observed_at=row.observed_at,
        created_at=row.created_at,
    )


def _profile_values(profile: BrandProfile) -> dict[str, Any]:
    snap = profile.snapshot()
    return {
        "products": snap["products"],
        "audience": snap["audience"],
        "tone": snap["tone"],
        "goals": snap["goals"],
        "version": profile.version,
        "updated_by_user_id": profile.updated_by_user_id,
        "updated_at": profile.updated_at,
    }


class PostgresBrandBrainRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------- profile ----
    async def get_profile(self, brand_id: UUID) -> BrandProfile | None:
        row = await self._session.get(BrandProfileRow, brand_id)
        return _profile(row) if row is not None else None

    async def insert_profile(self, profile: BrandProfile) -> BrandProfile:
        self._session.add(BrandProfileRow(brand_id=profile.brand_id, **_profile_values(profile)))
        await self._session.flush()
        return profile

    async def update_profile_cas(self, profile: BrandProfile, *, expected_version: int) -> bool:
        result = await self._session.execute(
            update(BrandProfileRow)
            .where(
                BrandProfileRow.brand_id == profile.brand_id,
                BrandProfileRow.version == expected_version,
            )
            .values(**_profile_values(profile))
        )
        return bool(result.rowcount)

    async def add_revision(self, revision: BrandProfileRevision) -> BrandProfileRevision:
        self._session.add(
            BrandProfileRevisionRow(
                id=revision.id,
                brand_id=revision.brand_id,
                version=revision.version,
                snapshot=dict(revision.snapshot),
                changed_by_user_id=revision.changed_by_user_id,
                source=revision.source,
                created_at=revision.created_at,
            )
        )
        await self._session.flush()
        return revision

    async def list_revisions(self, brand_id: UUID, *, limit: int) -> list[BrandProfileRevision]:
        rows = await self._session.scalars(
            select(BrandProfileRevisionRow)
            .where(BrandProfileRevisionRow.brand_id == brand_id)
            .order_by(BrandProfileRevisionRow.version.desc())
            .limit(limit)
        )
        return [_revision(r) for r in rows]

    # --------------------------------------------------------- competitors ----
    async def list_competitors(self, brand_id: UUID) -> list[Competitor]:
        rows = await self._session.scalars(
            select(BrandCompetitorRow)
            .where(BrandCompetitorRow.brand_id == brand_id)
            .order_by(BrandCompetitorRow.created_at.asc(), BrandCompetitorRow.id.asc())
        )
        return [_competitor(r) for r in rows]

    async def count_competitors(self, brand_id: UUID) -> int:
        value = await self._session.scalar(
            select(func.count())
            .select_from(BrandCompetitorRow)
            .where(BrandCompetitorRow.brand_id == brand_id)
        )
        return int(value or 0)

    async def get_competitor(self, brand_id: UUID, competitor_id: UUID) -> Competitor | None:
        row = await self._session.scalar(
            select(BrandCompetitorRow).where(
                BrandCompetitorRow.brand_id == brand_id, BrandCompetitorRow.id == competitor_id
            )
        )
        return _competitor(row) if row is not None else None

    async def add_competitor(self, competitor: Competitor) -> Competitor:
        self._session.add(
            BrandCompetitorRow(
                id=competitor.id,
                brand_id=competitor.brand_id,
                name=competitor.name,
                kind=competitor.kind,
                facebook_page_id=competitor.facebook_page_id,
                website_host=competitor.website_host,
                allowed_hosts=list(competitor.allowed_hosts),
                notes=competitor.notes,
                added_by_user_id=competitor.added_by_user_id,
                created_at=competitor.created_at,
                competitor_refreshed_at=competitor.refreshed_at,
            )
        )
        await self._session.flush()
        return competitor

    async def delete_competitor(self, brand_id: UUID, competitor_id: UUID) -> bool:
        result = await self._session.execute(
            delete(BrandCompetitorRow).where(
                BrandCompetitorRow.brand_id == brand_id, BrandCompetitorRow.id == competitor_id
            )
        )
        return bool(result.rowcount)

    async def touch_competitor_refreshed(self, competitor_id: UUID, *, at: datetime) -> None:
        await self._session.execute(
            update(BrandCompetitorRow)
            .where(BrandCompetitorRow.id == competitor_id)
            .values(competitor_refreshed_at=at)
        )

    async def lock_brand_for_competitors(self, brand_id: UUID) -> None:
        # ``FOR NO KEY UPDATE`` on the brand row: serialises the count-then-insert of
        # competitors per brand without blocking FK ``KEY SHARE`` takers (lesson of A-1).
        await self._session.execute(
            select(BrandRow.id).where(BrandRow.id == brand_id).with_for_update(key_share=True)
        )

    # ------------------------------------------------------------ evidence ----
    async def add_evidence(self, rows: list[Evidence]) -> list[Evidence]:
        for ev in rows:
            self._session.add(
                BrandEvidenceRow(
                    id=ev.id,
                    brand_id=ev.brand_id,
                    subject_kind=ev.subject_kind,
                    subject_id=ev.subject_id,
                    evidence_kind=ev.evidence_kind,
                    metric=ev.metric,
                    value=dict(ev.value),
                    confidence=ev.confidence,
                    source_kind=ev.source_kind,
                    source_ref=ev.source_ref,
                    observed_at=ev.observed_at,
                    created_at=ev.created_at,
                )
            )
        if rows:
            await self._session.flush()
        return rows

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
    ) -> list[Evidence]:
        stmt = select(BrandEvidenceRow).where(BrandEvidenceRow.brand_id == brand_id)
        if subject_kind is not None:
            stmt = stmt.where(BrandEvidenceRow.subject_kind == subject_kind)
        if subject_id is not None:
            stmt = stmt.where(BrandEvidenceRow.subject_id == subject_id)
        if metric is not None:
            stmt = stmt.where(BrandEvidenceRow.metric == metric)
        if evidence_kind is not None:
            stmt = stmt.where(BrandEvidenceRow.evidence_kind == evidence_kind)
        if cursor is not None:
            last_at, last_id = cursor
            stmt = stmt.where(
                or_(
                    BrandEvidenceRow.observed_at < last_at,
                    and_(BrandEvidenceRow.observed_at == last_at, BrandEvidenceRow.id < last_id),
                )
            )
        stmt = stmt.order_by(
            BrandEvidenceRow.observed_at.desc(), BrandEvidenceRow.id.desc()
        ).limit(limit + 1)
        return [_evidence(r) for r in await self._session.scalars(stmt)]

    async def latest_evidence(self, brand_id: UUID, *, limit: int) -> list[Evidence]:
        rows = await self._session.scalars(
            select(BrandEvidenceRow)
            .where(BrandEvidenceRow.brand_id == brand_id)
            .order_by(BrandEvidenceRow.observed_at.desc(), BrandEvidenceRow.id.desc())
            .limit(limit)
        )
        return [_evidence(r) for r in rows]

    async def get_evidence_by_ids(self, brand_id: UUID, ids: list[UUID]) -> list[Evidence]:
        if not ids:
            return []
        rows = await self._session.scalars(
            select(BrandEvidenceRow).where(
                BrandEvidenceRow.brand_id == brand_id, BrandEvidenceRow.id.in_(ids)
            )
        )
        return [_evidence(r) for r in rows]

    # ---------------------------------------------------------------- cost ----
    async def cost_by_day(self, brand_id: UUID, *, since: datetime) -> list[CostDay]:
        day = cast(AICostLogRow.created_at, Date).label("day")
        stmt = (
            select(
                day,
                func.count().label("calls"),
                func.sum(case((AICostLogRow.outcome == "error", 1), else_=0)).label("errors"),
                func.coalesce(func.sum(AICostLogRow.prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(AICostLogRow.completion_tokens), 0).label(
                    "completion_tokens"
                ),
                func.sum(AICostLogRow.estimated_cost_usd).label("estimated_cost_usd"),
            )
            .where(AICostLogRow.brand_id == brand_id, AICostLogRow.created_at >= since)
            .group_by(day)
            .order_by(day.desc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            CostDay(
                day=str(r.day),
                calls=int(r.calls),
                errors=int(r.errors or 0),
                prompt_tokens=int(r.prompt_tokens or 0),
                completion_tokens=int(r.completion_tokens or 0),
                estimated_cost_usd=(
                    Decimal(r.estimated_cost_usd) if r.estimated_cost_usd is not None else None
                ),
            )
            for r in rows
        ]


class PgCostLogWriter:
    """D-072: the cost row is committed on its own session, so it survives a failed request
    (and a failed gateway call is recorded as ``outcome='error'``)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory

    async def write(self, entry: CostEntry) -> None:
        factory = self._session_factory or get_session_factory()
        async with independent_session(factory) as session:
            session.add(
                AICostLogRow(
                    id=entry.id,
                    brand_id=entry.brand_id,
                    purpose=entry.purpose,
                    provider=entry.provider,
                    model=entry.model,
                    credential_mode=entry.credential_mode,
                    prompt_tokens=entry.prompt_tokens,
                    completion_tokens=entry.completion_tokens,
                    estimated_cost_usd=entry.estimated_cost_usd,
                    latency_ms=entry.latency_ms,
                    prompt_version=entry.prompt_version,
                    outcome=entry.outcome.value,
                    error_category=entry.error_category,
                    created_at=entry.created_at,
                )
            )
            await session.flush()
