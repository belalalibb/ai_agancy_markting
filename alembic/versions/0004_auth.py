"""auth — users / refresh_sessions

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-30

SPRINT-04 Part A (A3).

upgrade()
---------
1. ``users`` — one login per tenant (``tenant_id`` UNIQUE, FK ``tenants`` ON DELETE RESTRICT),
   ``email VARCHAR(320)`` with the unique functional index ``uq_users_email_lower`` on
   ``lower(email)`` (case-insensitive uniqueness), Argon2id ``password_hash TEXT``.
2. ``refresh_sessions`` — ``id`` is the JWT ``sid``; ``token_hash`` = hex SHA-256 of the
   cookie secret (the secret itself is never stored); ``replaced_by`` links a rotated
   session to its successor (reuse detection); ``ip INET`` / ``user_agent`` for audit.

downgrade()
-----------
Drops both tables (sessions first). No data is migrated into or out of other tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str) -> sa.Column:
    return sa.Column(
        name,
        postgresql.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT", name="fk_users_tenant_id"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _ts("created_at"),
        _ts("updated_at"),
        sa.Column("last_login_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", name="uq_users_tenant_id"),
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)

    op.create_table(
        "refresh_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_refresh_sessions_user_id"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revoked_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "replaced_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "refresh_sessions.id",
                ondelete="SET NULL",
                name="fk_refresh_sessions_replaced_by",
            ),
            nullable=True,
        ),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        _ts("created_at"),
    )
    op.create_index("ix_refresh_sessions_user_id", "refresh_sessions", ["user_id"])
    op.create_index("ix_refresh_sessions_expires_at", "refresh_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_refresh_sessions_expires_at", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_user_id", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_table("users")
