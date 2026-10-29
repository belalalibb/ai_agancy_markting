"""initial social connections

Revision ID: 0001
Revises:
Create Date: 2026-10-29

Creates the pgvector extension (ARCHITECTURE_LOCK D-032 — no vector tables yet) and
the two ``social_connect`` tables (SPRINT-02 §5-B4).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "social_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("provider_user_id", sa.String(length=64), nullable=False),
        sa.Column("provider_user_name", sa.String(length=255), nullable=True),
        sa.Column("encrypted_user_token", sa.Text(), nullable=False),
        sa.Column("token_expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "granted_scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("connected_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_synced_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id", "provider", "provider_user_id", name="uq_social_connections_identity"
        ),
    )
    op.create_index("ix_social_connections_tenant_id", "social_connections", ["tenant_id"])

    op.create_table(
        "social_connection_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("social_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_id", sa.String(length=64), nullable=False),
        sa.Column("page_name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column(
            "tasks",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("encrypted_page_token", sa.Text(), nullable=False),
        sa.Column("ig_user_id", sa.String(length=64), nullable=True),
        sa.Column("ig_username", sa.String(length=255), nullable=True),
        sa.Column("ig_name", sa.String(length=255), nullable=True),
        sa.Column("ig_profile_picture_url", sa.Text(), nullable=True),
        sa.UniqueConstraint("connection_id", "page_id", name="uq_social_connection_pages_page"),
    )
    op.create_index(
        "ix_social_connection_pages_connection_id", "social_connection_pages", ["connection_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_social_connection_pages_connection_id", table_name="social_connection_pages")
    op.drop_table("social_connection_pages")
    op.drop_index("ix_social_connections_tenant_id", table_name="social_connections")
    op.drop_table("social_connections")
    # The extension is shared infrastructure (D-032); it is intentionally left in place.
