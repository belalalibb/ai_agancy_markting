"""SPRINT-14-B B-4 — a human drafts, submits, approves (OWNER) and schedules a post through
the browser forms only. Real routers + in-memory repositories; no network, no provider."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from src.modules.content.domain.models import ContentStatus
from src.modules.publishing.domain.models import JobStatus
from src.modules.social_connect.domain.models import (
    AccountStatus,
    ConnectedAccount,
    InstagramAsset,
    PageAsset,
)
from src.ui.router import I18N_PATH

from tests.conftest import BRAND_ID, OWNER_ID, TEST_PASSWORD
from tests.fakes import (
    FakeConnectedAccountRepository,
    FakeContentRepository,
    FakePublishJobRepository,
    FakeStore,
)
from tests.ui.conftest import CONTENT, VIEWER_LOGIN, csrf_of, login
from tests.ui.test_ui_brain import OUTSIDER, _outsider

MESSAGES = json.loads(I18N_PATH.read_text(encoding="utf-8"))
SCOPES = [
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "instagram_basic",
    "instagram_content_publish",
]
BODY = "منشور تجريبي طويل بما يكفي لاختبار شاشة المحتوى."
IMAGE = "https://cdn.example.com/media/offer.jpg"
AGENCY = {"email": "agency@example.com", "password": TEST_PASSWORD}  # MANAGER grant
NEW = f"{CONTENT}/new"


def _page(page_id: str, name: str, *, selected: bool = True, ig: bool = False) -> PageAsset:
    return PageAsset(
        page_id=page_id,
        page_name=name,
        encrypted_page_token="gAAAAA-page" if selected else None,
        is_selected=selected,
        instagram=InstagramAsset(ig_user_id="178", username="nile") if ig else None,
    )


def _connect(
    repo: FakeConnectedAccountRepository,
    pages: list[PageAsset] | None = None,
    *,
    status: AccountStatus = AccountStatus.ACTIVE,
    brand: UUID = BRAND_ID,
) -> None:
    cid = uuid4()
    repo.rows[cid] = ConnectedAccount(
        id=cid,
        brand_id=brand,
        connected_by_tenant_id=OWNER_ID,
        provider_user_id=str(uuid4())[:12],
        encrypted_user_token="gAAAAA-user",
        status=status,
        granted_scopes=SCOPES,
        scopes_verified=True,
        pages=pages
        if pages is not None
        else [
            _page("1001", "صفحة النيل", ig=True),
            _page("1002", "صفحة القاهرة"),
            _page("1003", "صفحة الجيزة", selected=False),
        ],
    )


def _csrf(client: TestClient) -> str:
    return csrf_of(client.get(CONTENT).text)


def _create(client: TestClient, **over: str) -> int:
    data = {"csrf": _csrf(client), "channel": "facebook_page", "body": BODY}
    data.update(over)
    return client.post(NEW, data=data).status_code


def _force(repo: FakeContentRepository, item_id: UUID, status: ContentStatus) -> None:
    item = repo.items[item_id]
    repo.items[item_id] = item.model_copy(update={"status": status})


def _only_item(repo: FakeContentRepository) -> UUID:
    assert len(repo.items) == 1
    return next(iter(repo.items))


def _options(html: str, name: str) -> list[str]:
    block = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', html, re.S)
    assert block, name
    return re.findall(r'value="([^"]*)"', block.group(1))


# ------------------------------------------------------------------ create ----
def test_create_posts_exact_api_body_and_redirects_with_message(
    ui_client: TestClient, fake_repo: FakeConnectedAccountRepository, content_repo
) -> None:
    _connect(fake_repo)
    login(ui_client)
    res = ui_client.post(
        NEW,
        data={
            "csrf": _csrf(ui_client),
            "channel": "facebook_page",
            "body": BODY,
            "target_page_id": "1002",
        },
    )
    assert res.status_code == 303
    assert res.headers["location"] == f"{CONTENT}?status=draft&flash=draft_created"
    item = content_repo.items[_only_item(content_repo)]
    assert item.status is ContentStatus.DRAFT and item.target_page_id == "1002"
    assert item.scheduled_for is None  # never a schedule at creation (D-055)
    assert content_repo.revisions[item.id][0].media_refs == []
    html = ui_client.get(res.headers["location"]).text
    assert MESSAGES["content.created"] in html and "data-flash" in html
    # the flash key set is closed: free text in the query string is never echoed
    assert "<script>" not in ui_client.get(f"{CONTENT}?flash=<script>").text


def test_form_offers_only_active_channels_and_selected_active_pages(
    ui_client: TestClient, fake_repo: FakeConnectedAccountRepository
) -> None:
    _connect(fake_repo)  # 1001 (IG), 1002 selected; 1003 not selected
    _connect(fake_repo, [_page("2001", "منتهية")], status=AccountStatus.REAUTH_REQUIRED)
    _connect(fake_repo, [_page("3001", "علامة أخرى")], brand=uuid4())
    login(ui_client)
    html = ui_client.get(CONTENT).text
    assert _options(html, "channel") == ["facebook_page", "instagram"]
    # value = Graph page_id, never connection_id / instagram_user_id
    assert _options(html, "target_page_id") == ["", "1001", "1002"]
    assert "178" not in _options(html, "target_page_id")
    for gone in ("1003", "2001", "3001", "linkedin", 'value="x"'):
        assert gone not in html


def test_bad_targets_are_refused_with_text_kept_and_nothing_created(
    ui_client: TestClient, fake_repo: FakeConnectedAccountRepository, content_repo
) -> None:
    _connect(fake_repo)
    _connect(fake_repo, [_page("3001", "علامة أخرى")], brand=uuid4())
    login(ui_client)
    cases = {
        "channel_not_activated": {"channel": "linkedin"},
        "target_page_not_selected": {"target_page_id": "1003"},  # unselected
        "instagram_not_linked": {"channel": "instagram", "target_page_id": "1002"},
        "invalid_image_url": {
            "channel": "instagram",
            "target_page_id": "1001",
            "image_url": "http://cdn.example.com/a.jpg",
        },
    }
    other_brand = ("target_page_not_selected", {"target_page_id": "3001"})
    for code, over in [*cases.items(), other_brand]:
        res = ui_client.post(
            NEW,
            data={"csrf": _csrf(ui_client), "channel": "facebook_page", "body": BODY} | over,
        )
        assert res.status_code == 422, code
        key = f"error.{code}"
        assert MESSAGES[key] in res.text and BODY in res.text  # message + user's text kept
    assert not content_repo.items


def test_instagram_image_goes_to_media_refs_and_missing_image_blocks_schedule(
    ui_client: TestClient, fake_repo: FakeConnectedAccountRepository, content_repo
) -> None:
    _connect(fake_repo)
    login(ui_client)
    assert _create(ui_client, channel="instagram", target_page_id="1001", image_url=IMAGE) == 303
    item_id = _only_item(content_repo)
    assert content_repo.revisions[item_id][0].media_refs == [{"url": IMAGE, "kind": "image"}]
    # facebook ignores an image field (no media_refs); IG without image is a draft that
    # the API refuses to schedule — translated, no 500
    assert _create(ui_client, channel="instagram", target_page_id="1001") == 303
    bare = next(i for i in content_repo.items if i != item_id)
    assert content_repo.revisions[bare][0].media_refs == []
    _force(content_repo, bare, ContentStatus.APPROVED)
    when = (datetime.now(UTC) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    res = ui_client.post(
        f"{CONTENT}/{bare}/schedule",
        data={"csrf": _csrf(ui_client), "scheduled_for": when, "target_page_id": "1001"},
    )
    assert MESSAGES["error.instagram_requires_image"] in res.text


def test_draft_without_a_page_warns_and_cannot_be_scheduled(
    ui_client: TestClient, content_repo
) -> None:
    login(ui_client)  # no connection at all
    html = ui_client.get(CONTENT).text
    assert "data-no-page" in html and MESSAGES["content.connect_link"] in html
    assert f'href="/app/b/{BRAND_ID}/settings#connect"' in html
    assert _create(ui_client) == 303  # draft allowed without a page
    item_id = _only_item(content_repo)
    _force(content_repo, item_id, ContentStatus.APPROVED)
    html = ui_client.get(CONTENT).text
    assert 'data-form="schedule"' in html and 'name="scheduled_for"' not in html
    when = (datetime.now(UTC) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    res = ui_client.post(
        f"{CONTENT}/{item_id}/schedule", data={"csrf": _csrf(ui_client), "scheduled_for": when}
    )
    assert MESSAGES["error.target_page_not_selected"] in res.text


# --------------------------------------------------------- schedule + UTC ----
def test_schedule_form_has_dropdown_and_utc_label_and_converts_to_aware_iso(
    ui_client: TestClient,
    fake_repo: FakeConnectedAccountRepository,
    content_repo,
    publish_repo: FakePublishJobRepository,
) -> None:
    _connect(fake_repo)
    login(ui_client)
    assert _create(ui_client, target_page_id="1002") == 303
    item_id = _only_item(content_repo)
    _force(content_repo, item_id, ContentStatus.APPROVED)
    html = ui_client.get(CONTENT).text
    assert MESSAGES["content.schedule_utc"] in html and "UTC" in MESSAGES["content.schedule_utc"]
    form = html.split('data-form="schedule"', 1)[1]
    assert 'type="text" name="target_page_id"' not in html
    assert _options(
        "<x>" + form.split("</form>")[0].replace("<select ", "<select ", 1), "target_page_id"
    ) == ["1001", "1002"]
    naive = (datetime.now(UTC) + timedelta(days=3)).replace(second=0, microsecond=0)
    res = ui_client.post(
        f"{CONTENT}/{item_id}/schedule",
        data={
            "csrf": _csrf(ui_client),
            "scheduled_for": naive.strftime("%Y-%m-%dT%H:%M"),
            "target_page_id": "1002",
        },
    )
    assert res.status_code == 200 and MESSAGES["content.done"] in res.text
    (job,) = publish_repo.jobs.values()
    assert job.run_at == naive and job.run_at.utcoffset() == timedelta(0)
    assert job.target_page_id == "1002"


def test_invalid_and_too_soon_datetimes_are_translated_never_500(
    ui_client: TestClient, fake_repo: FakeConnectedAccountRepository, content_repo, publish_repo
) -> None:
    _connect(fake_repo)
    login(ui_client)
    _create(ui_client, target_page_id="1002")
    item_id = _only_item(content_repo)
    _force(content_repo, item_id, ContentStatus.APPROVED)
    csrf = _csrf(ui_client)
    for garbage in ("not-a-date", "2030-13-45T99:99", "9999999999"):
        res = ui_client.post(
            f"{CONTENT}/{item_id}/schedule",
            data={"csrf": csrf, "scheduled_for": garbage, "target_page_id": "1002"},
        )
        assert res.status_code == 400 and MESSAGES["error.invalid_datetime"] in res.text
    soon = (datetime.now(UTC) + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M")
    res = ui_client.post(
        f"{CONTENT}/{item_id}/schedule",
        data={"csrf": csrf, "scheduled_for": soon, "target_page_id": "1002"},
    )
    assert MESSAGES["error.schedule_too_soon"] in res.text
    assert not publish_repo.jobs
    # CSRF is checked before the datetime is even looked at
    bad = ui_client.post(f"{CONTENT}/{item_id}/schedule", data={"scheduled_for": "x"})
    assert bad.status_code == 403


# ---------------------------------------------------------- roles + tenancy ----
def test_viewer_sees_no_draft_form_and_cannot_create(
    ui_client: TestClient, fake_repo: FakeConnectedAccountRepository, content_repo
) -> None:
    _connect(fake_repo)
    login(ui_client, VIEWER_LOGIN)
    html = ui_client.get(CONTENT).text
    assert 'data-form="new-draft"' not in html and 'data-card="new-draft"' not in html
    res = ui_client.post(
        NEW, data={"csrf": csrf_of(html), "channel": "facebook_page", "body": BODY}
    )
    assert res.status_code == 403 and MESSAGES["error.insufficient_role"] in res.text
    assert not content_repo.items


def test_manager_can_draft_but_approve_stays_owner_only(
    ui_client: TestClient, fake_repo: FakeConnectedAccountRepository, content_repo
) -> None:
    _connect(fake_repo)
    login(ui_client, AGENCY)
    assert _create(ui_client, target_page_id="1002") == 303
    item_id = _only_item(content_repo)
    csrf = _csrf(ui_client)
    ui_client.post(f"{CONTENT}/{item_id}/submit", data={"csrf": csrf})
    res = ui_client.post(f"{CONTENT}/{item_id}/approve", data={"csrf": csrf})
    assert MESSAGES["error.approval_requires_owner"] in res.text
    assert content_repo.items[item_id].status is ContentStatus.PENDING_APPROVAL


def test_outsider_and_anonymous_and_csrf(
    ui_client: TestClient, store: FakeStore, content_repo
) -> None:
    assert ui_client.post(NEW, data={"channel": "facebook_page", "body": BODY}).status_code == 303
    _outsider(store)
    login(ui_client, OUTSIDER)
    csrf = csrf_of(ui_client.get("/app/").text)
    res = ui_client.post(NEW, data={"csrf": csrf, "channel": "facebook_page", "body": BODY})
    assert res.status_code == 404
    login(ui_client)
    res = ui_client.post(NEW, data={"csrf": "bad", "channel": "facebook_page", "body": BODY})
    assert res.status_code == 403
    assert not content_repo.items


# ------------------------------------------------------------ full journey ----
def test_full_journey_create_submit_approve_schedule_queues_a_job_only(
    ui_client: TestClient,
    fake_repo: FakeConnectedAccountRepository,
    content_repo,
    publish_repo: FakePublishJobRepository,
) -> None:
    _connect(fake_repo)
    login(ui_client)
    html = ui_client.get("/app/").text
    assert "data-onboarding" in html
    assert _create(ui_client, target_page_id="1001") == 303
    item_id = _only_item(content_repo)
    assert content_repo.items[item_id].status is ContentStatus.DRAFT and not publish_repo.jobs
    csrf = _csrf(ui_client)
    for action, expected in (("submit", "pending_approval"), ("approve", "approved")):
        res = ui_client.post(f"{CONTENT}/{item_id}/{action}", data={"csrf": csrf})
        assert res.status_code == 200
        assert content_repo.items[item_id].status.value == expected
        assert not publish_repo.jobs  # nothing is queued until a human schedules
    when = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    res = ui_client.post(
        f"{CONTENT}/{item_id}/schedule",
        data={"csrf": csrf, "scheduled_for": when, "target_page_id": "1001"},
    )
    assert res.status_code == 200
    assert content_repo.items[item_id].status is ContentStatus.SCHEDULED
    (job,) = publish_repo.jobs.values()
    assert job.status is JobStatus.QUEUED and job.attempt_count == 0
    assert not publish_repo.attempts  # no provider call was made
    # first draft done + a selected page ⇒ the onboarding bar is gone
    assert "data-onboarding" not in ui_client.get("/app/").text
