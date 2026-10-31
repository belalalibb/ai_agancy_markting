"""publishing — published/publish_failed states, publish_jobs, publish_attempts

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-30

SPRINT-07 Part B-1 (B1).

upgrade()
---------
1. ``content_items.status`` CHECK += ``'published'``, ``'publish_failed'`` (8 states).
2. ``content_items.published_at TIMESTAMPTZ NULL`` with
   ``(status = 'published') = (published_at IS NOT NULL)``.
3. ``approval_events.action`` widened ``VARCHAR(16)`` ⇒ ``VARCHAR(32)``
   (``'publish_cancelled'`` is 17 chars) and its CHECK += ``'scheduled'``, ``'published'``,
   ``'publish_failed'``, ``'publish_cancelled'``.
4. ``approval_events.actor_tenant_id`` / ``actor_user_id`` become **nullable only for the two
   system events** written by the scheduler worker outside any user request:
   ``ck_approval_events_actor_required`` ⇒
   ``action IN ('published','publish_failed') OR (actor_tenant_id IS NOT NULL AND
   actor_user_id IS NOT NULL)``. Who *authorised* the external action is kept on the job
   (``publish_jobs.scheduled_by_tenant_id`` / ``scheduled_by_user_id``) — D-055 evidence.
5. ``publish_jobs`` — one job per scheduling decision; partial UNIQUE index guarantees a
   single active (``queued``/``leased``) job per item; ``revision_id`` pins the approved
   revision at schedule time.
6. ``publish_attempts`` — one row per Graph call, written **before** the call
   (``outcome NULL``) and updated after; ``UNIQUE(job_id, attempt_no)``.

downgrade()
-----------
Symmetric: drops the two tables, restores the 4-action CHECK and ``VARCHAR(16)`` (rows with
new actions are deleted first — they cannot exist in a 0005 world), restores NOT NULL on the
actor columns (system rows deleted first), drops ``published_at`` and restores the 6-state
status CHECK (``published``/``publish_failed`` rows are archived first so the CHECK holds).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = "'draft', 'pending_approval', 'approved', 'rejected', 'scheduled', 'archived'"
NEW_STATUSES = OLD_STATUSES + ", 'published', 'publish_failed'"
OLD_ACTIONS = "'submitted', 'approved', 'rejected', 'withdrawn'"
NEW_ACTIONS = OLD_ACTIONS + ", 'scheduled', 'published', 'publish_failed', 'publish_cancelled'"
SYSTEM_ACTIONS = "'published', 'publish_failed'"
JOB_STATUSES = "'queued', 'leased', 'succeeded', 'failed', 'cancelled'"
ATTEMPT_OUTCOMES = "'succeeded', 'retryable_failure', 'permanent_failure', 'ambiguous'"


def _ts(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    # 1 + 2 — content_items
    op.drop_constraint("ck_content_items_status", "content_items", type_="check")
    op.create_check_constraint(
        "ck_content_items_status", "content_items", f"status IN ({NEW_STATUSES})"
    )
    op.add_column("content_items", _ts("published_at", nullable=True))
    op.create_check_constraint(
        "ck_content_items_published_at_iff_published",
        "content_items",
        "(status = 'published') = (published_at IS NOT NULL)",
    )

    # 3 + 4 — approval_events
    op.drop_constraint("ck_approval_events_action", "approval_events", type_="check")
    op.alter_column(
        "approval_events",
        "action",
        existing_type=sa.String(16),
        type_=sa.String(32),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_approval_events_action", "approval_events", f"action IN ({NEW_ACTIONS})"
    )
    op.alter_column("approval_events", "actor_tenant_id", existing_nullable=False, nullable=True)
    op.alter_column("approval_events", "actor_user_id", existing_nullable=False, nullable=True)
    op.create_check_constraint(
        "ck_approval_events_actor_required",
        "approval_events",
        f"action IN ({SYSTEM_ACTIONS}) "
        "OR (actor_tenant_id IS NOT NULL AND actor_user_id IS NOT NULL)",
    )

    # 5 — publish_jobs
    op.create_table(
        "publish_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id", ondelete="CASCADE", name="fk_publish_jobs_item_id"),
            nullable=False,
        ),
        sa.Column(
            "revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "content_revisions.id", ondelete="RESTRICT", name="fk_publish_jobs_revision_id"
            ),
            nullable=False,
        ),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brands.id", ondelete="CASCADE", name="fk_publish_jobs_brand_id"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("target_page_id", sa.String(64), nullable=False),
        _ts("run_at"),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        _ts("leased_until", nullable=True),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        _ts("next_attempt_at", nullable=True),
        sa.Column("external_post_id", sa.String(128), nullable=True),
        sa.Column("external_permalink", sa.Text(), nullable=True),
        sa.Column("last_error_class", sa.String(32), nullable=True),
        # D-055: the human who authorised the external action (schedule / retry).
        sa.Column(
            "scheduled_by_tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "tenants.id", ondelete="RESTRICT", name="fk_publish_jobs_scheduled_by_tenant_id"
            ),
            nullable=False,
        ),
        sa.Column(
            "scheduled_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="RESTRICT", name="fk_publish_jobs_scheduled_by_user_id"
            ),
            nullable=False,
        ),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint(f"status IN ({JOB_STATUSES})", name="ck_publish_jobs_status"),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1", name="ck_publish_jobs_attempts"
        ),
        sa.CheckConstraint(
            "(status = 'leased') = (leased_until IS NOT NULL AND lease_owner IS NOT NULL)",
            name="ck_publish_jobs_lease_iff_leased",
        ),
    )
    op.create_index(
        "uq_publish_jobs_active_item",
        "publish_jobs",
        ["item_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'leased')"),
    )
    op.create_index("ix_publish_jobs_status_run_at", "publish_jobs", ["status", "run_at"])
    op.create_index(
        "ix_publish_jobs_status_next_attempt_at", "publish_jobs", ["status", "next_attempt_at"]
    )
    op.create_index("ix_publish_jobs_item_created", "publish_jobs", ["item_id", "created_at"])

    # 6 — publish_attempts
    op.create_table(
        "publish_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "publish_jobs.id", ondelete="CASCADE", name="fk_publish_attempts_job_id"
            ),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        _ts("started_at"),
        _ts("finished_at", nullable=True),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("error_class", sa.String(32), nullable=True),
        sa.Column("graph_error_code", sa.Integer(), nullable=True),
        sa.Column("graph_error_subcode", sa.Integer(), nullable=True),
        sa.Column("graph_trace_id", sa.String(64), nullable=True),
        sa.Column("external_post_id", sa.String(128), nullable=True),
        sa.UniqueConstraint("job_id", "attempt_no", name="uq_publish_attempts_job_no"),
        sa.CheckConstraint(
            f"outcome IS NULL OR outcome IN ({ATTEMPT_OUTCOMES})",
            name="ck_publish_attempts_outcome",
        ),
        sa.CheckConstraint("attempt_no >= 1", name="ck_publish_attempts_no"),
    )


def downgrade() -> None:
    op.drop_table("publish_attempts")
    op.drop_index("ix_publish_jobs_item_created", table_name="publish_jobs")
    op.drop_index("ix_publish_jobs_status_next_attempt_at", table_name="publish_jobs")
    op.drop_index("ix_publish_jobs_status_run_at", table_name="publish_jobs")
    op.drop_index("uq_publish_jobs_active_item", table_name="publish_jobs")
    op.drop_table("publish_jobs")

    # approval_events — rows that only exist in a 0006 world cannot survive the narrowing.
    op.execute(
        f"DELETE FROM approval_events WHERE action NOT IN ({OLD_ACTIONS})"  # noqa: S608 - constant literals
    )
    op.drop_constraint("ck_approval_events_actor_required", "approval_events", type_="check")
    op.alter_column("approval_events", "actor_user_id", existing_nullable=True, nullable=False)
    op.alter_column("approval_events", "actor_tenant_id", existing_nullable=True, nullable=False)
    op.drop_constraint("ck_approval_events_action", "approval_events", type_="check")
    op.alter_column(
        "approval_events",
        "action",
        existing_type=sa.String(32),
        type_=sa.String(16),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_approval_events_action", "approval_events", f"action IN ({OLD_ACTIONS})"
    )

    # content_items — published / publish_failed items fold into the final 'archived' state
    # (the external post, if any, is untouched; nothing is lost but the two new labels).
    # The ``published_at iff published`` CHECK must go *first*, otherwise archiving a
    # published row (``published_at`` set) would violate it (caught by B1-M1).
    op.drop_constraint(
        "ck_content_items_published_at_iff_published", "content_items", type_="check"
    )
    op.execute(
        f"UPDATE content_items SET status = 'archived' WHERE status NOT IN ({OLD_STATUSES})"  # noqa: S608 - constant literals
    )
    op.drop_column("content_items", "published_at")
    op.drop_constraint("ck_content_items_status", "content_items", type_="check")
    op.create_check_constraint(
        "ck_content_items_status", "content_items", f"status IN ({OLD_STATUSES})"
    )
