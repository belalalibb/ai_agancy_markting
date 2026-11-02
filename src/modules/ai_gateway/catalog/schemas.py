"""HTTP schemas of ``/api/v1/admin/ai/*`` (SPRINT-11.1 C-1/C-2, D-077).

Zero-Secret contract (G4): ``api_key`` exists on **input** only (``SecretStr``, encrypted on
receipt and dropped); no response model carries ``api_key`` / ``credential_cipher``. The
``credential_ref`` returned is the *name* of an environment variable, never its value.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from src.modules.ai_gateway.catalog.domain import (
    SLUG_PATTERN,
    Capability,
    Model,
    ModelPricing,
    Provider,
    ProviderKind,
    Purpose,
    Route,
)
from src.modules.ai_gateway.contract import ErrorCategory

_UNSET: object = object()


# ---------------------------------------------------------------- providers --
class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    slug: str = Field(min_length=1, max_length=32, pattern=SLUG_PATTERN)
    kind: ProviderKind
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr | None = None
    credential_ref: str | None = Field(default=None, min_length=1, max_length=64)
    timeout_seconds: Decimal = Field(default=Decimal("30"), gt=Decimal("0"), le=Decimal("9999.9"))
    enabled: bool = True

    @model_validator(mode="after")
    def _one_credential(self) -> ProviderCreate:
        if self.api_key is not None and self.credential_ref is not None:
            raise ValueError("send either api_key or credential_ref, not both")
        return self


class ProviderPatch(BaseModel):
    """Partial update. ``api_key`` replaces the stored cipher; an explicit ``null`` clears it
    (``credential_ref`` likewise). Omitted fields are untouched."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    timeout_seconds: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("9999.9"))
    api_key: SecretStr | None = None
    credential_ref: str | None = Field(default=None, min_length=1, max_length=64)

    def sent(self, name: str) -> bool:
        return name in self.model_fields_set

    @model_validator(mode="after")
    def _one_credential(self) -> ProviderPatch:
        """SPRINT-12 A-6b (H6b): the same ``api_key ⊕ credential_ref`` rule as
        :class:`ProviderCreate` — a patch naming both would silently drop the key."""
        if self.api_key is not None and self.credential_ref is not None:
            raise ValueError("send either api_key or credential_ref, not both")
        return self


class ProviderOut(BaseModel):
    id: UUID
    slug: str
    kind: ProviderKind
    base_url: str
    has_credential: bool
    credential_ref: str | None
    enabled: bool
    timeout_seconds: Decimal
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, p: Provider) -> ProviderOut:
        return cls(
            id=p.id,
            slug=p.slug,
            kind=p.kind,
            base_url=p.base_url,
            has_credential=p.has_credential,
            credential_ref=p.credential_ref,
            enabled=p.enabled,
            timeout_seconds=p.timeout_seconds,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )


# ------------------------------------------------------------------- models --
class PricingIn(BaseModel):
    """Token pricing (``prompt_per_1k`` + ``completion_per_1k``) **or** audio pricing
    (``cost_per_minute_usd`` + ``max_audio_seconds`` ≤ 120); all optional ⇒ unpriced."""

    model_config = ConfigDict(extra="forbid")

    prompt_per_1k: Decimal | None = Field(default=None, ge=Decimal("0"))
    completion_per_1k: Decimal | None = Field(default=None, ge=Decimal("0"))
    cost_per_minute_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    max_audio_seconds: int | None = Field(default=None, ge=1, le=120)

    def to_domain(self) -> ModelPricing:
        return ModelPricing(**self.model_dump())


class ModelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider_id: UUID
    model_name: str = Field(min_length=1, max_length=64)
    capability: Capability = "text"
    pricing: PricingIn = Field(default_factory=PricingIn)
    max_tokens_cap: int | None = Field(default=None, ge=1)
    enabled: bool = True

    @model_validator(mode="after")
    def _pricing_matches_capability(self) -> ModelCreate:
        if not self.pricing.to_domain().is_shape_for(self.capability):
            raise ValueError(f"pricing shape does not match capability {self.capability!r}")
        return self


class ModelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    pricing: PricingIn | None = None
    max_tokens_cap: int | None = Field(default=None, ge=1)

    def sent(self, name: str) -> bool:
        return name in self.model_fields_set


class ModelOut(BaseModel):
    id: UUID
    provider_id: UUID
    model_name: str
    capability: Capability
    pricing: ModelPricing
    enabled: bool
    max_tokens_cap: int | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, m: Model) -> ModelOut:
        return cls(**m.model_dump())


# ------------------------------------------------------------------ routing --
class RoutingPut(BaseModel):
    """``PUT /admin/ai/routing/{purpose}`` — the full ordered chain (index = priority)."""

    model_config = ConfigDict(extra="forbid")

    models: list[UUID] = Field(max_length=16)

    @model_validator(mode="after")
    def _unique(self) -> RoutingPut:
        if len(set(self.models)) != len(self.models):
            raise ValueError("a model may appear once per purpose")
        return self


class RouteOut(BaseModel):
    id: UUID
    purpose: Purpose
    model_id: UUID
    priority: int
    enabled: bool

    @classmethod
    def from_domain(cls, r: Route) -> RouteOut:
        return cls(
            id=r.id, purpose=r.purpose, model_id=r.model_id, priority=r.priority, enabled=r.enabled
        )


# -------------------------------------------------------------------- probe --
class ProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: UUID
    prompt: str = Field(min_length=1, max_length=500)
    max_tokens: int = Field(default=16, ge=1, le=64)


class ProbeOut(BaseModel):
    """Diagnostic result — provider failures are **200 ``ok=false``** with one of the 12
    ``ErrorCategory`` names (the probe is a tool, not a request to be retried)."""

    ok: bool
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    estimated_cost_usd: Decimal | None
    preview: str = Field(max_length=200)
    error_category: ErrorCategory | None


__all__ = [
    "ModelCreate",
    "ModelOut",
    "ModelPatch",
    "PricingIn",
    "ProbeOut",
    "ProbeRequest",
    "ProviderCreate",
    "ProviderOut",
    "ProviderPatch",
    "RouteOut",
    "RoutingPut",
]
