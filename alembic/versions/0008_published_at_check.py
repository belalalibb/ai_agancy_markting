"""published_at CHECK — allow an archived item to keep its ``published_at``

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-31

SPRINT-09 Part A-1 (SPRINT-08 review F1).

Problem
-------
0006 added ``ck_content_items_published_at_iff_published`` as
``(status = 'published') = (published_at IS NOT NULL)``. ``compare_and_set_status`` keeps
``published_at`` when a *published* item is archived (intended — it is history), so the
``published ⇒ archived`` transition violated the CHECK on PostgreSQL (latent: SQLite tests
never hit it).

upgrade()
---------
Drop the constraint and recreate it **under the same name** with::

    (status = 'published' AND published_at IS NOT NULL)
    OR (status = 'archived')
    OR (status NOT IN ('published', 'archived') AND published_at IS NULL)

``archived`` may carry ``published_at`` (was published) or not (archived from scheduled /
draft / …). Every other status still requires ``published_at IS NULL``; ``published`` still
requires it NOT NULL. ``compare_and_set_status`` is **not** changed.

downgrade()
-----------
Archived rows with ``published_at`` set would violate the 0006 CHECK, so they are nulled
first (``UPDATE content_items SET published_at = NULL WHERE status = 'archived'``), then the
0006 expression is restored under the same name.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_content_items_published_at_iff_published"
TABLE = "content_items"

NEW_EXPRESSION = (
    "(status = 'published' AND published_at IS NOT NULL) "
    "OR (status = 'archived') "
    "OR (status NOT IN ('published', 'archived') AND published_at IS NULL)"
)
OLD_EXPRESSION = "(status = 'published') = (published_at IS NOT NULL)"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, NEW_EXPRESSION)


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    # Archived rows keeping their publish timestamp cannot satisfy the 0006 CHECK.
    op.execute("UPDATE content_items SET published_at = NULL WHERE status = 'archived'")
    op.create_check_constraint(CONSTRAINT, TABLE, OLD_EXPRESSION)
