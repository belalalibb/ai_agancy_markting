"""page token nullable

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29

SPRINT-02.1 F4: a Facebook Page the user can *see* but has no role on comes back from
``/me/accounts`` without ``access_token``. Such Pages are now stored with
``encrypted_page_token = NULL`` (= "no token granted") instead of an empty-string
pseudo-ciphertext, so the column must accept NULL.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("social_connection_pages", "encrypted_page_token", nullable=True)


def downgrade() -> None:
    # Restore the legacy sentinel for rows that have no token, then re-tighten the column.
    op.execute(
        "UPDATE social_connection_pages SET encrypted_page_token = '' "
        "WHERE encrypted_page_token IS NULL"
    )
    op.alter_column("social_connection_pages", "encrypted_page_token", nullable=False)
