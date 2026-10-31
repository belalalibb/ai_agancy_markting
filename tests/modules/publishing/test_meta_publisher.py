"""SPRINT-07 B5 — ``MetaPublisher`` + ``PostgresPageTokenProvider`` against fakes (no DB).

The publisher never raises for provider failures, returns the IG ``creation_id`` after a step-2
failure so a retry runs ``media_publish`` only, and asks for ``reauth_required`` on
``token_invalid`` through ``connection_id_to_mark`` (B6-I5 contract).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from src.core.security import TokenEncryptionService
from src.modules.content.domain.models import Channel, ContentRevision
from src.modules.publishing.domain.models import AttemptOutcome, ErrorClass, PublishJob
from src.modules.publishing.domain.publisher import ResolvedPageTarget
from src.modules.publishing.infrastructure.meta_publisher import (
    MetaPublisher,
    PostgresPageTokenProvider,
)
from src.modules.social_connect.domain.models import (
    AccountStatus,
    ConnectedAccount,
    InstagramAsset,
    PageAsset,
)
from src.modules.social_connect.meta_client import MetaGraphClient

from tests.conftest import BRAND_ID, OWNER_ID
from tests.fakes import FakeConnectedAccountRepository, FakeStore

PAGE_TOKEN = "EAAPublisherPageTokenLEAK"  # noqa: S105 - test fixture
CONNECTION_ID = uuid4()
IMAGE_URL = "https://cdn.example.com/media/offer.jpg"


class _Provider:
    def __init__(self, target: ResolvedPageTarget | None) -> None:
        self.target = target
        self.calls: list[tuple[UUID, str]] = []

    async def resolve(self, brand_id: UUID, page_id: str) -> ResolvedPageTarget | None:
        self.calls.append((brand_id, page_id))
        return self.target


def _target(*, ig: bool = False) -> ResolvedPageTarget:
    return ResolvedPageTarget(
        connection_id=CONNECTION_ID,
        page_id="1001",
        ig_user_id="178" if ig else None,
        page_token=PAGE_TOKEN,
    )


def _job(channel: Channel = Channel.FACEBOOK_PAGE) -> PublishJob:
    now = datetime.now(tz=UTC)
    return PublishJob(
        item_id=uuid4(),
        revision_id=uuid4(),
        brand_id=BRAND_ID,
        channel=channel.value,
        target_page_id="1001",
        run_at=now,
        scheduled_by_tenant_id=OWNER_ID,
        scheduled_by_user_id=uuid4(),
    )


def _revision(job: PublishJob, *, media: bool = False) -> ContentRevision:
    return ContentRevision(
        id=job.revision_id,
        item_id=job.item_id,
        revision_no=1,
        body="Hello from the publisher",
        media_refs=[{"url": IMAGE_URL, "kind": "image"}] if media else [],
        provenance={"kind": "human"},
        created_by_tenant_id=OWNER_ID,
    )


def _graph_error(code: int, *, http_status: int = 400) -> httpx.Response:
    return httpx.Response(
        http_status,
        json={"error": {"message": "LEAK-MESSAGE", "code": code, "type": "OAuthException"}},
    )


@pytest.fixture
def publisher(meta_client: MetaGraphClient) -> MetaPublisher:
    return MetaPublisher(meta_client)


# ---------------------------------------------------------------- facebook ---
async def test_facebook_success(publisher: MetaPublisher, graph_mock: respx.MockRouter) -> None:
    graph_mock.post("/1001/feed").mock(return_value=httpx.Response(200, json={"id": "1001_7"}))
    graph_mock.get("/1001_7").mock(
        return_value=httpx.Response(200, json={"permalink_url": "https://fb.com/7"})
    )
    job = _job()
    provider = _Provider(_target())
    outcome = await publisher.publish(job, _revision(job), provider)
    assert outcome.kind is AttemptOutcome.SUCCEEDED
    assert outcome.external_post_id == "1001_7" and outcome.permalink == "https://fb.com/7"
    assert outcome.connection_id_to_mark is None
    assert provider.calls == [(BRAND_ID, "1001")]


async def test_connection_unavailable_makes_no_graph_call(
    publisher: MetaPublisher, graph_mock: respx.MockRouter
) -> None:
    route = graph_mock.post("/1001/feed")
    job = _job()
    outcome = await publisher.publish(job, _revision(job), _Provider(None))
    assert outcome.kind is AttemptOutcome.PERMANENT_FAILURE
    assert outcome.error_class is ErrorClass.CONNECTION_UNAVAILABLE
    assert route.call_count == 0


@pytest.mark.parametrize(
    ("code", "error_class", "kind", "marks"),
    [
        (190, ErrorClass.TOKEN_INVALID, AttemptOutcome.PERMANENT_FAILURE, True),
        (200, ErrorClass.PERMISSION_DENIED, AttemptOutcome.PERMANENT_FAILURE, False),
        (4, ErrorClass.RATE_LIMITED, AttemptOutcome.RETRYABLE_FAILURE, False),
        (2, ErrorClass.TRANSIENT, AttemptOutcome.RETRYABLE_FAILURE, False),
        (100, ErrorClass.INVALID_REQUEST, AttemptOutcome.PERMANENT_FAILURE, False),
        (9999, ErrorClass.UNKNOWN, AttemptOutcome.PERMANENT_FAILURE, False),
    ],
)
async def test_graph_failures_are_classified_not_raised(
    publisher: MetaPublisher,
    graph_mock: respx.MockRouter,
    code: int,
    error_class: ErrorClass,
    kind: AttemptOutcome,
    marks: bool,
) -> None:
    graph_mock.post("/1001/feed").mock(return_value=_graph_error(code))
    job = _job()
    outcome = await publisher.publish(job, _revision(job), _Provider(_target()))
    assert outcome.kind is kind and outcome.error_class is error_class
    assert outcome.graph_error_code == code
    assert outcome.connection_id_to_mark == (CONNECTION_ID if marks else None)
    assert "LEAK" not in outcome.model_dump_json()


async def test_network_failure_is_transient(
    publisher: MetaPublisher, graph_mock: respx.MockRouter
) -> None:
    graph_mock.post("/1001/feed").mock(side_effect=httpx.ConnectError("boom"))
    job = _job()
    outcome = await publisher.publish(job, _revision(job), _Provider(_target()))
    assert outcome.kind is AttemptOutcome.RETRYABLE_FAILURE
    assert outcome.error_class is ErrorClass.TRANSIENT


# --------------------------------------------------------------- instagram ---
async def test_instagram_two_step_success(
    publisher: MetaPublisher, graph_mock: respx.MockRouter
) -> None:
    media = graph_mock.post("/178/media").mock(
        return_value=httpx.Response(200, json={"id": "c-1"})
    )
    graph_mock.post("/178/media_publish").mock(
        return_value=httpx.Response(200, json={"id": "m-1"})
    )
    graph_mock.get("/m-1").mock(
        return_value=httpx.Response(200, json={"permalink": "https://instagram.com/p/x/"})
    )
    job = _job(Channel.INSTAGRAM)
    outcome = await publisher.publish(job, _revision(job, media=True), _Provider(_target(ig=True)))
    assert outcome.kind is AttemptOutcome.SUCCEEDED
    assert outcome.external_post_id == "m-1"
    assert outcome.permalink == "https://instagram.com/p/x/"
    assert media.call_count == 1


async def test_instagram_step2_failure_keeps_creation_id_and_retry_skips_step1(
    publisher: MetaPublisher, graph_mock: respx.MockRouter
) -> None:
    media = graph_mock.post("/178/media").mock(
        return_value=httpx.Response(200, json={"id": "c-9"})
    )
    publish = graph_mock.post("/178/media_publish").mock(return_value=_graph_error(2))
    job = _job(Channel.INSTAGRAM)
    revision = _revision(job, media=True)
    first = await publisher.publish(job, revision, _Provider(_target(ig=True)))
    assert first.kind is AttemptOutcome.RETRYABLE_FAILURE
    assert first.external_post_id == "c-9"  # the container survives for the retry

    publish.mock(return_value=httpx.Response(200, json={"id": "m-2"}))
    graph_mock.get("/m-2").mock(return_value=httpx.Response(200, json={"permalink": None}))
    second = await publisher.publish(
        job, revision, _Provider(_target(ig=True)), last_creation_id="c-9"
    )
    assert second.kind is AttemptOutcome.SUCCEEDED and second.external_post_id == "m-2"
    assert media.call_count == 1  # step 1 was NOT repeated
    assert publish.call_count == 2


async def test_instagram_without_linked_account_or_image_is_permanent(
    publisher: MetaPublisher, graph_mock: respx.MockRouter
) -> None:
    job = _job(Channel.INSTAGRAM)
    no_ig = await publisher.publish(job, _revision(job, media=True), _Provider(_target()))
    assert no_ig.error_class is ErrorClass.CONNECTION_UNAVAILABLE
    no_image = await publisher.publish(job, _revision(job), _Provider(_target(ig=True)))
    assert no_image.error_class is ErrorClass.INVALID_REQUEST
    assert no_image.kind is AttemptOutcome.PERMANENT_FAILURE


# ----------------------------------------------------- PostgresPageTokenProvider ---
def _account(encryption: TokenEncryptionService, *, status: AccountStatus) -> ConnectedAccount:
    return ConnectedAccount(
        id=CONNECTION_ID,
        brand_id=BRAND_ID,
        connected_by_tenant_id=OWNER_ID,
        provider_user_id="90001",
        encrypted_user_token=encryption.encrypt_token("user-token"),
        status=status,
        granted_scopes=["pages_manage_posts"],
        scopes_verified=True,
        pages=[
            PageAsset(
                page_id="1001",
                page_name="Nile",
                encrypted_page_token=encryption.encrypt_token(PAGE_TOKEN),
                is_selected=True,
                instagram=InstagramAsset(ig_user_id="178"),
            ),
            PageAsset(page_id="1003", page_name="Giza"),  # not selected
        ],
    )


async def test_token_provider_resolves_only_selected_pages_of_active_connections(
    encryption: TokenEncryptionService,
) -> None:
    repo = FakeConnectedAccountRepository(FakeStore())
    repo.rows[CONNECTION_ID] = _account(encryption, status=AccountStatus.ACTIVE)
    provider = PostgresPageTokenProvider(repo, encryption)

    target = await provider.resolve(BRAND_ID, "1001")
    assert target is not None
    assert target.connection_id == CONNECTION_ID and target.ig_user_id == "178"
    assert target.page_token == PAGE_TOKEN
    assert PAGE_TOKEN not in repr(target)  # repr=False on the token field
    assert await provider.resolve(BRAND_ID, "1003") is None  # not selected
    assert await provider.resolve(BRAND_ID, "4040") is None  # unknown page
    assert await provider.resolve(uuid4(), "1001") is None  # other brand

    repo.rows[CONNECTION_ID] = _account(encryption, status=AccountStatus.REAUTH_REQUIRED)
    assert await provider.resolve(BRAND_ID, "1001") is None
