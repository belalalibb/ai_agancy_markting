"""SPRINT-07 B6 — ``PostgresScheduler`` cycle logic against the in-memory fakes (no database).

The PG integration suite (``tests/integration/test_scheduler_pg.py``) proves the locking and
transaction semantics; this file proves the *decision* logic of ``run_once`` (recovery, lease,
success / retry / permanent failure, out-of-band marking, loop error handling) so the CI
``unit`` job (``-m "not integration"``) covers the scheduler too.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from src.core.config import Settings
from src.core.metrics import counters
from src.core.security import TokenEncryptionService
from src.modules.content.domain.models import ContentItem, ContentRevision, ContentStatus
from src.modules.publishing.domain.models import (
    AttemptOutcome,
    ErrorClass,
    JobStatus,
    PublishAttempt,
    PublishJob,
    PublishOutcome,
)
from src.modules.publishing.scheduler import (
    PostgresScheduler,
    default_lease_owner,
    scheduler_loop,
)
from src.modules.social_connect.domain.models import (
    AccountStatus,
    ConnectedAccount,
    InstagramAsset,
    PageAsset,
)

from tests.conftest import BRAND_ID, OWNER_ID
from tests.fakes import (
    FakeConnectedAccountRepository,
    FakeContentRepository,
    FakePublisher,
    FakePublishJobRepository,
    FakeStatusMarker,
    FakeStore,
)

PAGE_TOKEN = "EAAUnitSchedulerTokenLEAK"  # noqa: S105 - test fixture


class _NullSession:
    """The fakes ignore the session; this stands in for ``async_sessionmaker()()``."""

    async def __aenter__(self) -> _NullSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    @contextlib.asynccontextmanager
    async def begin(self):  # noqa: ANN201
        yield self


class World:
    """One in-memory world: fakes + a scheduler wired to them."""

    def __init__(self, settings: Settings, encryption: TokenEncryptionService) -> None:
        self.store = FakeStore()
        self.connections = FakeConnectedAccountRepository(self.store)
        self.content = FakeContentRepository()
        self.jobs = FakePublishJobRepository()
        self.marker = FakeStatusMarker(self.store)
        self.publisher = FakePublisher()
        self.settings = settings
        self.connection_id = uuid4()
        self.connections.rows[self.connection_id] = ConnectedAccount(
            id=self.connection_id,
            brand_id=BRAND_ID,
            connected_by_tenant_id=OWNER_ID,
            provider_user_id="90001",
            encrypted_user_token=encryption.encrypt_token("user"),
            status=AccountStatus.ACTIVE,
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
        self.scheduler = PostgresScheduler(
            lambda: _NullSession,  # type: ignore[arg-type,return-value]
            self.publisher,
            self.marker,
            settings,
            encryption=encryption,
            lease_owner="unit:1",
            jobs_repo=lambda _s: self.jobs,
            content_repo=lambda _s: self.content,
            connections_repo=lambda _s: self.connections,
        )

    async def scheduled(
        self, *, run_at: datetime, channel: str = "facebook_page"
    ) -> tuple[ContentItem, PublishJob]:
        now = datetime.now(tz=UTC)
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
            body="unit body",
            media_refs=[{"url": "https://cdn.example.com/a.jpg", "kind": "image"}],
            provenance={"kind": "human"},
            created_by_tenant_id=OWNER_ID,
            created_at=now,
        )
        item = await self.content.create_item(item, revision)
        job = await self.jobs.create_job(
            PublishJob(
                item_id=item.id,
                revision_id=revision.id,
                brand_id=BRAND_ID,
                channel=channel,
                target_page_id="1001",
                run_at=run_at,
                scheduled_by_tenant_id=OWNER_ID,
                scheduled_by_user_id=uuid4(),
                created_at=now,
                updated_at=now,
            )
        )
        return item, job

    def job(self, job_id: UUID) -> PublishJob:
        return self.jobs.jobs[job_id]

    def item(self, item_id: UUID) -> ContentItem:
        return self.content.items[item_id]

    async def attempts(self, job_id: UUID) -> list[PublishAttempt]:
        return await self.jobs.list_attempts(job_id)

    def events(self, item_id: UUID) -> list[tuple[str, UUID | None]]:
        return [(e.action.value, e.actor_tenant_id) for e in self.content.events[item_id]]


@pytest.fixture
def world(settings: Settings, encryption: TokenEncryptionService) -> World:
    counters.reset()
    return World(settings, encryption)


NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
PAST = NOW - timedelta(minutes=1)


# ------------------------------------------------------------------ success ---
async def test_due_job_succeeds_and_publishes_item(world: World) -> None:
    item, job = await world.scheduled(run_at=PAST)
    _, future = await world.scheduled(run_at=NOW + timedelta(hours=1))

    assert await world.scheduler.run_once(NOW) == 1
    assert world.publisher.calls == {job.id: 1}
    j = world.job(job.id)
    assert j.status is JobStatus.SUCCEEDED and j.attempt_count == 1
    assert j.leased_until is None and j.lease_owner is None
    assert j.external_post_id == "fake-post" and j.external_permalink is not None
    attempts = await world.attempts(job.id)
    assert [(a.attempt_no, a.outcome) for a in attempts] == [(1, AttemptOutcome.SUCCEEDED)]
    i = world.item(item.id)
    assert i.status is ContentStatus.PUBLISHED and i.published_at is not None
    assert i.scheduled_for is None
    assert world.events(item.id) == [("published", None)]
    assert world.job(future.id).status is JobStatus.QUEUED
    assert counters["publish_succeeded"] == 1
    assert await world.scheduler.run_once(NOW) == 0  # idempotent


async def test_token_provider_resolves_through_injected_connections_repo(world: World) -> None:
    provider = world.scheduler._page_token_provider()
    target = await provider.resolve(BRAND_ID, "1001")
    assert target is not None and target.page_token == PAGE_TOKEN
    assert target.connection_id == world.connection_id and target.ig_user_id == "178"
    assert await provider.resolve(BRAND_ID, "9999") is None


async def test_token_provider_without_encryption_returns_none(world: World) -> None:
    world.scheduler._encryption = None
    assert await world.scheduler._page_token_provider().resolve(BRAND_ID, "1001") is None


# ------------------------------------------------------------- retry / fail ---
async def test_retryable_failure_backs_off_on_cycle_clock_then_fails_at_max(
    world: World,
) -> None:
    item, job = await world.scheduled(run_at=PAST)
    world.publisher.outcomes[job.id] = PublishOutcome.failure(
        ErrorClass.RATE_LIMITED, graph_error_code=4
    )
    assert await world.scheduler.run_once(NOW) == 1
    j = world.job(job.id)
    assert j.status is JobStatus.QUEUED and j.attempt_count == 1
    assert j.next_attempt_at == NOW + timedelta(seconds=60)
    assert j.last_error_class is ErrorClass.RATE_LIMITED
    assert world.item(item.id).status is ContentStatus.SCHEDULED
    assert counters["publish_retried"] == 1

    assert await world.scheduler.run_once(NOW + timedelta(seconds=30)) == 0  # not due yet
    t2 = NOW + timedelta(seconds=61)
    assert await world.scheduler.run_once(t2) == 1
    assert world.job(job.id).next_attempt_at == t2 + timedelta(seconds=300)

    t3 = t2 + timedelta(seconds=301)
    world.publisher.outcomes[job.id] = PublishOutcome.failure(ErrorClass.TRANSIENT)
    assert await world.scheduler.run_once(t3) == 1
    j = world.job(job.id)
    assert j.status is JobStatus.FAILED and j.attempt_count == 3
    assert j.last_error_class is ErrorClass.TRANSIENT
    assert [a.outcome for a in await world.attempts(job.id)] == [
        AttemptOutcome.RETRYABLE_FAILURE,
        AttemptOutcome.RETRYABLE_FAILURE,
        AttemptOutcome.RETRYABLE_FAILURE,
    ]
    i = world.item(item.id)
    assert i.status is ContentStatus.PUBLISH_FAILED and i.published_at is None
    assert world.events(item.id) == [("publish_failed", None)]
    assert counters["publish_failed"] == 1 and counters["publish_retried"] == 2


async def test_backoff_index_is_clamped_to_schedule_length(world: World) -> None:
    world.scheduler._settings = world.settings.model_copy(
        update={"publish_retry_backoff_seconds": (7,)}
    )
    _, job = await world.scheduled(run_at=PAST)
    world.jobs.jobs[job.id] = job.model_copy(update={"max_attempts": 5})
    world.publisher.outcomes[job.id] = PublishOutcome.failure(ErrorClass.TRANSIENT)
    t = NOW
    for _ in range(3):
        assert await world.scheduler.run_once(t) == 1
        assert world.job(job.id).next_attempt_at == t + timedelta(seconds=7)
        t += timedelta(seconds=8)


async def test_permanent_failure_marks_item_and_records_graph_codes(world: World) -> None:
    item, job = await world.scheduled(run_at=PAST)
    world.publisher.outcomes[job.id] = PublishOutcome.failure(
        ErrorClass.PERMISSION_DENIED,
        graph_error_code=200,
        graph_error_subcode=1,
        graph_trace_id="tr",
    )
    assert await world.scheduler.run_once(NOW) == 1
    j = world.job(job.id)
    assert j.status is JobStatus.FAILED and j.last_error_class is ErrorClass.PERMISSION_DENIED
    a = (await world.attempts(job.id))[0]
    assert a.outcome is AttemptOutcome.PERMANENT_FAILURE
    assert (a.graph_error_code, a.graph_error_subcode, a.graph_trace_id) == (200, 1, "tr")
    assert world.item(item.id).status is ContentStatus.PUBLISH_FAILED
    assert world.marker.calls == []


async def test_token_invalid_marks_connection_out_of_band(world: World) -> None:
    _, job = await world.scheduled(run_at=PAST)
    world.publisher.outcomes[job.id] = PublishOutcome.failure(
        ErrorClass.TOKEN_INVALID, graph_error_code=190, connection_id_to_mark=world.connection_id
    )
    assert await world.scheduler.run_once(NOW) == 1
    assert world.job(job.id).status is JobStatus.FAILED
    assert world.marker.calls == [(BRAND_ID, world.connection_id, AccountStatus.REAUTH_REQUIRED)]
    assert world.connections.rows[world.connection_id].status is AccountStatus.REAUTH_REQUIRED
    assert await world.scheduler._page_token_provider().resolve(BRAND_ID, "1001") is None


async def test_missing_revision_is_invalid_request_without_publisher_call(world: World) -> None:
    item, job = await world.scheduled(run_at=PAST)
    world.content.revisions[item.id] = []  # revision vanished behind the job's back
    assert await world.scheduler.run_once(NOW) == 1
    assert world.publisher.calls == {}
    j = world.job(job.id)
    assert j.status is JobStatus.FAILED and j.last_error_class is ErrorClass.INVALID_REQUEST
    assert world.item(item.id).status is ContentStatus.PUBLISH_FAILED


async def test_instagram_retry_passes_last_creation_id(world: World) -> None:
    world.scheduler._settings = world.settings.model_copy(
        update={"publish_retry_backoff_seconds": (1,)}
    )
    _, job = await world.scheduled(run_at=PAST, channel="instagram")
    world.publisher.outcomes[job.id] = PublishOutcome.failure(
        ErrorClass.TRANSIENT, external_post_id="creation-5"
    )
    assert await world.scheduler.run_once(NOW) == 1
    del world.publisher.outcomes[job.id]
    assert await world.scheduler.run_once(NOW + timedelta(seconds=2)) == 1
    assert world.publisher.seen_creation_ids == [None, "creation-5"]
    assert world.job(job.id).status is JobStatus.SUCCEEDED


# ----------------------------------------------------------------- recovery ---
async def test_expired_lease_with_open_attempt_is_ambiguous_and_never_republished(
    world: World,
) -> None:
    item, job = await world.scheduled(run_at=NOW - timedelta(minutes=10))
    _, lost = await world.scheduled(run_at=NOW - timedelta(minutes=9))
    leased = await world.jobs.lease_due_jobs(
        now=NOW - timedelta(minutes=5),
        lease_until=NOW - timedelta(minutes=1),
        owner="dead:1",
        batch=10,
    )
    assert {j.id for j in leased} == {job.id, lost.id}
    await world.jobs.start_attempt(
        PublishAttempt(job_id=job.id, attempt_no=1, started_at=NOW - timedelta(minutes=5))
    )

    processed = await world.scheduler.run_once(NOW)

    assert job.id not in world.publisher.calls
    j = world.job(job.id)
    assert j.status is JobStatus.FAILED and j.last_error_class is ErrorClass.AMBIGUOUS_OUTCOME
    a = (await world.attempts(job.id))[0]
    assert a.outcome is AttemptOutcome.AMBIGUOUS and a.finished_at == NOW
    assert world.item(item.id).status is ContentStatus.PUBLISH_FAILED
    assert world.events(item.id) == [("publish_failed", None)]
    # The attempt-less lease was requeued and then processed in this very cycle.
    assert processed == 1 and world.publisher.calls == {lost.id: 1}
    r = world.job(lost.id)
    assert r.status is JobStatus.SUCCEEDED and r.attempt_count == 2
    assert [a.attempt_no for a in await world.attempts(lost.id)] == [2]
    assert counters["publish_failed"] == 1 and counters["publish_succeeded"] == 1


async def test_active_lease_is_left_alone(world: World) -> None:
    _, job = await world.scheduled(run_at=PAST)
    await world.jobs.lease_due_jobs(
        now=NOW, lease_until=NOW + timedelta(minutes=2), owner="other:7", batch=10
    )
    assert await world.scheduler.run_once(NOW) == 0
    assert world.publisher.calls == {}
    assert world.job(job.id).status is JobStatus.LEASED
    assert world.job(job.id).lease_owner == "other:7"


# ---------------------------------------------------------------- batching ---
async def test_batch_size_limits_one_cycle(world: World) -> None:
    world.scheduler._settings = world.settings.model_copy(update={"scheduler_batch_size": 2})
    jobs = [(await world.scheduled(run_at=NOW - timedelta(minutes=i + 1)))[1] for i in range(3)]
    assert await world.scheduler.run_once(NOW) == 2
    assert await world.scheduler.run_once(NOW) == 1
    assert all(world.job(j.id).status is JobStatus.SUCCEEDED for j in jobs)
    assert sum(world.publisher.calls.values()) == 3


# ------------------------------------------------------------------- loop ---
def test_default_lease_owner_is_host_and_pid() -> None:
    owner = default_lease_owner()
    assert ":" in owner and len(owner) <= 64
    assert owner.rsplit(":", 1)[1].isdigit()


async def test_scheduler_loop_logs_and_counts_cycle_errors_then_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    counters.reset()
    calls = 0

    class Boom:
        async def run_once(self, now: datetime) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("LEAK-DETAIL")
            return 0

    with caplog.at_level(logging.ERROR, logger="src.modules.publishing.scheduler"):
        task = asyncio.create_task(scheduler_loop(Boom(), 0.01))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if calls >= 2:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert calls >= 2  # the loop survived the failing cycle
    assert counters["scheduler_cycle_errors"] == 1
    assert "scheduler cycle failed" in caplog.text
