"""SPRINT-11.1 B-1 — runtime provider catalog (D-077): ``ai_providers`` / ``ai_models`` /
``ai_routing``. Three new tables only; no existing table is touched. Eleven **named**
constraints; ``downgrade`` drops the three tables in reverse order. Round-trip tested on
PostgreSQL by ``test_b1_migration_0015_round_trip_on_pg`` (``head ⇒ 0014 ⇒ head``).

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str) -> sa.Column:
    return sa.Column(
        name, postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    op.create_table(
        "ai_providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("credential_cipher", sa.Text(), nullable=True),
        sa.Column("credential_ref", sa.String(64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("timeout_seconds", sa.Numeric(5, 1), nullable=False, server_default="30"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("slug", name="uq_ai_providers_slug"),
        sa.CheckConstraint("slug ~ '^[a-z0-9_-]+$'", name="ck_ai_providers_slug"),
        sa.CheckConstraint("kind IN ('openai_compatible', 'fake')", name="ck_ai_providers_kind"),
        sa.CheckConstraint(
            "NOT (credential_cipher IS NOT NULL AND credential_ref IS NOT NULL)",
            name="ck_ai_providers_one_credential",
        ),
    )
    op.create_table(
        "ai_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(64), nullable=False),
        sa.Column("capability", sa.String(16), nullable=False),
        sa.Column("prompt_per_1k", sa.Numeric(12, 6), nullable=True),
        sa.Column("completion_per_1k", sa.Numeric(12, 6), nullable=True),
        sa.Column("cost_per_minute_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("max_audio_seconds", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("max_tokens_cap", sa.Integer(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("provider_id", "model_name", name="uq_ai_models_provider_name"),
        sa.CheckConstraint("capability IN ('text', 'audio')", name="ck_ai_models_capability"),
        sa.CheckConstraint(
            "max_audio_seconds IS NULL OR max_audio_seconds <= 120",
            name="ck_ai_models_max_audio_seconds",
        ),
        sa.CheckConstraint(
            "(capability = 'text' AND cost_per_minute_usd IS NULL AND max_audio_seconds IS NULL)"
            " OR (capability = 'audio' AND prompt_per_1k IS NULL AND completion_per_1k IS NULL)",
            name="ck_ai_models_price_shape",
        ),
        sa.CheckConstraint(
            "(prompt_per_1k IS NULL OR prompt_per_1k >= 0) AND "
            "(completion_per_1k IS NULL OR completion_per_1k >= 0) AND "
            "(cost_per_minute_usd IS NULL OR cost_per_minute_usd >= 0)",
            name="ck_ai_models_prices_non_negative",
        ),
    )
    op.create_table(
        "ai_routing",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column(
            "model_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_models.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("priority", sa.SmallInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint(
            "purpose IN ('content_generation', 'whatsapp_suggest_reply', 'brand_diagnosis', "
            "'content_plan', 'audio_transcription')",
            name="ck_ai_routing_purpose",
        ),
        sa.UniqueConstraint("purpose", "priority", name="uq_ai_routing_purpose_priority"),
        sa.UniqueConstraint("purpose", "model_id", name="uq_ai_routing_purpose_model"),
    )


def downgrade() -> None:
    op.drop_table("ai_routing")
    op.drop_table("ai_models")
    op.drop_table("ai_providers")
