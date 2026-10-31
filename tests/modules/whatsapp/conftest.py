"""Shared fixtures for the ``whatsapp`` unit tests (fake repository, seeded account)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from src.core.security import TokenEncryptionService
from src.modules.whatsapp.domain.models import WhatsAppAccount
from src.modules.whatsapp.service import hash_verify_token

from tests.conftest import BRAND2_ID, BRAND_ID, OWNER_ID, USER_BY_TENANT
from tests.fakes import FakeWhatsAppRepository

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
def wa_app(app: FastAPI, wa_repo: FakeWhatsAppRepository) -> FastAPI:
    """The fake-wired app with the WhatsApp repository overridden too."""
    from src.modules.whatsapp import webhook

    app.dependency_overrides[webhook._repo] = lambda: wa_repo
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
