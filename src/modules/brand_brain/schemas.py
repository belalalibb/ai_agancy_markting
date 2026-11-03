"""Request / response schemas of ``/brands/{brand_id}/brain`` (B-1).

Visibility (D-066, §2-0 و): ``EvidencePublic.confidence`` / ``source_ref`` exist only in the
OWNER/MANAGER projection; VIEWER receives ``EvidenceSummaryPublic`` (``evidence_kind`` +
summary). ``ProfilePublic`` drops ``tone.banned_phrases`` (and ``reply_templates``) for VIEWER.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.modules.brand_brain.domain.models import (
    MAX_ALLOWED_HOSTS,
    MAX_GOALS,
    MAX_PRODUCTS,
    Audience,
    BrandProfile,
    BrandProfileRevision,
    Competitor,
    CompetitorKind,
    CostDay,
    Evidence,
    Goal,
    Product,
    Recommendation,
    Sector,
    Tone,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ profile --
class ToneInput(_Strict):
    voice: str = Field(default="", max_length=200)
    formality: Literal["casual", "neutral", "formal"] = "neutral"
    emoji_policy: Literal["none", "light", "free"] = "light"
    banned_phrases: list[str] = Field(default_factory=list, max_length=50)


class ProfileUpdate(_Strict):
    """``PUT /profile`` body. ``reply_templates`` is **not** writable here — it is filled
    only by accepting a ``reply_template`` recommendation (B-3)."""

    products: list[Product] = Field(default_factory=list, max_length=MAX_PRODUCTS)
    audience: Audience = Field(default_factory=Audience)
    tone: ToneInput = Field(default_factory=ToneInput)
    goals: list[Goal] = Field(default_factory=list, max_length=MAX_GOALS)
    # SPRINT-13 B-3: omitted ⇒ the stored sector is preserved; ``null`` clears it.
    sector: Sector | None = None


class TonePublic(_Strict):
    voice: str
    formality: str
    emoji_policy: str
    banned_phrases: list[str] | None = None  # None ⇒ hidden for VIEWER
    reply_templates: list[str] | None = None


class ProfilePublic(_Strict):
    brand_id: UUID
    products: list[Product]
    audience: Audience
    tone: TonePublic
    goals: list[Goal]
    sector: Sector | None = None  # SPRINT-13 B-3
    version: int
    updated_by_user_id: UUID | None
    updated_at: datetime

    @classmethod
    def from_domain(cls, profile: BrandProfile, *, private: bool) -> ProfilePublic:
        tone: Tone = profile.tone
        return cls(
            brand_id=profile.brand_id,
            products=list(profile.products),
            audience=profile.audience,
            tone=TonePublic(
                voice=tone.voice,
                formality=tone.formality,
                emoji_policy=tone.emoji_policy,
                banned_phrases=list(tone.banned_phrases) if private else None,
                reply_templates=list(tone.reply_templates) if private else None,
            ),
            goals=list(profile.goals),
            sector=profile.sector,
            version=profile.version,
            updated_by_user_id=profile.updated_by_user_id,
            updated_at=profile.updated_at,
        )


class RevisionPublic(_Strict):
    id: UUID
    version: int
    source: str
    changed_by_user_id: UUID | None
    created_at: datetime
    snapshot: dict[str, Any]

    @classmethod
    def from_domain(cls, rev: BrandProfileRevision, *, private: bool) -> RevisionPublic:
        """A-5 (B3): the same projection as ``ProfilePublic`` — a non-private reader never
        sees ``tone.banned_phrases`` nor ``reply_templates`` inside a revision snapshot."""
        snapshot: dict[str, Any] = dict(rev.snapshot)
        if not private:
            tone = snapshot.get("tone")
            if isinstance(tone, dict):
                tone = dict(tone)
                tone.pop("banned_phrases", None)
                tone.pop("reply_templates", None)  # stored under ``tone`` (see Tone model)
                snapshot["tone"] = tone
            snapshot.pop("reply_templates", None)  # tolerate a top-level key as well
        return cls(
            id=rev.id,
            version=rev.version,
            source=rev.source,
            changed_by_user_id=rev.changed_by_user_id,
            created_at=rev.created_at,
            snapshot=snapshot,
        )


class RevisionsPage(_Strict):
    items: list[RevisionPublic]


# -------------------------------------------------------------- competitors --
class CompetitorCreate(_Strict):
    name: str = Field(min_length=1, max_length=120)
    kind: CompetitorKind = "direct"
    facebook_page_id: str | None = Field(default=None, max_length=32, pattern=r"^[0-9]{1,32}$")
    website_host: str | None = Field(default=None, max_length=253)
    allowed_hosts: list[str] = Field(default_factory=list, max_length=MAX_ALLOWED_HOSTS)
    notes: str = Field(default="", max_length=600)


class CompetitorPublic(_Strict):
    id: UUID
    brand_id: UUID
    name: str
    kind: str
    facebook_page_id: str | None
    website_host: str | None
    allowed_hosts: list[str]
    notes: str
    added_by_user_id: UUID
    created_at: datetime
    competitor_refreshed_at: datetime | None

    @classmethod
    def from_domain(cls, c: Competitor) -> CompetitorPublic:
        return cls(
            id=c.id,
            brand_id=c.brand_id,
            name=c.name,
            kind=c.kind,
            facebook_page_id=c.facebook_page_id,
            website_host=c.website_host,
            allowed_hosts=list(c.allowed_hosts),
            notes=c.notes,
            added_by_user_id=c.added_by_user_id,
            created_at=c.created_at,
            competitor_refreshed_at=c.refreshed_at,
        )


class CompetitorsPage(_Strict):
    items: list[CompetitorPublic]
    limit: int


# ----------------------------------------------------------------- evidence --
class EvidenceSummaryPublic(_Strict):
    """VIEWER projection (D-066): grade + what was measured — no confidence, no source."""

    id: UUID
    subject_kind: str
    subject_id: UUID
    evidence_kind: str
    metric: str
    value: dict[str, Any]
    observed_at: datetime


class EvidencePublic(EvidenceSummaryPublic):
    """OWNER/MANAGER projection — adds ``confidence`` + ``source_kind`` + ``source_ref``."""

    confidence: Decimal
    source_kind: str
    source_ref: str | None


def evidence_to_public(ev: Evidence, *, private: bool) -> EvidenceSummaryPublic:
    base: dict[str, Any] = {
        "id": ev.id,
        "subject_kind": ev.subject_kind,
        "subject_id": ev.subject_id,
        "evidence_kind": ev.evidence_kind,
        "metric": ev.metric,
        "value": dict(ev.value),
        "observed_at": ev.observed_at,
    }
    if not private:
        return EvidenceSummaryPublic(**base)
    return EvidencePublic(
        **base, confidence=ev.confidence, source_kind=ev.source_kind, source_ref=ev.source_ref
    )


class EvidencePage(_Strict):
    items: list[EvidenceSummaryPublic | EvidencePublic]
    next_cursor: str | None
    limit: int


class RefreshResult(_Strict):
    collected: int
    metrics: list[str]
    observed_at: datetime
    status: Literal["ok", "unavailable"] = "ok"  # A-4: public page answered >= 400


# --------------------------------------------------------------------- cost --
class CostDayPublic(_Strict):
    day: str
    calls: int
    errors: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: Decimal | None

    @classmethod
    def from_domain(cls, d: CostDay) -> CostDayPublic:
        return cls(**d.model_dump())


class CostReport(_Strict):
    days: int
    items: list[CostDayPublic]
    total_calls: int
    prompt_tokens: int
    completion_tokens: int
    total_estimated_cost_usd: Decimal | None


# ----------------------------------------------------- recommendations (B-3) --
class EvidenceUsedPublic(_Strict):
    id: UUID
    kind: Literal["measured", "inferred", "estimated"]


class DiagnosisPointPublic(_Strict):
    text: str
    evidence_ids: list[UUID]


class DiagnosisPublic(_Strict):
    """``POST /brain/diagnose`` — the narrative plus what was stored as ``proposed``
    recommendations. Grade tags in every text come from the **server**."""

    summary: str
    strengths: list[DiagnosisPointPublic]
    gaps: list[DiagnosisPointPublic]
    evidence_used: list[EvidenceUsedPublic]
    recommendations: list[RecommendationPublic]
    recommendations_dropped: int
    dropped: int = 0  # A-7 (B5): literal name from the directive; equals recommendations_dropped
    prompt_version: str


class RecommendationPublic(_Strict):
    id: UUID
    kind: str
    title: str
    rationale: str
    evidence_ids: list[UUID]
    evidence_kinds_summary: dict[str, int]
    status: str
    decided_by_user_id: UUID | None
    decided_at: datetime | None
    materialized_item_id: UUID | None
    provenance: dict[str, Any]  # public keys only unless OWNER/MANAGER (D-066)
    created_at: datetime

    @classmethod
    def from_domain(cls, rec: Recommendation, *, private: bool) -> RecommendationPublic:
        provenance = (
            dict(rec.provenance)
            if private
            else {k: v for k, v in rec.provenance.items() if k in PUBLIC_RECOMMENDATION_KEYS}
        )
        return cls(
            id=rec.id,
            kind=rec.kind,
            title=rec.title,
            rationale=rec.rationale,
            evidence_ids=list(rec.evidence_ids),
            evidence_kinds_summary=rec.evidence_kinds_summary.model_dump(),
            status=rec.status,
            decided_by_user_id=rec.decided_by_user_id,
            decided_at=rec.decided_at,
            materialized_item_id=rec.materialized_item_id,
            provenance=provenance,
            created_at=rec.created_at,
        )


# D-066: the provenance keys every role may see; provider/model/tokens/latency/
# credential_mode are private (OWNER/MANAGER).
PUBLIC_RECOMMENDATION_KEYS = frozenset({"kind", "generated_at", "prompt_version"})


class RecommendationsPage(_Strict):
    items: list[RecommendationPublic]
    limit: int


class ContentPlanItemPublic(_Strict):
    recommendation_id: UUID
    item_id: UUID
    channel: str
    title: str
    status: str  # always ``draft``


class ContentPlanPublic(_Strict):
    """``POST /brain/content-plan`` — ≤7 drafts, every one ``status=draft``."""

    items: list[ContentPlanItemPublic]
    items_dropped: int
    prompt_version: str


DiagnosisPublic.model_rebuild()
