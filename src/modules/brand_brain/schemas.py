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
    def from_domain(cls, rev: BrandProfileRevision) -> RevisionPublic:
        return cls(
            id=rev.id,
            version=rev.version,
            source=rev.source,
            changed_by_user_id=rev.changed_by_user_id,
            created_at=rev.created_at,
            snapshot=dict(rev.snapshot),
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
    total_prompt_tokens: int
    total_completion_tokens: int
    total_estimated_cost_usd: Decimal | None
