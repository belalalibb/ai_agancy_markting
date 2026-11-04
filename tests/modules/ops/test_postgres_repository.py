"""DB-less coverage of :class:`PgOpsRepository` (SPRINT-13 Part A) — statement shape and
row→domain mapping over the scripted :class:`StubSession`; the integration suite runs the
same code against PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from src.modules.ops.domain import REQUEUED_BY_ADMIN, AdminAction, DeadLetterJob
from src.modules.ops.exceptions import ItemNotPublishFailedError
from src.modules.ops.infrastructure.postgres_repository import (
    AdminActionRow,
    PgOpsRepository,
    PlatformFlagRow,
)

from tests.fakes import StubResult, StubSession, compiled

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
BRAND = uuid4()
JOB, ITEM, REV, ACTOR, TENANT = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()


def _job_row(status: str = "failed") -> tuple[object, ...]:
    return (JOB, ITEM, BRAND, "facebook_page", "transient", 3, 3, NOW, status, REV)


def _action_row(**over: object) -> AdminActionRow:
    base = {
        "id": uuid4(),
        "actor_user_id": ACTOR,
        "action": "flag_set",
        "target_kind": "platform_flag",
        "target_id": "platform_readonly",
        "before": None,
        "after": {"value": True},
        "at": NOW,
    }
    return AdminActionRow(**{**base, **over})


async def test_add_action_flushes_a_row_with_json_copies() -> None:
    session = StubSession()
    action = AdminAction(
        actor_user_id=None,
        action="retention_sweep",
        target_kind="platform",
        target_id="retention",
        before={},
        after={"deleted": 0},
        at=NOW,
    )
    assert await PgOpsRepository(session).add_action(action) is action
    (row,) = session.added
    assert isinstance(row, AdminActionRow)
    assert row.actor_user_id is None and row.after == {"deleted": 0} and row.at == NOW
    assert session.flush_count == 1


async def test_list_actions_filters_orders_and_caps_the_limit() -> None:
    rows = [_action_row(), _action_row(before={"value": False})]
    session = StubSession([StubResult(scalars=rows)])
    out = await PgOpsRepository(session).list_actions(
        target_kind="platform_flag", target_id="platform_readonly", limit=999
    )
    sql = compiled(session.executed[0][0])
    assert "admin_actions.target_kind = " in sql and "admin_actions.target_id = " in sql
    assert "ORDER BY admin_actions.at DESC, admin_actions.id DESC" in sql and "LIMIT" in sql
    assert [a.before for a in out] == [{}, {"value": False}]
    session = StubSession([StubResult(scalars=[])])
    assert (
        await PgOpsRepository(session).list_actions(target_kind=None, target_id=None, limit=0)
        == []
    )
    assert "WHERE" not in compiled(session.executed[0][0])


async def test_flags_round_trip_insert_then_update() -> None:
    session = StubSession(
        [StubResult(scalars=[PlatformFlagRow(key="feature_pulse", value=False, updated_at=NOW)])]
    )
    repo = PgOpsRepository(session)
    flags = await repo.get_flags()
    assert flags["feature_pulse"].value is False and flags["feature_pulse"].source == "db"
    created = await repo.set_flag("platform_readonly", True, now=NOW)  # no identity ⇒ insert
    assert created.value is True and isinstance(session.added[0], PlatformFlagRow)
    existing = PlatformFlagRow(key="platform_readonly", value=True, updated_at=NOW)
    session.identity[(PlatformFlagRow, "platform_readonly")] = existing
    updated = await repo.set_flag("platform_readonly", False, now=NOW)
    assert updated.value is False and existing.value is False and len(session.added) == 1


async def test_brand_suspension_read_and_write() -> None:
    session = StubSession([StubResult(rows=[(NOW,)]), StubResult(rows=[]), StubResult(rowcount=1)])
    repo = PgOpsRepository(session)
    state = await repo.brand_suspension(BRAND)
    assert state is not None and state.suspended_at == NOW
    assert await repo.brand_suspension(uuid4()) is None
    assert await repo.set_suspended(BRAND, None) is True
    assert session.executed[2][1] == {"brand_id": BRAND, "at": None}


async def test_dead_letter_and_job_status_map_rows() -> None:
    session = StubSession(
        [
            StubResult(rows=[_job_row()[:8]]),
            StubResult(rows=[_job_row("queued")]),
            StubResult(rows=[]),
        ]
    )
    repo = PgOpsRepository(session)
    (job,) = await repo.dead_letter(limit=5000)
    assert (
        isinstance(job, DeadLetterJob) and job.error_class == "transient" and job.failed_at == NOW
    )
    assert session.executed[0][1] == {"limit": 200}
    found = await repo.job_status(JOB)
    assert found is not None and found[1] == "queued" and found[2] == REV
    assert await repo.job_status(uuid4()) is None


def _job() -> DeadLetterJob:
    return DeadLetterJob(
        job_id=JOB,
        item_id=ITEM,
        brand_id=BRAND,
        channel="facebook_page",
        error_class=None,
        attempt_count=3,
        max_attempts=3,
        failed_at=NOW,
    )


@pytest.mark.parametrize("moved", [1, 0])
async def test_requeue_cas_item_and_d055_event(moved: int) -> None:
    session = StubSession(
        [
            StubResult(rowcount=moved),
            StubResult(rowcount=1),
            StubResult(rows=[(TENANT,)]),
            StubResult(rowcount=1),
        ]
    )
    ok = await PgOpsRepository(session).requeue(
        _job(), revision_id=REV, actor_user_id=ACTOR, now=NOW, grace=3
    )
    assert ok is bool(moved)
    job_sql, job_params = session.executed[0]
    text = str(job_sql)
    assert "attempt_count = 0" not in text  # F-1: never reset
    assert "max_attempts = attempt_count + :grace" in text and job_params["grace"] == 3
    if moved:
        assert len(session.executed) == 4
        params = session.executed[3][1]
        assert params["action"] == REQUEUED_BY_ADMIN == "requeued_by_admin"
        assert params["tenant_id"] == TENANT and params["revision_id"] == REV
        assert "status = 'publish_failed'" in str(session.executed[1][0])
    else:
        assert len(session.executed) == 1  # CAS lost ⇒ nothing else touched


async def test_requeue_item_cas_miss_raises_item_not_publish_failed() -> None:
    """F-3: the item CAS must hit exactly one row, else the whole requeue is rolled back."""
    session = StubSession([StubResult(rowcount=1), StubResult(rowcount=0)])
    with pytest.raises(ItemNotPublishFailedError):
        await PgOpsRepository(session).requeue(
            _job(), revision_id=REV, actor_user_id=ACTOR, now=NOW, grace=3
        )
    assert len(session.executed) == 2  # no D-055 event after a missed item CAS


async def test_item_has_active_job_and_admin_views() -> None:
    account = uuid4()
    session = StubSession(
        [
            StubResult(rows=[(1,)]),
            StubResult(rows=[]),
            StubResult(rows=[(BRAND, "Nile", "nile", NOW, TENANT)]),
            StubResult(rows=[]),
            StubResult(rows=[(account, "+20100", "active", "GREEN", "TIER_1K")]),
            StubResult(rows=[]),
        ]
    )
    repo = PgOpsRepository(session)
    assert await repo.item_has_active_job(ITEM) is True
    assert await repo.item_has_active_job(ITEM) is False
    assert "status IN ('queued', 'leased')" in str(session.executed[0][0])
    view = await repo.admin_brand(BRAND)
    assert view is not None and view.suspended is True and view.owner_tenant_id == TENANT
    assert await repo.admin_brand(BRAND) is None
    wa = await repo.admin_wa_summary(BRAND)
    assert wa is not None and wa.id == account and wa.quality_rating == "GREEN"
    assert "encrypted" not in str(session.executed[4][0]) and "hash" not in str(
        session.executed[4][0]
    )
    assert await repo.admin_wa_summary(BRAND) is None


async def test_admin_alerts_rows_to_dicts() -> None:
    alert_id = uuid4()
    session = StubSession(
        [
            StubResult(
                rows=[
                    (
                        alert_id,
                        BRAND,
                        "meta_token_expiring",
                        "critical",
                        {"reason": "refresh_failed"},
                        NOW,
                        None,
                    )
                ]
            )
        ]
    )
    (row,) = await PgOpsRepository(session).admin_alerts(kind="meta_token_expiring", limit=0)
    assert row == {
        "id": str(alert_id),
        "brand_id": str(BRAND),
        "kind": "meta_token_expiring",
        "severity": "critical",
        "payload": {"reason": "refresh_failed"},
        "created_at": NOW,
        "acknowledged_at": None,
    }
    assert session.executed[0][1] == {"kind": "meta_token_expiring", "limit": 1}
