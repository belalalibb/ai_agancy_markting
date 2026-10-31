"""channels — widen the channel CHECK to the five registry channels

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-31

SPRINT-08 Part B-1 (B1-د).

upgrade()
---------
1. ``ck_content_items_channel`` ⇒ ``channel IN ('facebook_page', 'instagram', 'linkedin',
   'x', 'tiktok')`` — drafts and approvals are allowed for every registry channel; only
   ``schedule`` is refused for non-activated ones (422 ``channel_not_activated``).
2. ``publish_jobs.channel`` has **no** CHECK in 0006 (a plain ``VARCHAR(16)``) — nothing to
   widen there; no constraint is *added* either ("nothing else in 0007").

downgrade()
-----------
Rows whose ``channel`` is not one of the two 0006 values cannot survive the narrowed CHECK:
``UPDATE … SET status = 'archived'`` alone would still violate it (the column keeps
``'linkedin'``), so such rows are **deleted** after being archived — children first
(``publish_jobs``/``publish_attempts`` via CASCADE, ``approval_events`` via CASCADE,
``content_revisions`` via CASCADE) with the circular ``content_items.current_revision_id``
FK (``ON DELETE SET NULL``) released explicitly first (0005/0006 pattern, lesson 15: order).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_CHANNELS = "'facebook_page', 'instagram'"
NEW_CHANNELS = OLD_CHANNELS + ", 'linkedin', 'x', 'tiktok'"


def upgrade() -> None:
    op.drop_constraint("ck_content_items_channel", "content_items", type_="check")
    op.create_check_constraint(
        "ck_content_items_channel", "content_items", f"channel IN ({NEW_CHANNELS})"
    )


def downgrade() -> None:
    # 1. Archive (directive wording) — keeps the intent visible in any audit of this step.
    op.execute(
        f"UPDATE content_items SET status = 'archived', scheduled_for = NULL, "  # noqa: S608 - constant literals
        f"published_at = NULL WHERE channel NOT IN ({OLD_CHANNELS})"
    )
    # 2. The archived rows still carry a channel the 0006 CHECK forbids ⇒ remove them.
    #    Release the circular FK first, then delete; revisions / events / jobs / attempts
    #    follow through ``ON DELETE CASCADE``.
    op.execute(
        f"UPDATE content_items SET current_revision_id = NULL "  # noqa: S608 - constant literals
        f"WHERE channel NOT IN ({OLD_CHANNELS})"
    )
    op.execute(
        f"DELETE FROM content_items WHERE channel NOT IN ({OLD_CHANNELS})"  # noqa: S608 - constant literals
    )
    # 3. Narrow the CHECK back to the SPRINT-06 pair.
    op.drop_constraint("ck_content_items_channel", "content_items", type_="check")
    op.create_check_constraint(
        "ck_content_items_channel", "content_items", f"channel IN ({OLD_CHANNELS})"
    )
