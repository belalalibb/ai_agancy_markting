"""content — content_items / content_revisions / approval_events + scopes_verified

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-30

SPRINT-06 (Part A A-1 + Part B-1 B1).

upgrade()
---------
1. ``social_connections.scopes_verified BOOLEAN NOT NULL DEFAULT false`` (A-1): ``True`` iff
   ``granted_scopes`` came from a successful ``/debug_token``. Backfill: rows that already
   hold a non-empty scope list were necessarily filled by debug_token ⇒ ``true``.
2. ``content_items`` / ``content_revisions`` / ``approval_events`` (B1) — see Part B-1.

downgrade()
-----------
Drops the content tables (events → revisions → items) and the ``scopes_verified`` column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------------------------------------------------------- A-1 ---
    op.add_column(
        "social_connections",
        sa.Column(
            "scopes_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.execute(
        "UPDATE social_connections SET scopes_verified = true "
        "WHERE granted_scopes IS NOT NULL AND granted_scopes::text <> '[]'"
    )


def downgrade() -> None:
    op.drop_column("social_connections", "scopes_verified")
