"""Repository contract of Brand Brain (B-1). Implemented by
``infrastructure.postgres_repository.PostgresBrandBrainRepository`` and ``tests.fakes``."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.modules.brand_brain.domain.models import (
    BrandProfile,
    BrandProfileRevision,
    Competitor,
    CostDay,
    CostEntry,
    Evidence,
    LearningSignal,
    Recommendation,
)

EvidenceCursor = tuple[datetime, UUID]


class BrandBrainRepository(Protocol):
    # ----------------------------------------------------------- profile ----
    async def get_profile(self, brand_id: UUID) -> BrandProfile | None: ...

    async def insert_profile(self, profile: BrandProfile) -> BrandProfile: ...

    async def update_profile_cas(self, profile: BrandProfile, *, expected_version: int) -> bool:
        """``UPDATE … WHERE brand_id = ? AND version = expected`` ⇒ ``True`` when one row
        changed (the compare-and-set that backs ``If-Match``)."""
        ...

    async def add_revision(self, revision: BrandProfileRevision) -> BrandProfileRevision: ...

    async def list_revisions(
        self, brand_id: UUID, *, limit: int
    ) -> list[BrandProfileRevision]: ...

    # ------------------------------------------------------- competitors ----
    async def list_competitors(self, brand_id: UUID) -> list[Competitor]: ...

    async def count_competitors(self, brand_id: UUID) -> int: ...

    async def get_competitor(self, brand_id: UUID, competitor_id: UUID) -> Competitor | None: ...

    async def add_competitor(self, competitor: Competitor) -> Competitor:
        """Raises ``sqlalchemy.exc.IntegrityError`` on ``uq_brand_competitors_brand_name``."""
        ...

    async def delete_competitor(self, brand_id: UUID, competitor_id: UUID) -> bool: ...

    async def touch_competitor_refreshed(self, competitor_id: UUID, *, at: datetime) -> None: ...

    async def lock_brand_for_competitors(self, brand_id: UUID) -> None:
        """Serialise competitor inserts per brand (``SELECT … FOR UPDATE`` on ``brands``)."""
        ...

    # ---------------------------------------------------------- evidence ----
    async def add_evidence(self, rows: list[Evidence]) -> list[Evidence]: ...

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
        """Keyset ``observed_at DESC, id DESC``; returns up to ``limit + 1`` rows."""
        ...

    async def latest_evidence(self, brand_id: UUID, *, limit: int) -> list[Evidence]: ...

    async def get_evidence_by_ids(self, brand_id: UUID, ids: list[UUID]) -> list[Evidence]: ...

    # -------------------------------------------------------------- cost ----
    async def cost_by_day(self, brand_id: UUID, *, since: datetime) -> list[CostDay]: ...

    # --------------------------------------------------- recommendations ----
    async def add_recommendations(self, rows: list[Recommendation]) -> list[Recommendation]: ...

    async def list_recommendations(
        self, brand_id: UUID, *, status: str | None, limit: int
    ) -> list[Recommendation]:
        """Newest first."""
        ...

    async def get_recommendation_for_update(
        self, brand_id: UUID, recommendation_id: UUID
    ) -> Recommendation | None:
        """``SELECT … FOR UPDATE`` — a decision is taken on a locked row (A-3 pattern)."""
        ...

    async def decide_recommendation(
        self,
        recommendation_id: UUID,
        *,
        expected_status: str,
        new_status: str,
        actor_user_id: UUID,
        decided_at: datetime,
        materialized_item_id: UUID | None = None,
    ) -> bool:
        """Compare-and-set ``proposed ⇒ accepted|dismissed|materialized``; ``True`` iff one
        row changed."""
        ...

    # -------------------------------------------------- learning signals ----
    async def add_learning_signal(self, signal: LearningSignal) -> LearningSignal: ...


class CostLogWriter(Protocol):
    """D-072: one committed independent row per gateway call (``ai_gateway`` wrapper)."""

    async def write(self, entry: CostEntry) -> None: ...
