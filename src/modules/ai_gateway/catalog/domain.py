"""Catalog domain (SPRINT-11.1 B-2, D-077): providers, models, routes — frozen Pydantic.

A :class:`Provider` **never carries a raw key**: it holds either ``credential_cipher`` (Fernet
text written by the admin API) or ``credential_ref`` (the *name* of an environment variable).
The key is resolved by ``CredentialResolver`` inside ``CatalogGatewayResolver`` at client-build
time only, never logged, never returned.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Catalog routing purposes (``ai_routing.purpose`` CHECK). The live gateway purposes map
#: onto them through :data:`PURPOSE_ALIASES` (§5-5 — see §6 deviation B-1).
Purpose = Literal[
    "content_generation",
    "whatsapp_suggest_reply",
    "brand_diagnosis",
    "content_plan",
    "audio_transcription",
]
PURPOSES: tuple[str, ...] = (
    "content_generation",
    "whatsapp_suggest_reply",
    "brand_diagnosis",
    "content_plan",
    "audio_transcription",
)
#: ``gateway.Purpose`` (what live callers send) ⇒ catalog purpose. ``competitor_gap`` and
#: ``tone_profile`` are Brand-Brain analyses priced/ routed like ``brand_diagnosis``;
#: ``admin_probe`` has no route — the probe names its model explicitly.
PURPOSE_ALIASES: dict[str, str] = {
    "content_draft": "content_generation",
    "reply_suggestion": "whatsapp_suggest_reply",
    "brand_diagnosis": "brand_diagnosis",
    "competitor_gap": "brand_diagnosis",
    "tone_profile": "brand_diagnosis",
    "content_plan": "content_plan",
    "audio_transcription": "audio_transcription",
}
ProviderKind = Literal["openai_compatible", "fake"]
Capability = Literal["text", "audio"]
MAX_AUDIO_SECONDS = 120
SLUG_PATTERN = r"^[a-z0-9_-]+$"


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def catalog_purpose(gateway_purpose: str) -> str | None:
    """Map a live ``CompletionRequest.purpose`` onto a catalog purpose (``None`` ⇒ unknown)."""
    return PURPOSE_ALIASES.get(gateway_purpose)


class ModelPricing(BaseModel):
    """``token(prompt, completion)`` **or** ``audio(per_minute, max_seconds)`` — the shape is
    enforced here and by ``ck_ai_models_price_shape``. A ``None`` price means *not entered*
    (⇒ 503 ``ai_model_unpriced`` in strict mode); ``Decimal("0")`` is a valid price."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt_per_1k: Decimal | None = Field(default=None, ge=Decimal("0"))
    completion_per_1k: Decimal | None = Field(default=None, ge=Decimal("0"))
    cost_per_minute_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    max_audio_seconds: int | None = Field(default=None, ge=1, le=MAX_AUDIO_SECONDS)

    @classmethod
    def token(cls, prompt: Decimal, completion: Decimal) -> ModelPricing:
        return cls(prompt_per_1k=prompt, completion_per_1k=completion)

    @classmethod
    def audio(cls, per_minute: Decimal, max_seconds: int) -> ModelPricing:
        return cls(cost_per_minute_usd=per_minute, max_audio_seconds=max_seconds)

    def is_shape_for(self, capability: str) -> bool:
        if capability == "text":
            return self.cost_per_minute_usd is None and self.max_audio_seconds is None
        return self.prompt_per_1k is None and self.completion_per_1k is None

    @property
    def token_unit(self) -> tuple[Decimal, Decimal] | None:
        if self.prompt_per_1k is None or self.completion_per_1k is None:
            return None
        return (self.prompt_per_1k, self.completion_per_1k)


class Provider(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    slug: str = Field(min_length=1, max_length=32, pattern=SLUG_PATTERN)
    kind: ProviderKind
    base_url: str = Field(min_length=1)
    credential_cipher: str | None = None
    credential_ref: str | None = Field(default=None, max_length=64)
    enabled: bool = True
    timeout_seconds: Decimal = Field(default=Decimal("30"), gt=Decimal("0"), le=Decimal("9999.9"))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _one_credential(self) -> Provider:
        if self.credential_cipher is not None and self.credential_ref is not None:
            raise ValueError("a provider carries either credential_cipher or credential_ref")
        return self

    @property
    def has_credential(self) -> bool:
        return self.credential_cipher is not None or self.credential_ref is not None


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    provider_id: UUID
    model_name: str = Field(min_length=1, max_length=64)
    capability: Capability = "text"
    pricing: ModelPricing = Field(default_factory=ModelPricing)
    enabled: bool = True
    max_tokens_cap: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _price_shape(self) -> Model:
        if not self.pricing.is_shape_for(self.capability):
            raise ValueError(f"pricing shape does not match capability {self.capability!r}")
        return self


class Route(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    purpose: Purpose
    model_id: UUID
    priority: int = Field(ge=0, le=32_767)
    enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ResolvedModel(BaseModel):
    """One routable (provider, model) pair — the output of ``ProviderCatalog.resolve``."""

    model_config = ConfigDict(frozen=True)

    provider: Provider
    model: Model
    priority: int


class CatalogSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    providers: tuple[Provider, ...] = ()
    models: tuple[Model, ...] = ()
    routes: tuple[Route, ...] = ()
