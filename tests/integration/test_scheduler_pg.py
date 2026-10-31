"""SPRINT-07 B6 — ``PostgresScheduler`` against a real PostgreSQL (B6-I1..I5).

Every test seeds the SPRINT-03 world + one active connection (Page ``1001`` selected with an
encrypted token) + approved items scheduled directly through the repositories, then drives
``run_once(now)`` with a scripted ``FakePublisher`` (no Graph traffic).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from src.core.config import Settings
from src.core.database import build_engine, build_session_factory
from src.core.metrics import counters
from src.core.security import TokenEncryptionService
from src.modules.content.domain.models import ContentItem, ContentRevision, ContentStatus
from src.modules.content.infrastructure.postgres_repository import PostgresContentRepository
from src.modules.publishing.domain.models import (
    ErrorClass,
    PublishAttempt,
    PublishJob,
    PublishOutcome,
)
from src.modules.publishing.infrastructure.postgres_repository import PostgresPublishJobRepository
from src.modules.publishing.scheduler import PostgresScheduler
from src.modules.social_connect.domain.models import (
    AccountStatus,
    ConnectedAccount,
    InstagramAsset,
    PageAsset,
)
from src.modules.social_connect.infrastructure.postgres_repository import (
    PostgresConnectedAccountRepository,
)
from src.modules.social_connect.infrastructure.status_marker import PostgresStatusMarker
from tests.conftest import BRAND_ID, OWNER_ID, USER_BY_TENANT
from tests.fakes import FakePublisher
from tests.integration.conftest import seed_tenancy

pytestmark = pytest.mark.integration

PAGE_TOKEN = "EAAPageTokenForSchedulerLEAK"  # noqa: S105 - test fixture
SessionFactory = async_sessionmaker[AsyncSession]


# ------------------------------------------------------------------ helpers ---
async def _seed_connection(
    factory: SessionFactory, encryption: TokenEncryptionService, *, status=AccountStatus.ACTIVE
) -> UUID:  # noqa: ANN001
    async with factory() as session, session.begin():
        account = await PostgresConnectedAccountRepository(session).upsert(
            ConnectedAccount(
                brand_id=BRAND_ID,
                connected_by_tenant_id=OWNER_ID,
                provider_user_id="90001",
                encrypted_user_token=encryption.encrypt_token("user-token"),
                status=status,
                granted_scopes=["pages_manage_posts", "instagram_content_publish"],
                scopes_verified=True,
                pages=[
                    PageAsset(
                        page_id="1001",
                        page_name="Nile",
                        encrypted_page_token=encryption.encrypt_token(PAGE_TOKEN),
                        is_selected=True,
                        instagram=InstagramAsset(ig_user_id="178"),
                    )
                ],
            )
        )
        return account.id


async def _scheduled_item(
    factory: SessionFactory, *, run_at: datetime, channel: str = "facebook_page"
) -> tuple[ContentItem, PublishJob]:
    """An item already in ``scheduled`` with a ``queued`` job (what ``schedule`` leaves)."""
    now = datetime.now(tz=UTC)
    async with factory() as session, session.begin():
        content = PostgresContentRepository(session)
        item = ContentItem(
            brand_id=BRAND_ID,
            created_by_tenant_id=OWNER_ID,
            channel=channel,
            target_page_id="1001",
            status=ContentStatus.SCHEDULED,
            scheduled_for=run_at,
            created_at=now,
            updated_at=now,
        )
        revision = ContentRevision(
            item_id=item.id,
            revision_no=1,
            body="Scheduled body",
            media_refs=[{"url": "https://cdn.example.com/a.jpg", "kind": "image"}],
            provenance={"kind": "human"},
            created_by_tenant_id=OWNER_ID,
            created_at=now,
        )
        item = await content.create_item(item, revision)
        job = await PostgresPublishJobRepository(session).create_job(
            PublishJob(
                item_id=item.id,
                revision_id=revision.id,
                brand_id=BRAND_ID,
                channel=channel,
                target_page_id="1001",
                run_at=run_at,
                scheduled_by_tenant_id=OWNER_ID,
                scheduled_by_user_id=USER_BY_TENANT[OWNER_ID],
                created_at=now,
                updated_at=now,
            )
        )
    return item, job


async def _item_row(engine: AsyncEngine, item_id: UUID) -> tuple:
    async with engine.connect() as conn:
        return tuple(
            (
                await conn.execute(
                    text(
                        "SELECT status, scheduled_for, published_at FROM content_items "
                        "WHERE id = :id"
                    ),
                    {"id": item_id},
                )
            ).one()
        )


async def _job_row(engine: AsyncEngine, job_id: UUID) -> tuple:
    async with engine.connect() as conn:
        return tuple(
            (
                await conn.execute(
                    text(
                        "SELECT status, attempt_count, leased_until, lease_owner, "
                        "next_attempt_at, external_post_id, external_permalink, "
                        "last_error_class FROM publish_jobs WHERE id = :id"
                    ),
                    {"id": job_id},
                )
            ).one()
        )


async def _attempts(engine: AsyncEngine, job_id: UUID) -> list[tuple]:
    async with engine.connect() as conn:
        return [
            tuple(r)
            for r in await conn.execute(
                text(
                    "SELECT attempt_no, outcome, error_class, external_post_id, finished_at "
                    "FROM publish_attempts WHERE job_id = :id ORDER BY attempt_no"
                ),
                {"id": job_id},
            )
        ]


async def _events(engine: AsyncEngine, item_id: UUID) -> list[tuple]:
    async with engine.connect() as conn:
        return [
            tuple(r)
            for r in await conn.execute(
                text(
                    "SELECT action, actor_tenant_id FROM approval_events "
                    "WHERE item_id = :id ORDER BY created_at"
                ),
                {"id": item_id},
            )
        ]


def _scheduler(
    factory: SessionFactory,
    publisher: FakePublisher,
    settings: Settings,
    encryption: TokenEncryptionService,
    *,
    owner: str = "test-host:1",
) -> PostgresScheduler:
    return PostgresScheduler(
        lambda: factory,
        publisher,
        PostgresStatusMarker(lambda: factory),
        settings,
        encryption=encryption,
        lease_owner=owner,
    )


@pytest.fixture
async def world(session_factory: SessionFactory, encryption: TokenEncryptionService) -> UUID:
    """Seeded tenancy + one active connection; returns the connection id."""
    async with session_factory() as session:
        await seed_tenancy(session)
    counters.reset()
    return await _seed_connection(session_factory, encryption)


# -------------------------------------------------------------------- B6-I1 ---
async def test_b6_i1_due_job_published_once_with_attempt_status_and_event(
    engine: AsyncEngine,
    session_factory: SessionFactory,
    integration_settings: Settings,
    encryption: TokenEncryptionService,
    world: UUID,
) -> None:
    now = datetime.now(tz=UTC)
    item, job = await _scheduled_item(session_factory, run_at=now - timedelta(minutes=1))
    _, future = await _scheduled_item(session_factory, run_at=now + timedelta(hours=1))
    publisher = FakePublisher()
    scheduler = _scheduler(session_factory, publisher, integration_settings, encryption)

    assert await scheduler.run_once(now) == 1
    assert publisher.calls == {job.id: 1}  # the future job was not touched

    status_, attempts, leased_until, owner, _next, post_id, permalink, err = await _job_row(
        engine, job.id
    )
    assert status_ == "succeeded" and attempts == 1 and err is None
    assert leased_until is None and owner is None
    assert post_id == "fake-post" and permalink == "https://fb.example/fake"
    rows = await _attempts(engine, job.id)
    assert [a[:4] for a in rows] == [(1, "succeeded", None, "fake-post")]
    assert rows[0][4] is not None  # finished_at
    item_status, scheduled_for, published_at = await _item_row(engine, item.id)
    assert item_status == "published" and scheduled_for is None and published_at is not None
    assert await _events(engine, item.id) == [("published", None)]  # system event, no actor
    assert (await _job_row(engine, future.id))[0] == "queued"
    assert counters["publish_succeeded"] == 1 and counters["publish_failed"] == 0

    # Idempotent: a second cycle finds nothing due.
    assert await scheduler.run_once(now) == 0
    assert publisher.calls == {job.id: 1}


# -------------------------------------------------------------------- B6-I2 ---
async def test_b6_i2_retryable_failure_backs_off_then_permanent_failure_marks_item(
    engine: AsyncEngine,
    session_factory: SessionFactory,
    integration_settings: Settings,
    encryption: TokenEncryptionService,
    world: UUID,
) -> None:
    settings = integration_settings.model_copy(
        update={"publish_retry_backoff_seconds": (60, 300, 900)}
    )
    now = datetime.now(tz=UTC)
    item, job = await _scheduled_item(session_factory, run_at=now - timedelta(minutes=1))
    publisher = FakePublisher()
    publisher.outcomes[job.id] = PublishOutcome.failure(
        ErrorClass.RATE_LIMITED, graph_error_code=4, graph_trace_id="tr-1"
    )
    scheduler = _scheduler(session_factory, publisher, settings, encryption)

    # Attempt 1: retryable ⇒ back to queued with next_attempt_at = now + 60s.
    assert await scheduler.run_once(now) == 1
    status_, attempts, _lu, _lo, next_at, _p, _pl, err = await _job_row(engine, job.id)
    assert status_ == "queued" and attempts == 1 and err == "rate_limited"
    assert next_at is not None and timedelta(seconds=55) <= next_at - now <= timedelta(seconds=70)
    assert (await _item_row(engine, item.id))[0] == "scheduled"  # item untouched
    assert counters["publish_retried"] == 1

    # Not yet due for retry ⇒ nothing happens.
    assert await scheduler.run_once(now + timedelta(seconds=30)) == 0
    assert publisher.calls == {job.id: 1}

    # Attempt 2 (after the backoff): still rate limited ⇒ backoff[1] = 300s.
    t2 = now + timedelta(seconds=61)
    assert await scheduler.run_once(t2) == 1
    status_, attempts, _lu, _lo, next_at, _p, _pl, _e = await _job_row(engine, job.id)
    assert status_ == "queued" and attempts == 2
    assert timedelta(seconds=295) <= next_at - t2 <= timedelta(seconds=310)

    # Attempt 3 = max_attempts ⇒ failed + item publish_failed + system event.
    t3 = t2 + timedelta(seconds=301)
    publisher.outcomes[job.id] = PublishOutcome.failure(
        ErrorClass.PERMISSION_DENIED, graph_error_code=200
    )
    assert await scheduler.run_once(t3) == 1
    status_, attempts, lu, lo, _n, _p, _pl, err = await _job_row(engine, job.id)
    assert status_ == "failed" and attempts == 3 and err == "permission_denied"
    assert lu is None and lo is None
    rows = [a[:3] for a in await _attempts(engine, job.id)]
    assert rows == [
        (1, "retryable_failure", "rate_limited"),
        (2, "retryable_failure", "rate_limited"),
        (3, "permanent_failure", "permission_denied"),
    ]
    item_status, scheduled_for, published_at = await _item_row(engine, item.id)
    assert item_status == "publish_failed" and scheduled_for is None and published_at is None
    assert await _events(engine, item.id) == [("publish_failed", None)]
    assert counters["publish_failed"] == 1 and counters["publish_retried"] == 2
    # Nothing left to do.
    assert await scheduler.run_once(t3 + timedelta(hours=1)) == 0


async def test_b6_i2b_retryable_failure_at_max_attempts_is_final(
    engine: AsyncEngine,
    session_factory: SessionFactory,
    integration_settings: Settings,
    encryption: TokenEncryptionService,
    world: UUID,
) -> None:
    """A *retryable* class on the last allowed attempt still ends the job (no infinite loop)."""
    now = datetime.now(tz=UTC)
    item, job = await _scheduled_item(session_factory, run_at=now - timedelta(minutes=1))
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE publish_jobs SET max_attempts = 1 WHERE id = :id"), {"id": job.id}
        )
    publisher = FakePublisher()
    publisher.outcomes[job.id] = PublishOutcome.failure(ErrorClass.TRANSIENT)
    scheduler = _scheduler(session_factory, publisher, integration_settings, encryption)
    assert await scheduler.run_once(now) == 1
    assert (await _job_row(engine, job.id))[0] == "failed"
    assert (await _item_row(engine, item.id))[0] == "publish_failed"


# -------------------------------------------------------------------- B6-I3 ---
async def test_b6_i3_two_workers_two_engines_each_job_published_exactly_once(
    integration_settings: Settings,
    encryption: TokenEncryptionService,
    session_factory: SessionFactory,
    engine: AsyncEngine,
    world: UUID,
) -> None:
    """Two schedulers on two independent engines/pools start a cycle at the same instant
    (``asyncio.Barrier``) over 5 due jobs. ``FOR UPDATE SKIP LOCKED`` partitions the batch —
    the union is all 5, no job is published twice, every item ends ``published``."""
    now = datetime.now(tz=UTC)
    jobs = [
        (await _scheduled_item(session_factory, run_at=now - timedelta(minutes=i + 1)))[1]
        for i in range(5)
    ]
    settings = integration_settings.model_copy(update={"scheduler_batch_size": 3})
    engines = [build_engine(settings), build_engine(settings)]
    start = asyncio.Barrier(2)
    publishers = [FakePublisher(barrier=None), FakePublisher(barrier=None)]
    try:
        schedulers = [
            _scheduler(build_session_factory(eng), pub, settings, encryption, owner=f"worker:{i}")
            for i, (eng, pub) in enumerate(zip(engines, publishers, strict=True))
        ]

        async def worker(sched: PostgresScheduler) -> int:
            await start.wait()  # both leases race at the same instant
            return await sched.run_once(now)

        counts = await asyncio.gather(*(worker(s) for s in schedulers))
    finally:
        for eng in engines:
            await eng.dispose()

    assert sum(counts) == 5 and all(c <= 3 for c in counts)
    seen: dict[UUID, int] = {}
    for pub in publishers:
        for job_id, n in pub.calls.items():
            seen[job_id] = seen.get(job_id, 0) + n
    assert seen == {job.id: 1 for job in jobs}  # each job exactly once, across both workers
    for job in jobs:
        status_, attempts, *_rest = await _job_row(engine, job.id)
        assert status_ == "succeeded" and attempts == 1
        assert [a[:2] for a in await _attempts(engine, job.id)] == [(1, "succeeded")]
    async with engine.connect() as conn:
        published = (
            await conn.execute(
                text("SELECT COUNT(*) FROM content_items WHERE status = 'published'")
            )
        ).scalar_one()
        owners = {
            r[0]
            for r in await conn.execute(
                text("SELECT DISTINCT lease_owner FROM publish_jobs WHERE lease_owner IS NOT NULL")
            )
        }
    assert published == 5
    assert owners == set()  # leases cleared on completion
    assert counters["publish_succeeded"] == 5


# -------------------------------------------------------------------- B6-I4 ---
async def test_b6_i4_expired_lease_with_open_attempt_is_ambiguous_without_publisher_call(
    engine: AsyncEngine,
    session_factory: SessionFactory,
    integration_settings: Settings,
    encryption: TokenEncryptionService,
    world: UUID,
) -> None:
    now = datetime.now(tz=UTC)
    item, job = await _scheduled_item(session_factory, run_at=now - timedelta(minutes=10))
    _, requeue_job = await _scheduled_item(session_factory, run_at=now - timedelta(minutes=9))
    # A previous worker leased both, opened an attempt on the first (died mid-Graph-call) and
    # never got to the second (no attempt row) — both leases are now expired.
    async with session_factory() as session, session.begin():
        repo = PostgresPublishJobRepository(session)
        leased = await repo.lease_due_jobs(
            now=now - timedelta(minutes=5),
            lease_until=now - timedelta(minutes=1),
            owner="dead-worker:9",
            batch=10,
        )
        assert {j.id for j in leased} == {job.id, requeue_job.id}
        await repo.start_attempt(
            PublishAttempt(job_id=job.id, attempt_no=1, started_at=now - timedelta(minutes=5))
        )
    publisher = FakePublisher()
    scheduler = _scheduler(session_factory, publisher, integration_settings, encryption)

    processed = await scheduler.run_once(now)

    # Recovery: the open-attempt job is ambiguous — NO publisher call for it.
    assert job.id not in publisher.calls
    status_, attempts, lu, lo, _n, _p, _pl, err = await _job_row(engine, job.id)
    assert status_ == "failed" and attempts == 1 and err == "ambiguous_outcome"
    assert lu is None and lo is None
    rows = await _attempts(engine, job.id)
    assert [a[:3] for a in rows] == [(1, "ambiguous", "ambiguous_outcome")]
    assert rows[0][4] is not None  # finished_at set
    item_status, scheduled_for, _pub = await _item_row(engine, item.id)
    assert item_status == "publish_failed" and scheduled_for is None
    assert await _events(engine, item.id) == [("publish_failed", None)]
    assert counters["publish_failed"] == 1

    # The attempt-less expired lease was requeued (lost attempt still counts) and then
    # processed in the same cycle by this worker.
    assert processed == 1 and publisher.calls == {requeue_job.id: 1}
    r_status, r_attempts, *_r = await _job_row(engine, requeue_job.id)
    assert r_status == "succeeded" and r_attempts == 2
    assert [a[:2] for a in await _attempts(engine, requeue_job.id)] == [(2, "succeeded")]


# -------------------------------------------------------------------- B6-I5 ---
async def test_b6_i5_token_invalid_marks_connection_reauth_required_out_of_band(
    engine: AsyncEngine,
    session_factory: SessionFactory,
    integration_settings: Settings,
    encryption: TokenEncryptionService,
    world: UUID,
) -> None:
    connection_id = world
    now = datetime.now(tz=UTC)
    item, job = await _scheduled_item(session_factory, run_at=now - timedelta(minutes=1))
    publisher = FakePublisher()
    publisher.outcomes[job.id] = PublishOutcome.failure(
        ErrorClass.TOKEN_INVALID,
        graph_error_code=190,
        graph_error_subcode=463,
        connection_id_to_mark=connection_id,
    )
    scheduler = _scheduler(session_factory, publisher, integration_settings, encryption)

    assert await scheduler.run_once(now) == 1

    # token_invalid is permanent: job failed on attempt 1 of 3, item publish_failed.
    status_, attempts, *_rest, err = await _job_row(engine, job.id)
    assert status_ == "failed" and attempts == 1 and err == "token_invalid"
    assert (await _item_row(engine, item.id))[0] == "publish_failed"
    assert [a[:3] for a in await _attempts(engine, job.id)] == [
        (1, "permanent_failure", "token_invalid")
    ]
    # …and the connection was flipped by the StatusMarker in its own committed transaction.
    async with engine.connect() as conn:
        status = (
            await conn.execute(
                text("SELECT status FROM social_connections WHERE id = :id"),
                {"id": connection_id},
            )
        ).scalar_one()
    assert status == "reauth_required"
    # A follow-up job for the same Page now fails fast as connection_unavailable (B5), proven
    # through the real MetaPublisher token provider path: resolve() returns None.
    from src.modules.publishing.infrastructure.meta_publisher import PostgresPageTokenProvider

    async with session_factory() as session:
        provider = PostgresPageTokenProvider(
            PostgresConnectedAccountRepository(session), encryption
        )
        assert await provider.resolve(BRAND_ID, "1001") is None


# --------------------------------------------------------- B6 extra: IG retry path ---
async def test_b6_instagram_retry_passes_last_creation_id_to_publisher(
    engine: AsyncEngine,
    session_factory: SessionFactory,
    integration_settings: Settings,
    encryption: TokenEncryptionService,
    world: UUID,
) -> None:
    """The container id saved on a failed IG attempt reaches the publisher on the retry (B4)."""
    settings = integration_settings.model_copy(update={"publish_retry_backoff_seconds": (1,)})
    now = datetime.now(tz=UTC)
    item, job = await _scheduled_item(
        session_factory, run_at=now - timedelta(minutes=1), channel="instagram"
    )
    publisher = FakePublisher()
    publisher.outcomes[job.id] = PublishOutcome.failure(
        ErrorClass.TRANSIENT, external_post_id="creation-77"
    )
    scheduler = _scheduler(session_factory, publisher, settings, encryption)
    assert await scheduler.run_once(now) == 1
    assert publisher.seen_creation_ids == [None]
    assert (await _attempts(engine, job.id))[0][3] == "creation-77"

    del publisher.outcomes[job.id]  # next attempt succeeds
    assert await scheduler.run_once(now + timedelta(seconds=2)) == 1
    assert publisher.seen_creation_ids == [None, "creation-77"]
    assert (await _job_row(engine, job.id))[0] == "succeeded"
    assert (await _item_row(engine, item.id))[0] == "published"
