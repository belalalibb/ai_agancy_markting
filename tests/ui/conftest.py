"""Fixtures for the L1 UI (SPRINT-12.1 B): the fake-wired app with Pulse + WhatsApp fakes
and a browser-like ``TestClient`` (cookie jar, no auto-follow)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.security import TokenEncryptionService
from src.modules.pulse.domain import Alert
from src.modules.pulse.router import get_pulse_repository
from src.modules.tenancy.domain.models import GrantRole
from src.modules.whatsapp.domain.models import (
    DeliveryStatus,
    Direction,
    Message,
    MessageKind,
    WhatsAppAccount,
)

from tests.conftest import AGENCY_ID, BRAND_ID, STRANGER_ID, TEST_PASSWORD, grant
from tests.fakes import (
    FakeBrandConsentReader,
    FakeIndependentWrites,
    FakeStore,
    FakeWhatsAppRepository,
)
from tests.modules.pulse.test_pulse import FakePulseRepository
from tests.modules.whatsapp.conftest import CUSTOMER_WA_ID, PHONE_NUMBER_ID, make_account

OWNER_LOGIN = {"email": "nile-owner@example.com", "password": TEST_PASSWORD}
LOGIN = "/app/login"
TODAY = f"/app/b/{BRAND_ID}/today"
INBOX = f"/app/b/{BRAND_ID}/inbox"
GRAPH_MESSAGES = f"/{PHONE_NUMBER_ID}/messages"


@pytest.fixture
def pulse_repo() -> FakePulseRepository:
    return FakePulseRepository()


@pytest.fixture
def wa_repo() -> FakeWhatsAppRepository:
    return FakeWhatsAppRepository()


@pytest.fixture
def ui_app(
    app: FastAPI,
    store: FakeStore,
    pulse_repo: FakePulseRepository,
    wa_repo: FakeWhatsAppRepository,
) -> FastAPI:
    """``app`` + the Pulse and WhatsApp fakes the Today/Inbox screens read through the API."""
    from src.modules.whatsapp import router as wa_router
    from src.modules.whatsapp import webhook

    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    app.dependency_overrides[get_pulse_repository] = lambda: pulse_repo
    app.state.independent_writes = FakeIndependentWrites(wa_repo)
    app.dependency_overrides[webhook._repo] = lambda: wa_repo
    app.dependency_overrides[webhook._consent] = lambda: FakeBrandConsentReader(store)
    app.dependency_overrides[wa_router.get_whatsapp_repository] = lambda: wa_repo
    app.dependency_overrides[wa_router.get_independent_writes] = lambda: (
        app.state.independent_writes
    )
    return app


@pytest.fixture
def ui_client(ui_app: FastAPI) -> Iterator[TestClient]:
    """Browser-like client: keeps cookies, never follows redirects (we assert on them).
    Entered as a context manager so the lifespan runs (``app.state.http_client``)."""
    with TestClient(ui_app, follow_redirects=False) as client:
        yield client


@pytest.fixture
async def wa_account(
    wa_repo: FakeWhatsAppRepository, encryption: TokenEncryptionService
) -> WhatsAppAccount:
    return await wa_repo.create_account(make_account(encryption))


def login(client: TestClient) -> str:
    """POST the login form; returns the ``app_session`` cookie value (asserts 303 ⇒ /app/)."""
    response = client.post(LOGIN, data=OWNER_LOGIN)
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/app/"
    value = client.cookies.get("app_session")
    assert value, response.headers.get("set-cookie")
    assert client.cookies.get("app_access") and client.cookies.get("app_csrf")
    return value


def csrf_of(html: str) -> str:
    marker = 'name="csrf" value="'
    start = html.index(marker) + len(marker)
    return html[start : html.index('"', start)]


def seed_alert(repo: FakePulseRepository, brand_id: UUID = BRAND_ID, **payload: object) -> Alert:
    alert = Alert(
        brand_id=brand_id,
        kind="publish_failed_24h",
        severity="warning",
        payload={"ref": str(uuid4()), **payload},
        created_at=datetime.now(tz=UTC),
    )
    repo.alerts[alert.id] = alert
    return alert


async def seed_conversation(
    repo: FakeWhatsAppRepository,
    account_id: UUID,
    *,
    inbound_ago: timedelta = timedelta(hours=21),
    wa_id: str = CUSTOMER_WA_ID,
    profile_name: str | None = "Nour",
    body: str = "hello",
):  # noqa: ANN201
    """One open conversation with a single inbound text; the 24h window closes in
    ``24h - inbound_ago`` (default 3h ⇒ shows in the *closing ≤ 4h* card)."""
    at = datetime.now(tz=UTC) - inbound_ago
    conv = await repo.upsert_conversation_inbound(
        brand_id=BRAND_ID,
        account_id=account_id,
        wa_id=wa_id,
        profile_name=profile_name,
        at=at,
        window_expires_at=at + timedelta(hours=24),
    )
    await repo.insert_inbound_if_new(
        Message(
            conversation_id=conv.id,
            brand_id=BRAND_ID,
            direction=Direction.INBOUND,
            wamid=f"wamid.IN.{uuid4()}",
            kind=MessageKind.TEXT,
            body=body,
            delivery_status=DeliveryStatus.RECEIVED,
            platform_timestamp=at,
            created_at=at,
        )
    )
    return conv
