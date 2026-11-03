"""SPRINT-12.1-B C-1 — the content screen through the real API (fakes behind it)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from src.modules.content.domain.models import ContentStatus
from src.ui.router import I18N_PATH

from tests.conftest import BRAND_ID
from tests.fakes import FakeContentRepository
from tests.ui.conftest import CONTENT, csrf_of, login

API = f"/api/v1/brands/{BRAND_ID}/content"
BODY = "A long enough post body for the content screen tests."
HX = {"HX-Request": "true"}
MISSING = "00000000-0000-4000-8000-0000000000ff"
MESSAGES = json.loads(I18N_PATH.read_text(encoding="utf-8"))


def _item(client: TestClient, pending: bool = False) -> str:
    """Create a draft through the API (the UI session's access JWT as Bearer); optionally
    submit it so it sits in ``pending_approval``."""
    token = client.cookies.get("app_access")
    headers = {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}
    res = client.post(
        f"{API}/items", json={"channel": "facebook_page", "body": BODY}, headers=headers
    )
    assert res.status_code == 201, res.text
    item_id = res.json()["id"]
    if pending:
        res = client.post(f"{API}/items/{item_id}/submit", json={}, headers=headers)
        assert res.status_code == 200, res.text
    return item_id


def _force(repo: FakeContentRepository, item_id: str, status: ContentStatus) -> None:
    item = repo.items[UUID(item_id)]
    repo.items[item.id] = item.model_copy(update={"status": status})


def test_content_renders_tabs_items_and_state_buttons(
    ui_client: TestClient, content_repo: FakeContentRepository
) -> None:
    login(ui_client)
    draft = _item(ui_client)
    pending = _item(ui_client, pending=True)
    failed = _item(ui_client)
    _force(content_repo, failed, ContentStatus.PUBLISH_FAILED)
    html = ui_client.get(CONTENT).text
    assert '<html lang="ar" dir="rtl">' in html and "data-tabs" in html
    for s in ("draft", "pending_approval", "approved", "scheduled", "published", "publish_failed"):
        assert f"?status={s}" in html
    assert f'data-item="{draft}" data-status="draft"' in html
    assert f'/content/{draft}/submit"' in html and f'/content/{draft}/generate"' in html
    assert f'/content/{pending}/approve"' in html and f'/content/{pending}/reject"' in html
    assert f'/content/{failed}/retry"' in html
    assert f'/content/{draft}/approve"' not in html  # buttons mirror the state machine
    # tab filter + htmx partial
    res = ui_client.get(f"{CONTENT}?status=pending_approval", headers=HX)
    assert res.status_code == 200 and "<html" not in res.text
    assert pending in res.text and draft not in res.text
    assert MESSAGES["status.pending_approval"] in res.text and "status.pending" not in res.text


def test_content_submit_then_approve_success(ui_client: TestClient) -> None:
    login(ui_client)
    item = _item(ui_client)
    csrf = csrf_of(ui_client.get(CONTENT).text)
    res = ui_client.post(
        f"{CONTENT}/{item}/submit", data={"csrf": csrf, "status": "draft"}, headers=HX
    )
    assert res.status_code == 200, res.text
    assert MESSAGES["content.done"] in res.text
    html = ui_client.get(CONTENT).text
    assert f'data-item="{item}" data-status="pending_approval"' in html
    res = ui_client.post(f"{CONTENT}/{item}/approve", data={"csrf": csrf})
    assert res.status_code == 200
    assert f'data-item="{item}" data-status="approved"' in res.text


def test_content_errors_become_translated_flash(ui_client: TestClient) -> None:
    login(ui_client)
    item = _item(ui_client)
    csrf = csrf_of(ui_client.get(CONTENT).text)
    # invalid transition (approve a draft) ⇒ translated flash in the partial, no 500
    res = ui_client.post(f"{CONTENT}/{item}/approve", data={"csrf": csrf}, headers=HX)
    assert res.status_code == 200
    assert MESSAGES["error.invalid_transition"] in res.text
    assert "invalid_transition" not in res.text
    # reject needs a note ≥ 3 ⇒ 422 upstream ⇒ translated flash, no raw JSON
    ui_client.post(f"{CONTENT}/{item}/submit", data={"csrf": csrf})
    res = ui_client.post(f"{CONTENT}/{item}/reject", data={"csrf": csrf, "note": "x"}, headers=HX)
    assert res.status_code == 200 and "data-flash" in res.text
    assert "detail" not in res.text and "{" not in res.text.split("data-flash>", 1)[1][:200]
    # generate without a gateway ⇒ 503 ai_provider_not_configured ⇒ translated
    res = ui_client.post(
        f"{CONTENT}/{item}/generate", data={"csrf": csrf, "brief": "ten chars brief"}, headers=HX
    )
    assert res.status_code == 200 and "data-flash" in res.text
    assert "ai_provider_not_configured" not in res.text
    # schedule too soon ⇒ translated
    soon = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    res = ui_client.post(f"{CONTENT}/{item}/schedule", data={"csrf": csrf, "scheduled_for": soon})
    assert res.status_code == 200 and "schedule_too_soon" not in res.text
    # unknown action ⇒ 404 page, but only *after* the session + CSRF gate
    assert ui_client.post(f"{CONTENT}/{item}/publish", data={"csrf": csrf}).status_code == 404
    assert ui_client.post(f"{CONTENT}/{item}/publish", data={}).status_code == 403


def test_content_batch_three_mixed_gives_three_translated_lines(
    ui_client: TestClient, content_repo: FakeContentRepository
) -> None:
    login(ui_client)
    ok = _item(ui_client, pending=True)
    draft = _item(ui_client)  # ⇒ invalid_transition
    csrf = csrf_of(ui_client.get(CONTENT).text)
    res = ui_client.post(
        f"{CONTENT}/batch-approve",
        data={"csrf": csrf, "item_ids": [ok, draft, MISSING]},
        headers=HX,
    )
    assert res.status_code == 200, res.text
    assert res.text.count("data-batch-item=") == 3
    assert f'data-batch-item="{ok}">{MESSAGES["content.batch_approved"]}<' in res.text
    skipped = MESSAGES["content.batch_skipped"]
    assert f"{skipped}: {MESSAGES['error.invalid_transition']}" in res.text
    assert f"{skipped}: {MESSAGES['error.content_not_found']}" in res.text
    assert "invalid_transition" not in res.text and "content_not_found" not in res.text
    assert content_repo.items[UUID(ok)].status == ContentStatus.APPROVED
    # empty selection ⇒ 422 upstream ⇒ translated flash, never a 500
    res = ui_client.post(f"{CONTENT}/batch-approve", data={"csrf": csrf}, headers=HX)
    assert res.status_code == 200 and MESSAGES["error.http_422"] in res.text
