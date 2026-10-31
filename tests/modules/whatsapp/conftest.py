"""Shared fixtures for the ``whatsapp`` unit tests (fake repository, seeded account)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from src.core.config import Settings
from src.core.security import TokenEncryptionService
from src.modules.whatsapp.client import WhatsAppCloudClient
from src.modules.whatsapp.domain.models import WhatsAppAccount
from src.modules.whatsapp.service import hash_verify_token

from tests.conftest import BRAND2_ID, BRAND_ID, OWNER_ID, USER_BY_TENANT
from tests.fakes import FakeIndependentWrites, FakeWhatsAppRepository

PAYLOADS = Path(__file__).parent / "payloads"
PHONE_NUMBER_ID = "PNID_1001"
PHONE_NUMBER_ID_2 = "PNID_2002"
SYSTEM_TOKEN = "EAAWhatsAppSystemTokenLEAK"  # noqa: S105 - test fixture, not a credential
APP_SECRET = "wa-app-secret-for-tests"  # noqa: S105 - test fixture
APP_SECRET_2 = "other-brand-secret"  # noqa: S105 - test fixture
VERIFY_TOKEN = "verify-me-please"  # noqa: S105 - test fixture
CUSTOMER_WA_ID = "201234567890"


def load_payload(name: str) -> dict[str, Any]:
    return json.loads((PAYLOADS / f"{name}.json").read_text(encoding="utf-8"))


def make_account(
    encryption: TokenEncryptionService,
    *,
    brand_id=BRAND_ID,  # noqa: ANN001
    phone_number_id: str = PHONE_NUMBER_ID,
    app_secret: str = APP_SECRET,
) -> WhatsAppAccount:
    now = datetime.now(tz=UTC)
    return WhatsAppAccount(
        brand_id=brand_id,
        phone_number_id=phone_number_id,
        waba_id="WABA_ID_1",
        display_phone="+1 555 000 1111",
        encrypted_system_token=encryption.encrypt_token(SYSTEM_TOKEN),
        encrypted_app_secret=encryption.encrypt_token(app_secret),
        verify_token_hash=hash_verify_token(VERIFY_TOKEN),
        created_by_user_id=USER_BY_TENANT[OWNER_ID],
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def wa_repo() -> FakeWhatsAppRepository:
    return FakeWhatsAppRepository()


@pytest.fixture
async def wa_client(settings: Settings):  # noqa: ANN201
    """A real :class:`WhatsAppCloudClient` over an ``httpx`` client (``respx`` intercepts),
    with back-off sleeps disabled."""

    async def _no_sleep(_: float) -> None:
        return None

    async with httpx.AsyncClient() as http:
        yield WhatsAppCloudClient(settings=settings, http_client=http, sleep=_no_sleep)


@pytest.fixture
def wa_app(app: FastAPI, wa_repo: FakeWhatsAppRepository, content_repo) -> FastAPI:  # noqa: ANN001
    """The fake-wired app with the WhatsApp repository overridden too (B-3: the content
    repository is linked so the shared daily token budget sums both features)."""
    from src.modules.whatsapp import router, webhook

    app.state.budget_reader.wa_repo = wa_repo  # A-4: drafts count in the shared budget
    app.state.independent_writes = FakeIndependentWrites(wa_repo)  # A-1 (D-072)
    app.dependency_overrides[webhook._repo] = lambda: wa_repo
    app.dependency_overrides[router.get_whatsapp_repository] = lambda: wa_repo
    app.dependency_overrides[router.get_independent_writes] = lambda: app.state.independent_writes
    return app


@pytest.fixture
async def wa_account(
    wa_repo: FakeWhatsAppRepository, encryption: TokenEncryptionService
) -> WhatsAppAccount:
    return await wa_repo.create_account(make_account(encryption))


@pytest.fixture
async def wa_account_brand2(
    wa_repo: FakeWhatsAppRepository, encryption: TokenEncryptionService
) -> WhatsAppAccount:
    return await wa_repo.create_account(
        make_account(
            encryption,
            brand_id=BRAND2_ID,
            phone_number_id=PHONE_NUMBER_ID_2,
            app_secret=APP_SECRET_2,
        )
    )
