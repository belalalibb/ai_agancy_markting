"""ORM rows for the provider catalog (SPRINT-11.1 B-1, migration ``0015``). Every constraint is
**named** so ``test_b1_migration_0015_round_trip_on_pg`` can watch the eleven of them disappear
and return."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base

PROVIDER_CONSTRAINTS = (
    "uq_ai_providers_slug",
    "ck_ai_providers_slug",
    "ck_ai_providers_kind",
    "ck_ai_providers_one_credential",
)
MODEL_CONSTRAINTS = (
    "uq_ai_models_provider_name",
    "ck_ai_models_capability",
    "ck_ai_models_max_audio_seconds",
    "ck_ai_models_price_shape",
    "ck_ai_models_prices_non_negative",
)
ROUTING_CONSTRAINTS = (
    "ck_ai_routing_purpose",
    "uq_ai_routing_purpose_priority",
    "uq_ai_routing_purpose_model",
)
#: The twelve named constraints of migration 0015 (plus the three table PKs) — the
#: directive counts eleven; ``ck_ai_models_prices_non_negative`` is the extra one (§6 B-2).
NAMED_CONSTRAINTS = PROVIDER_CONSTRAINTS + MODEL_CONSTRAINTS + ROUTING_CONSTRAINTS


def _ts(*, nullable: bool = False) -> Mapped[datetime]:
    return mapped_column(TIMESTAMP(timezone=True), nullable=nullable, server_default=func.now())


class AIProviderRow(Base):
    __tablename__ = "ai_providers"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_ai_providers_slug"),
        CheckConstraint("slug ~ '^[a-z0-9_-]+$'", name="ck_ai_providers_slug"),
        CheckConstraint("kind IN ('openai_compatible', 'fake')", name="ck_ai_providers_kind"),
        CheckConstraint(
            "NOT (credential_cipher IS NOT NULL AND credential_ref IS NOT NULL)",
            name="ck_ai_providers_one_credential",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    credential_cipher: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    timeout_seconds: Mapped[Decimal] = mapped_column(
        Numeric(5, 1), nullable=False, server_default="30"
    )
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class AIModelRow(Base):
    __tablename__ = "ai_models"
    __table_args__ = (
        UniqueConstraint("provider_id", "model_name", name="uq_ai_models_provider_name"),
        CheckConstraint("capability IN ('text', 'audio')", name="ck_ai_models_capability"),
        CheckConstraint(
            "max_audio_seconds IS NULL OR max_audio_seconds <= 120",
            name="ck_ai_models_max_audio_seconds",
        ),
        CheckConstraint(
            "(capability = 'text' AND cost_per_minute_usd IS NULL AND max_audio_seconds IS NULL)"
            " OR (capability = 'audio' AND prompt_per_1k IS NULL AND completion_per_1k IS NULL)",
            name="ck_ai_models_price_shape",
        ),
        CheckConstraint(
            "(prompt_per_1k IS NULL OR prompt_per_1k >= 0) AND "
            "(completion_per_1k IS NULL OR completion_per_1k >= 0) AND "
            "(cost_per_minute_usd IS NULL OR cost_per_minute_usd >= 0)",
            name="ck_ai_models_prices_non_negative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_providers.id", ondelete="CASCADE"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt_per_1k: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    completion_per_1k: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    cost_per_minute_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    max_audio_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    max_tokens_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class AIRoutingRow(Base):
    __tablename__ = "ai_routing"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('content_generation', 'whatsapp_suggest_reply', 'brand_diagnosis', "
            "'content_plan', 'audio_transcription')",
            name="ck_ai_routing_purpose",
        ),
        UniqueConstraint("purpose", "priority", name="uq_ai_routing_purpose_priority"),
        UniqueConstraint("purpose", "model_id", name="uq_ai_routing_purpose_model"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_models.id", ondelete="CASCADE"), nullable=False
    )
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
