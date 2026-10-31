"""Brand Brain domain models (SPRINT-10 B-1, migration 0011).

Pure pydantic — no SQLAlchemy, no I/O. The JSONB shapes of ``brand_profiles`` are typed here
(``Product``, ``Audience``, ``Tone``, ``Goal``) so the service validates them once and the
database stores the normalised ``model_dump()``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.modules.brand_brain.domain.metrics import (
    EvidenceKind,
    SourceKind,
    SubjectKind,
    is_known_metric,
    source_allowed,
)

MAX_PRODUCTS = 20
MAX_GOALS = 5
MAX_COMPETITORS_PER_BRAND = 3
MAX_ALLOWED_HOSTS = 3
MAX_REPLY_TEMPLATES = 10

GoalKind = Literal["awareness", "leads", "sales", "retention"]
CompetitorKind = Literal["direct", "indirect"]
RevisionSource = Literal["human", "ai_suggested"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ profile --
class Product(_Strict):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=600)
    price_hint: str | None = Field(default=None, max_length=60)
    url: str | None = Field(default=None, max_length=300)


class Audience(_Strict):
    segments: list[str] = Field(default_factory=list, max_length=10)
    regions: list[str] = Field(default_factory=list, max_length=10)
    languages: list[str] = Field(default_factory=list, max_length=5)
    age_bands: list[str] = Field(default_factory=list, max_length=8)


class Tone(_Strict):
    voice: str = Field(default="", max_length=200)
    formality: Literal["casual", "neutral", "formal"] = "neutral"
    emoji_policy: Literal["none", "light", "free"] = "light"
    banned_phrases: list[str] = Field(default_factory=list, max_length=50)
    # B-3: accepted ``reply_template`` recommendations land here (≤10, after accept only).
    reply_templates: list[str] = Field(default_factory=list, max_length=MAX_REPLY_TEMPLATES)


class Goal(_Strict):
    kind: GoalKind
    target: str = Field(min_length=1, max_length=200)
    horizon_days: int = Field(ge=1, le=365)


class BrandProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand_id: UUID
    products: list[Product] = Field(default_factory=list, max_length=MAX_PRODUCTS)
    audience: Audience = Field(default_factory=Audience)
    tone: Tone = Field(default_factory=Tone)
    goals: list[Goal] = Field(default_factory=list, max_length=MAX_GOALS)
    version: int = Field(default=1, ge=1)
    updated_by_user_id: UUID | None = None
    updated_at: datetime

    def snapshot(self) -> dict[str, Any]:
        """The JSON stored in ``brand_profile_revisions.snapshot``."""
        return {
            "products": [p.model_dump() for p in self.products],
            "audience": self.audience.model_dump(),
            "tone": self.tone.model_dump(),
            "goals": [g.model_dump() for g in self.goals],
        }


class BrandProfileRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    brand_id: UUID
    version: int = Field(ge=1)
    snapshot: dict[str, Any]
    changed_by_user_id: UUID | None = None
    source: RevisionSource = "human"
    created_at: datetime


# -------------------------------------------------------------- competitors --
class Competitor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    brand_id: UUID
    name: str = Field(min_length=1, max_length=120)
    kind: CompetitorKind = "direct"
    facebook_page_id: str | None = Field(default=None, max_length=32)
    website_host: str | None = Field(default=None, max_length=253)
    allowed_hosts: list[str] = Field(default_factory=list, max_length=MAX_ALLOWED_HOSTS)
    notes: str = Field(default="", max_length=600)
    added_by_user_id: UUID
    created_at: datetime
    refreshed_at: datetime | None = None  # B-2: ``competitor_refreshed_at``

    @field_validator("facebook_page_id")
    @classmethod
    def _digits(cls, value: str | None) -> str | None:
        if value is not None and not value.isdigit():
            msg = "facebook_page_id must be numeric"
            raise ValueError(msg)
        return value


# ----------------------------------------------------------------- evidence --
class Evidence(BaseModel):
    """One graded observation (D-074). ``source_ref`` never carries a token."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    brand_id: UUID
    subject_kind: SubjectKind
    subject_id: UUID
    evidence_kind: EvidenceKind
    metric: str = Field(min_length=1, max_length=64)
    value: dict[str, Any]
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"), max_digits=3, decimal_places=2)
    source_kind: SourceKind
    source_ref: str | None = Field(default=None, max_length=512)
    observed_at: datetime
    created_at: datetime

    @field_validator("metric")
    @classmethod
    def _known(cls, value: str) -> str:
        if not is_known_metric(value):
            msg = f"unknown metric {value!r}"
            raise ValueError(msg)
        return value

    @field_validator("source_kind")
    @classmethod
    def _source_matches_kind(cls, value: SourceKind, info: Any) -> SourceKind:
        kind = info.data.get("evidence_kind")
        if kind is not None and not source_allowed(kind, value):
            msg = f"source_kind {value!r} is not allowed for evidence_kind {kind!r}"
            raise ValueError(msg)
        return value


# ----------------------------------------------------------------- cost log --
class CostOutcome(StrEnum):
    OK = "ok"
    ERROR = "error"


class CostEntry(BaseModel):
    """``ai_cost_log`` row — metadata only, never prompt or output text."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    brand_id: UUID
    purpose: str = Field(max_length=32)
    provider: str = Field(max_length=32)
    model: str = Field(max_length=64)
    credential_mode: str = Field(max_length=16)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    latency_ms: int = Field(ge=0)
    prompt_version: str = Field(max_length=32)
    outcome: CostOutcome
    error_category: str | None = Field(default=None, max_length=32)
    created_at: datetime


class CostDay(BaseModel):
    """One row of ``GET /brain/cost`` (daily aggregation)."""

    model_config = ConfigDict(extra="forbid")

    day: str  # ISO date
    calls: int
    errors: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: Decimal | None
