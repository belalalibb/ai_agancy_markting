"""SPRINT-11 B-2 — ``ai_cost_log`` ownership moves to ``ai_gateway`` + reserve-before-call.

Changes (PostgreSQL round-trip tested by ``test_b1_migration_0013_round_trip``):

* ``outcome`` becomes ``VARCHAR(16)`` and may be ``pending`` / ``ambiguous`` besides
  ``ok`` / ``error`` (``ck_ai_cost_log_outcome`` is recreated accordingly);
* ``brand_id`` becomes **nullable** and the FK is recreated with ``ON DELETE SET NULL``
  (D-081: the financial log outlives the brand for 7 years, anonymised);
* new columns ``settled_at TIMESTAMPTZ NULL`` and ``request_id VARCHAR(64) NULL``;
* index ``ix_ai_cost_log_mode_created (credential_mode, created_at)`` for the platform USD
  cap and the partial index ``ix_ai_cost_log_pending (created_at) WHERE outcome = 'pending'``
  for ``settle_stale_reservations``.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "ai_cost_log"


def upgrade() -> None:
    op.alter_column(
        TABLE,
        "outcome",
        existing_type=sa.String(8),
        type_=sa.String(16),
        existing_nullable=False,
    )
    op.drop_constraint("ck_ai_cost_log_outcome", TABLE, type_="check")
    op.create_check_constraint(
        "ck_ai_cost_log_outcome",
        TABLE,
        "outcome IN ('pending', 'ok', 'error', 'ambiguous')",
    )
    # D-081: brand deletion anonymises the row instead of deleting it.
    op.drop_constraint("fk_ai_cost_log_brand_id", TABLE, type_="foreignkey")
    op.alter_column(TABLE, "brand_id", existing_nullable=False, nullable=True)
    op.create_foreign_key(
        "fk_ai_cost_log_brand_id", TABLE, "brands", ["brand_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column(TABLE, sa.Column("settled_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column(TABLE, sa.Column("request_id", sa.String(64), nullable=True))
    op.create_index("ix_ai_cost_log_mode_created", TABLE, ["credential_mode", "created_at"])
    op.create_index(
        "ix_ai_cost_log_pending",
        TABLE,
        ["created_at"],
        postgresql_where=sa.text("outcome = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_ai_cost_log_pending", table_name=TABLE)
    op.drop_index("ix_ai_cost_log_mode_created", table_name=TABLE)
    op.drop_column(TABLE, "request_id")
    op.drop_column(TABLE, "settled_at")
    # Rows that are not representable in the 0012 schema: unsettled reservations (``pending``
    # / ``ambiguous``) are mapped to ``error`` with a synthetic category so the
    # ``error_iff_category`` CHECK still holds; **orphaned financial rows are deleted** —
    # WARNING: this discards the anonymised history D-081 keeps on the 0013 schema.
    op.execute(
        sa.text(
            "UPDATE ai_cost_log SET outcome = 'error', error_category = 'downgraded' "
            "WHERE outcome IN ('pending', 'ambiguous')"
        )
    )
    op.execute(sa.text("DELETE FROM ai_cost_log WHERE brand_id IS NULL"))
    op.drop_constraint("fk_ai_cost_log_brand_id", TABLE, type_="foreignkey")
    op.alter_column(TABLE, "brand_id", existing_nullable=True, nullable=False)
    op.create_foreign_key(
        "fk_ai_cost_log_brand_id", TABLE, "brands", ["brand_id"], ["id"], ondelete="CASCADE"
    )
    op.drop_constraint("ck_ai_cost_log_outcome", TABLE, type_="check")
    op.create_check_constraint("ck_ai_cost_log_outcome", TABLE, "outcome IN ('ok', 'error')")
    op.alter_column(
        TABLE,
        "outcome",
        existing_type=sa.String(16),
        type_=sa.String(8),
        existing_nullable=False,
    )
