"""SPRINT-14-B B-5 — «approve selected»: every line is identified and translated (D-055:
each item is its own upstream decision; nothing is hidden, nothing prints a raw code)."""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.modules.content.domain.models import ContentStatus
from src.modules.entitlements.domain.models import DEFAULT_LIMITS, BrandEntitlement, Plan
from src.ui.router import I18N_PATH

from tests.conftest import BRAND_ID, TEST_PASSWORD
from tests.fakes import FakeContentRepository
from tests.ui.conftest import CONTENT, VIEWER_LOGIN, csrf_of, login
from tests.ui.test_ui_content import HX, MISSING, _item

MESSAGES = json.loads(I18N_PATH.read_text(encoding="utf-8"))
BATCH = f"{CONTENT}/batch-approve"
AGENCY = {"email": "agency@example.com", "password": TEST_PASSWORD}  # MANAGER grant
APPROVED, SKIPPED = MESSAGES["content.batch_approved"], MESSAGES["content.batch_skipped"]


def _line(item: str, text: str) -> str:
    return f'data-batch-item="{item}">{text}<'


def _post(client: TestClient, ids: list[str], csrf: str | None = None):  # noqa: ANN202
    csrf = csrf or csrf_of(client.get(CONTENT).text)
    return client.post(BATCH, data={"csrf": csrf, "item_ids": ids}, headers=HX)


def _raw_codes_absent(html: str) -> None:
    for code in ("invalid_transition", "content_not_found", "entitlement_", "approval_requires"):
        assert code not in html


def test_mixed_results_name_every_item_and_translate_each_reason(
    ui_client: TestClient, content_repo: FakeContentRepository
) -> None:
    login(ui_client)
    ok, draft = _item(ui_client, pending=True), _item(ui_client)
    res = _post(ui_client, [ok, draft, MISSING])
    assert res.status_code == 200 and res.text.count("data-batch-item=") == 3
    assert _line(ok, APPROVED) in res.text
    assert f"{SKIPPED}: {MESSAGES['error.invalid_transition']}" in res.text
    assert f"{SKIPPED}: {MESSAGES['error.content_not_found']}" in res.text
    _raw_codes_absent(res.text)
    assert content_repo.items[UUID(ok)].status is ContentStatus.APPROVED
    assert content_repo.items[UUID(draft)].status is ContentStatus.DRAFT


def test_quota_refusal_is_per_item_and_translated(
    ui_app: FastAPI, ui_client: TestClient, content_repo: FakeContentRepository
) -> None:
    class Counting:  # usage grows with every per-item plan check: 8, 9, 10 (trial = 10 / month)
        calls = 0

        async def monthly_used(self, brand_id: UUID, counter: str, *, since) -> int:  # noqa: ANN001
            self.calls += 1
            return 8 + self.calls - 1

    login(ui_client)
    ids = [_item(ui_client, pending=True) for _ in range(3)]
    csrf = csrf_of(ui_client.get(CONTENT).text)  # the page itself reads the usage counter
    ui_app.state.counter_reader = Counting()
    res = _post(ui_client, ids, csrf)
    assert res.status_code == 200 and res.text.count("data-batch-item=") == 3
    assert _line(ids[0], APPROVED) in res.text and _line(ids[1], APPROVED) in res.text
    assert _line(ids[2], f"{SKIPPED}: {MESSAGES['error.entitlement_quota_exceeded']}") in res.text
    _raw_codes_absent(res.text)
    states = [content_repo.items[UUID(i)].status for i in ids]
    assert states == [ContentStatus.APPROVED] * 2 + [ContentStatus.PENDING_APPROVAL]


def test_inactive_plan_skips_every_item_with_one_arabic_reason(
    ui_app: FastAPI, ui_client: TestClient, content_repo: FakeContentRepository
) -> None:
    login(ui_client)
    ids = [_item(ui_client, pending=True) for _ in range(2)]
    ui_app.state.entitlement_repo.entitlements[BRAND_ID] = BrandEntitlement(
        brand_id=BRAND_ID, plan=Plan.STARTER, limits=DEFAULT_LIMITS[Plan.STARTER], is_active=False
    )
    res = _post(ui_client, ids)
    inactive = f"{SKIPPED}: {MESSAGES['error.entitlement_plan_inactive']}"
    assert res.status_code == 200 and res.text.count(inactive) == 2
    assert all(content_repo.items[UUID(i)].status is ContentStatus.PENDING_APPROVAL for i in ids)
    _raw_codes_absent(res.text)


def test_manager_attempt_is_skipped_per_item_owner_only(
    ui_client: TestClient, content_repo: FakeContentRepository
) -> None:
    login(ui_client)
    ids = [_item(ui_client, pending=True) for _ in range(2)]
    ui_client.cookies.clear()
    login(ui_client, AGENCY)
    res = _post(ui_client, ids)
    owner_only = f"{SKIPPED}: {MESSAGES['error.approval_requires_owner']}"
    assert res.status_code == 200 and res.text.count(owner_only) == 2
    assert all(content_repo.items[UUID(i)].status is ContentStatus.PENDING_APPROVAL for i in ids)


def test_viewer_and_missing_csrf_change_nothing(
    ui_client: TestClient, content_repo: FakeContentRepository
) -> None:
    login(ui_client)
    pending = _item(ui_client, pending=True)
    assert ui_client.post(BATCH, data={"item_ids": [pending]}).status_code == 403  # CSRF first
    ui_client.cookies.clear()
    login(ui_client, VIEWER_LOGIN)
    res = _post(ui_client, [pending])
    assert MESSAGES["error.insufficient_role"] in res.text and "data-batch-item=" not in res.text
    assert content_repo.items[UUID(pending)].status is ContentStatus.PENDING_APPROVAL
