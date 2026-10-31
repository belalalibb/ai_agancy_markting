"""SPRINT-10 A-5 (N5): one pending reply suggestion per conversation.

``uq_wa_reply_drafts_one_pending`` — partial unique index on ``wa_reply_drafts
(conversation_id) WHERE status = 'suggested'`` so two concurrent ``suggest-reply`` requests
that both passed the ``pending_draft`` check (before ``gateway.complete()``) cannot stack
drafts: the loser hits ``IntegrityError`` ⇒ 409 ``draft_pending``.

Pre-cleanup: where a conversation already holds several ``suggested`` drafts, every one but
the **newest** is marked ``expired`` (``decided_at = now()``) before the index is built.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "uq_wa_reply_drafts_one_pending"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE wa_reply_drafts d
               SET status = 'expired', decided_at = now()
             WHERE d.status = 'suggested'
               AND d.id <> (
                   SELECT n.id FROM wa_reply_drafts n
                    WHERE n.conversation_id = d.conversation_id AND n.status = 'suggested'
                    ORDER BY n.created_at DESC, n.id DESC
                    LIMIT 1
               )
            """
        )
    )
    op.create_index(
        INDEX_NAME,
        "wa_reply_drafts",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("status = 'suggested'"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="wa_reply_drafts")
