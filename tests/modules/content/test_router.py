"""SPRINT-06 B3 — content routes against the fakes (roles, visibility, lifecycle, isolation)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.modules.content.domain.models import ContentStatus
from src.modules.social_connect.domain.models import (
    AccountStatus,
    ConnectedAccount,
    InstagramAsset,
    PageAsset,
)
from src.modules.tenancy.domain.models import GrantRole

from tests.conftest import AGENCY_ID, BRAND2_ID, BRAND_ID, OWNER_ID, STRANGER_ID, grant
from tests.fakes import (
    FakeConnectedAccountRepository,
    FakeContentRepository,
    FakeRateLimiter,
    FakeStore,
)

Headers = Callable[[UUID], dict[str, str]]
API = "/api/v1"
BASE = f"{API}/brands/{BRAND_ID}/content"
BODY = "عرض خاص هذا الأسبوع على كل خدمات التجميل — احجز الآن."


@pytest.fixture
def as_(auth_headers: Headers) -> Headers:
    return auth_headers


def _create(client: TestClient, headers: dict[str, str], **overrides: object) -> dict:
    payload = {"channel": "facebook_page", "body": BODY, **overrides}
    res = client.post(f"{BASE}/items", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _act(client: TestClient, headers: dict[str, str], item_id: str, action: str, **json: object):
    return client.post(f"{BASE}/items/{item_id}/{action}", json=json or None, headers=headers)


def _to_pending(client: TestClient, owner: dict[str, str]) -> str:
    item_id = _create(client, owner)["id"]
    assert _act(client, owner, item_id, "submit").status_code == 200
    return item_id


def _to_approved(client: TestClient, owner: dict[str, str]) -> str:
    item_id = _to_pending(client, owner)
    assert _act(client, owner, item_id, "approve").status_code == 200
    return item_id


PUBLISH_SCOPES = [
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "instagram_basic",
    "instagram_content_publish",
]
IMAGE_URL = "https://cdn.example.com/media/offer.jpg"


def _active_connection(
    fake_repo: FakeConnectedAccountRepository,
    *,
    scopes: list[str] | None = None,
    scopes_verified: bool = True,
) -> UUID:
    """A verified connection with the SPRINT-07 publishing scopes (B3) unless told otherwise."""
    cid = uuid4()
    fake_repo.rows[cid] = ConnectedAccount(
        brand_id=BRAND_ID,
        connected_by_tenant_id=OWNER_ID,
        provider_user_id="90001",
        encrypted_user_token="gAAAAA-user",
        status=AccountStatus.ACTIVE,
        granted_scopes=list(PUBLISH_SCOPES if scopes is None else scopes),
        scopes_verified=scopes_verified,
        pages=[
            PageAsset(
                page_id="1001",
                page_name="Nile",
                encrypted_page_token="gAAAAA-page",
                is_selected=True,
                instagram=InstagramAsset(ig_user_id="178", username="nile"),
            ),
            PageAsset(
                page_id="1002",
                page_name="Cairo",
                encrypted_page_token="gAAAAA-p2",
                is_selected=True,
            ),
            PageAsset(page_id="1003", page_name="Giza"),  # not selected, no token
        ],
    )
    return cid


# ------------------------------------------------------------------ B3-U1 create --
def test_b3_u1_owner_and_manager_create_drafts_viewer_cannot(
    client: TestClient, store: FakeStore, as_: Headers, content_repo: FakeContentRepository
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    owner_item = _create(client, as_(OWNER_ID))
    assert owner_item["status"] == "draft"
    assert owner_item["created_by_tenant_id"] == str(OWNER_ID)
    assert owner_item["current_body"] == BODY
    manager_item = _create(client, as_(AGENCY_ID), channel="instagram")
    assert manager_item["channel"] == "instagram"
    viewer = client.post(
        f"{BASE}/items", json={"channel": "facebook_page", "body": BODY}, headers=as_(STRANGER_ID)
    )
    assert viewer.status_code == 403
    assert viewer.json()["detail"]["error"] == "insufficient_role"
    # revision 1 with human provenance
    revs = content_repo.revisions[UUID(owner_item["id"])]
    assert [r.revision_no for r in revs] == [1]
    assert revs[0].provenance == {"kind": "human"}
    assert owner_item["current_revision_id"] == str(revs[0].id)


def test_b3_u1_create_validates_body_and_channel(client: TestClient, as_: Headers) -> None:
    owner = as_(OWNER_ID)
    # SPRINT-08 B-1: ``tiktok`` is now a registered (structural) channel ⇒ drafts are allowed;
    # an unknown value is still rejected by the enum-derived ``ChannelLiteral``.
    assert (
        client.post(
            f"{BASE}/items", json={"channel": "myspace", "body": BODY}, headers=owner
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"{BASE}/items", json={"channel": "instagram", "body": ""}, headers=owner
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"{BASE}/items", json={"channel": "instagram", "body": "x" * 5001}, headers=owner
        ).status_code
        == 422
    )
    # SPRINT-07 A-2: the 5000 cap is the schema ceiling; the channel limit is enforced in the
    # service (Instagram 2200 ⇒ 422 body_too_long, Facebook 5000 ⇒ 201).
    assert (
        client.post(
            f"{BASE}/items", json={"channel": "facebook_page", "body": "x" * 5000}, headers=owner
        ).status_code
        == 201
    )


# ------------------------------------------------------------- B3-U2 read + list --
def test_b3_u2_all_roles_read_items_and_detail(
    client: TestClient, store: FakeStore, as_: Headers
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    item_id = _to_pending(client, as_(OWNER_ID))
    for tenant in (OWNER_ID, AGENCY_ID, STRANGER_ID):
        listed = client.get(f"{BASE}/items", headers=as_(tenant))
        assert listed.status_code == 200, listed.text
        assert [i["id"] for i in listed.json()["items"]] == [item_id]
        detail = client.get(f"{BASE}/items/{item_id}", headers=as_(tenant))
        assert detail.status_code == 200
        body = detail.json()
        assert body["status"] == "pending_approval"
        assert [r["revision_no"] for r in body["revisions"]] == [1]
        assert [e["action"] for e in body["approval_events"]] == ["submitted"]
        assert body["approval_events"][0]["actor_tenant_id"] == str(OWNER_ID)


def test_b3_u2_list_filters_by_status_and_paginates_keyset(
    client: TestClient, as_: Headers
) -> None:
    owner = as_(OWNER_ID)
    ids = [_create(client, owner)["id"] for _ in range(5)]
    _act(client, owner, ids[0], "submit")
    first = client.get(f"{BASE}/items", params={"limit": 2}, headers=owner).json()
    assert len(first["items"]) == 2 and first["has_more"] is True and first["next_cursor"]
    second = client.get(
        f"{BASE}/items", params={"limit": 2, "cursor": first["next_cursor"]}, headers=owner
    ).json()
    third = client.get(
        f"{BASE}/items", params={"limit": 2, "cursor": second["next_cursor"]}, headers=owner
    ).json()
    seen = [i["id"] for page in (first, second, third) for i in page["items"]]
    assert sorted(seen) == sorted(ids) and len(seen) == 5
    assert third["has_more"] is False and third["next_cursor"] is None
    pending = client.get(f"{BASE}/items", params={"status": "pending_approval"}, headers=owner)
    assert [i["id"] for i in pending.json()["items"]] == [ids[0]]
    assert client.get(f"{BASE}/items", params={"limit": 101}, headers=owner).status_code == 422
    assert (
        client.get(f"{BASE}/items", params={"cursor": "!!bad"}, headers=owner).status_code == 422
    )
    assert (
        client.get(f"{BASE}/items", params={"status": "bogus"}, headers=owner).status_code == 422
    )


# ----------------------------------------------------------- B3-U3 owner approval --
def test_b3_u3_only_owner_approves_or_rejects(
    client: TestClient, store: FakeStore, as_: Headers
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    item_id = _to_pending(client, as_(AGENCY_ID))  # manager drafted and submitted
    manager = _act(client, as_(AGENCY_ID), item_id, "approve")
    assert manager.status_code == 403
    assert manager.json()["detail"]["error"] == "approval_requires_owner"
    manager_reject = _act(client, as_(AGENCY_ID), item_id, "reject", note="too long")
    assert manager_reject.status_code == 403
    assert manager_reject.json()["detail"]["error"] == "approval_requires_owner"
    viewer = _act(client, as_(STRANGER_ID), item_id, "approve")
    assert viewer.status_code == 403 and viewer.json()["detail"]["error"] == "insufficient_role"
    owner = _act(client, as_(OWNER_ID), item_id, "approve", note="ok")
    assert owner.status_code == 200 and owner.json()["status"] == "approved"
    detail = client.get(f"{BASE}/items/{item_id}", headers=as_(OWNER_ID)).json()
    assert [(e["action"], e["actor_tenant_id"]) for e in detail["approval_events"]] == [
        ("submitted", str(AGENCY_ID)),
        ("approved", str(OWNER_ID)),
    ]


def test_b3_u3_owner_approves_own_draft_and_reject_needs_note(
    client: TestClient, as_: Headers
) -> None:
    owner = as_(OWNER_ID)
    item_id = _to_pending(client, owner)
    assert _act(client, owner, item_id, "reject").status_code == 422  # body required
    short = _act(client, owner, item_id, "reject", note="no")
    assert short.status_code == 422
    rejected = _act(client, owner, item_id, "reject", note="claims not in brief")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    detail = client.get(f"{BASE}/items/{item_id}", headers=owner).json()
    assert detail["approval_events"][-1] == {
        **detail["approval_events"][-1],
        "action": "rejected",
        "note": "claims not in brief",
    }
    own = _to_pending(client, owner)
    assert _act(client, owner, own, "approve").status_code == 200


# ----------------------------------------------------- B3-U4 invalid transitions 409 --
def test_b3_u4_invalid_transition_is_409_with_from_and_action(
    client: TestClient, as_: Headers
) -> None:
    owner = as_(OWNER_ID)
    item_id = _create(client, owner)["id"]
    res = _act(client, owner, item_id, "approve")
    assert res.status_code == 409
    assert res.json()["detail"] == {
        **res.json()["detail"],
        "error": "invalid_transition",
        "from": "draft",
        "action": "approve",
    }
    assert _act(client, owner, item_id, "archive").status_code == 200
    for action in ("submit", "withdraw", "approve", "schedule", "unschedule", "archive"):
        kwargs = {"scheduled_for": "2030-01-01T00:00:00Z"} if action == "schedule" else {}
        res = _act(client, owner, item_id, action, **kwargs)
        assert res.status_code == 409, (action, res.text)
        assert res.json()["detail"]["from"] == "archived"
    edit = client.put(f"{BASE}/items/{item_id}/body", json={"body": "x"}, headers=owner)
    assert edit.status_code == 409 and edit.json()["detail"]["from"] == "archived"


# ------------------------------------------------------- B2-U2 edit resets to draft --
def test_b2_u2_body_edit_after_submit_creates_revision_and_withdraws(
    client: TestClient, store: FakeStore, as_: Headers, content_repo: FakeContentRepository
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    owner = as_(OWNER_ID)
    item_id = _to_approved(client, owner)
    edited = client.put(
        f"{BASE}/items/{item_id}/body", json={"body": "نسخة ثانية"}, headers=as_(AGENCY_ID)
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "draft"
    assert edited.json()["current_body"] == "نسخة ثانية"
    detail = client.get(f"{BASE}/items/{item_id}", headers=owner).json()
    assert [r["revision_no"] for r in detail["revisions"]] == [2, 1]
    assert detail["revisions"][0]["created_by_tenant_id"] == str(AGENCY_ID)
    assert [e["action"] for e in detail["approval_events"]] == [
        "submitted",
        "approved",
        "withdrawn",
    ]
    assert detail["approval_events"][-1]["revision_id"] == detail["revisions"][0]["id"]
    # Editing a plain draft only adds a revision — no event.
    again = client.put(f"{BASE}/items/{item_id}/body", json={"body": "ثالثة"}, headers=owner)
    assert again.status_code == 200 and again.json()["status"] == "draft"
    assert len(content_repo.revisions[UUID(item_id)]) == 3
    assert len(content_repo.events[UUID(item_id)]) == 3
    viewer_edit = client.put(
        f"{BASE}/items/{item_id}/body", json={"body": "x"}, headers=as_(STRANGER_ID)
    )
    assert viewer_edit.status_code == 404  # no grant at all ⇒ brand_not_found


def test_b2_u2_withdraw_and_unschedule_paths(
    client: TestClient, as_: Headers, fake_repo: FakeConnectedAccountRepository
) -> None:
    owner = as_(OWNER_ID)
    item_id = _to_pending(client, owner)
    res = _act(client, owner, item_id, "withdraw", note="rework")
    assert res.status_code == 200 and res.json()["status"] == "draft"
    _active_connection(fake_repo)
    item_id = _to_approved(client, owner)
    when = (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat()
    scheduled = _act(client, owner, item_id, "schedule", scheduled_for=when, target_page_id="1002")
    assert scheduled.status_code == 200, scheduled.text
    assert scheduled.json()["status"] == "scheduled"
    assert scheduled.json()["scheduled_for"] is not None
    assert scheduled.json()["target_page_id"] == "1002"
    back = _act(client, owner, item_id, "unschedule")
    assert back.status_code == 200
    assert back.json()["status"] == "approved" and back.json()["scheduled_for"] is None


# ------------------------------------------------------------ B2-U4 schedule rules --
def test_b2_u4_schedule_validations(
    client: TestClient, as_: Headers, fake_repo: FakeConnectedAccountRepository
) -> None:
    owner = as_(OWNER_ID)
    soon = (datetime.now(tz=UTC) + timedelta(minutes=4)).isoformat()
    later = (datetime.now(tz=UTC) + timedelta(minutes=6)).isoformat()
    fb = _to_approved(client, owner)
    # no connection at all
    res = _act(client, owner, fb, "schedule", scheduled_for=later, target_page_id="1001")
    assert res.status_code == 422 and res.json()["detail"]["error"] == "target_page_not_selected"
    _active_connection(fake_repo)
    res = _act(client, owner, fb, "schedule", scheduled_for=soon, target_page_id="1001")
    assert res.status_code == 422 and res.json()["detail"]["error"] == "schedule_too_soon"
    res = _act(client, owner, fb, "schedule", scheduled_for=later)  # no target at all
    assert res.status_code == 422 and res.json()["detail"]["error"] == "target_page_not_selected"
    res = _act(client, owner, fb, "schedule", scheduled_for=later, target_page_id="1003")
    assert res.status_code == 422 and res.json()["detail"]["error"] == "target_page_not_selected"
    res = _act(client, owner, fb, "schedule", scheduled_for=later, target_page_id="9999")
    assert res.status_code == 422 and res.json()["detail"]["error"] == "target_page_not_selected"
    # instagram channel needs a linked IG account on the page
    ig_item = _create(
        client,
        owner,
        channel="instagram",
        target_page_id="1002",
        media_refs=[{"url": IMAGE_URL}],
    )["id"]
    _act(client, owner, ig_item, "submit")
    _act(client, owner, ig_item, "approve")
    res = _act(client, owner, ig_item, "schedule", scheduled_for=later)
    assert res.status_code == 422 and res.json()["detail"]["error"] == "instagram_not_linked"
    res = _act(client, owner, ig_item, "schedule", scheduled_for=later, target_page_id="1001")
    assert res.status_code == 200 and res.json()["target_page_id"] == "1001"
    # a reauth_required connection does not count as active
    for cid, acc in list(fake_repo.rows.items()):
        fake_repo.rows[cid] = acc.model_copy(update={"status": AccountStatus.REAUTH_REQUIRED})
    res = _act(client, owner, fb, "schedule", scheduled_for=later, target_page_id="1001")
    assert res.status_code == 422 and res.json()["detail"]["error"] == "target_page_not_selected"
    # schedule on a draft is a state error, not a 422
    draft = _create(client, owner)["id"]
    res = _act(client, owner, draft, "schedule", scheduled_for=later, target_page_id="1001")
    assert res.status_code == 409 and res.json()["detail"]["error"] == "invalid_transition"


# ---------------------------------------------- SPRINT-07 B2-U2 publishing scopes --
def test_b2_u2_missing_or_unverified_publish_scopes_require_reconnect(
    client: TestClient, as_: Headers, fake_repo: FakeConnectedAccountRepository
) -> None:
    owner = as_(OWNER_ID)
    later = (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat()
    # (a) scope absent ⇒ 409 with the exact missing list and the reconnect hint.
    cid = _active_connection(fake_repo, scopes=["pages_show_list", "pages_read_engagement"])
    fb = _to_approved(client, owner)
    res = _act(client, owner, fb, "schedule", scheduled_for=later, target_page_id="1002")
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == {
        "error": "reauth_required",
        "message": "missing publishing scopes: pages_manage_posts",
        "required_action": "reconnect",
        "missing_scopes": ["pages_manage_posts"],
    }
    # Instagram lists both publishing scopes it needs.
    ig = _create(client, owner, channel="instagram", media_refs=[{"url": IMAGE_URL}])["id"]
    _act(client, owner, ig, "submit")
    _act(client, owner, ig, "approve")
    res = _act(client, owner, ig, "schedule", scheduled_for=later, target_page_id="1001")
    assert res.status_code == 409
    assert res.json()["detail"]["missing_scopes"] == [
        "pages_manage_posts",
        "instagram_content_publish",
    ]
    # (b) scope granted but **unverified** ⇒ still 409 (unknown ≠ confirmed, SPRINT-06 A-1).
    del fake_repo.rows[cid]
    _active_connection(fake_repo, scopes_verified=False)
    res = _act(client, owner, fb, "schedule", scheduled_for=later, target_page_id="1002")
    assert res.status_code == 409 and res.json()["detail"]["error"] == "reauth_required"
    assert res.json()["detail"]["missing_scopes"] == ["pages_manage_posts"]
    # The item never moved and no job was created.
    assert client.get(f"{BASE}/items/{fb}", headers=owner).json()["status"] == "approved"


# ---------------------------------------------- SPRINT-07 B2-U3 instagram needs image --
@pytest.mark.parametrize(
    "media_refs",
    [
        [],
        [{"url": "http://cdn.example.com/offer.jpg"}],  # not https
        [{"url": "https://cdn.example.com/offer.gif"}],  # not jpg|jpeg|png
        [{"url": "https://cdn.example.com/offer.mp4"}],
    ],
    ids=["no-media", "http", "gif", "mp4"],
)
def test_b2_u3_instagram_schedule_requires_https_image(
    client: TestClient,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
    media_refs: list[dict[str, str]],
) -> None:
    owner = as_(OWNER_ID)
    later = (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat()
    _active_connection(fake_repo)
    ig = _create(client, owner, channel="instagram", media_refs=media_refs)["id"]
    _act(client, owner, ig, "submit")
    _act(client, owner, ig, "approve")
    res = _act(client, owner, ig, "schedule", scheduled_for=later, target_page_id="1001")
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["error"] == "instagram_requires_image"
    # Facebook never needs an image.
    fb = _to_approved(client, owner)
    res = _act(client, owner, fb, "schedule", scheduled_for=later, target_page_id="1002")
    assert res.status_code == 200, res.text


def test_b2_u3_instagram_accepts_https_jpeg_png_with_query_string(
    client: TestClient, as_: Headers, fake_repo: FakeConnectedAccountRepository
) -> None:
    owner = as_(OWNER_ID)
    later = (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat()
    _active_connection(fake_repo)
    for url in ("https://cdn.example.com/a.JPEG?w=1080", "https://cdn.example.com/b.png#x"):
        ig = _create(client, owner, channel="instagram", media_refs=[{"url": url}])["id"]
        _act(client, owner, ig, "submit")
        _act(client, owner, ig, "approve")
        res = _act(client, owner, ig, "schedule", scheduled_for=later, target_page_id="1001")
        assert res.status_code == 200, res.text


# ------------------------------------------- SPRINT-07 B2 schedule creates the job --
def test_b2_schedule_creates_queued_job_and_unschedule_cancels_it(
    client: TestClient,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
    publish_repo,  # noqa: ANN001
) -> None:
    from src.modules.publishing.domain.models import JobStatus

    owner = as_(OWNER_ID)
    when = datetime.now(tz=UTC) + timedelta(hours=1)
    _active_connection(fake_repo)
    item_id = _to_approved(client, owner)
    res = _act(
        client, owner, item_id, "schedule", scheduled_for=when.isoformat(), target_page_id="1002"
    )
    assert res.status_code == 200, res.text
    jobs = [j for j in publish_repo.jobs.values() if str(j.item_id) == item_id]
    assert len(jobs) == 1
    job = jobs[0]
    assert job.status is JobStatus.QUEUED
    assert job.run_at == when
    assert job.channel == "facebook_page" and job.target_page_id == "1002"
    assert str(job.revision_id) == res.json()["current_revision_id"]
    assert job.scheduled_by_tenant_id == OWNER_ID
    # Event ``scheduled`` was recorded with the actor.
    detail = client.get(f"{BASE}/items/{item_id}", headers=owner).json()
    assert [e["action"] for e in detail["approval_events"]][-1] == "scheduled"
    assert detail["approval_events"][-1]["actor_tenant_id"] == str(OWNER_ID)
    # unschedule ⇒ job cancelled + event publish_cancelled.
    res = _act(client, owner, item_id, "unschedule")
    assert res.status_code == 200 and res.json()["status"] == "approved"
    assert publish_repo.jobs[job.id].status is JobStatus.CANCELLED
    detail = client.get(f"{BASE}/items/{item_id}", headers=owner).json()
    assert [e["action"] for e in detail["approval_events"]][-1] == "publish_cancelled"
    # Re-scheduling creates a fresh queued job (the cancelled one is not active).
    res = _act(
        client, owner, item_id, "schedule", scheduled_for=when.isoformat(), target_page_id="1002"
    )
    assert res.status_code == 200, res.text
    active = [j for j in publish_repo.jobs.values() if j.status is JobStatus.QUEUED]
    assert len(active) == 1 and active[0].id != job.id
    # archive from scheduled cancels too.
    res = _act(client, owner, item_id, "archive")
    assert res.status_code == 200 and res.json()["status"] == "archived"
    assert publish_repo.jobs[active[0].id].status is JobStatus.CANCELLED


def test_b2_leased_job_blocks_unschedule_archive_and_edit_with_409(
    client: TestClient,
    store: FakeStore,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
    publish_repo,  # noqa: ANN001
) -> None:
    from src.modules.publishing.domain.models import JobStatus

    owner = as_(OWNER_ID)
    when = datetime.now(tz=UTC) + timedelta(hours=1)
    _active_connection(fake_repo)
    item_id = _to_approved(client, owner)
    assert (
        _act(
            client,
            owner,
            item_id,
            "schedule",
            scheduled_for=when.isoformat(),
            target_page_id="1002",
        ).status_code
        == 200
    )
    (job,) = publish_repo.jobs.values()
    publish_repo.jobs[job.id] = job.model_copy(
        update={
            "status": JobStatus.LEASED,
            "leased_until": when,
            "lease_owner": "worker-1",
            "attempt_count": 1,
        }
    )
    for action in ("unschedule", "archive"):
        res = _act(client, owner, item_id, action)
        assert res.status_code == 409, res.text
        assert res.json()["detail"]["error"] == "publish_in_progress"
    res = client.put(f"{BASE}/items/{item_id}/body", json={"body": "new"}, headers=owner)
    assert res.status_code == 409 and res.json()["detail"]["error"] == "publish_in_progress"
    # Nothing moved: item still scheduled, job still leased, no new event.
    detail = client.get(f"{BASE}/items/{item_id}", headers=owner).json()
    assert detail["status"] == "scheduled" and detail["scheduled_for"] is not None
    assert publish_repo.jobs[job.id].status is JobStatus.LEASED
    assert [e["action"] for e in detail["approval_events"]][-1] == "scheduled"
    # A VIEWER still gets 403 (role before job state).
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    res = _act(client, as_(STRANGER_ID), item_id, "unschedule")
    assert res.status_code == 403


# ---------------------------------------------- SPRINT-07 B3-U1 legacy connections --
def test_b3_u1_legacy_connection_without_publish_scopes_still_reads(
    client: TestClient, as_: Headers, fake_repo: FakeConnectedAccountRepository
) -> None:
    """Older connections (SPRINT-03 scopes only) keep working for every read path and for
    drafting/approval; only ``schedule`` asks for a reconnect."""
    owner = as_(OWNER_ID)
    later = (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat()
    _active_connection(fake_repo, scopes=["pages_show_list", "pages_read_engagement"])
    res = client.get(f"{API}/brands/{BRAND_ID}/social/meta/connections", headers=owner)
    assert res.status_code == 200 and len(res.json()) == 1
    item_id = _to_approved(client, owner)
    assert client.get(f"{BASE}/items/{item_id}", headers=owner).status_code == 200
    assert client.get(f"{BASE}/items", headers=owner).status_code == 200
    res = _act(client, owner, item_id, "schedule", scheduled_for=later, target_page_id="1002")
    assert res.status_code == 409 and res.json()["detail"]["required_action"] == "reconnect"


# ------------------------------------------------------------- B3-U5 archive rules --
def test_b3_u5_manager_archives_only_own_items(
    client: TestClient, store: FakeStore, as_: Headers
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    owner_item = _create(client, as_(OWNER_ID))["id"]
    manager_item = _create(client, as_(AGENCY_ID))["id"]
    res = _act(client, as_(AGENCY_ID), owner_item, "archive")
    assert res.status_code == 403 and res.json()["detail"]["error"] == "insufficient_role"
    assert _act(client, as_(AGENCY_ID), manager_item, "archive").json()["status"] == "archived"
    assert _act(client, as_(OWNER_ID), owner_item, "archive").json()["status"] == "archived"


# ------------------------------------------------------ B3-U6 generation_log visibility --
AI_PROVENANCE = {
    "kind": "ai_generated",
    "generated_at": "2026-10-30T12:00:00+00:00",
    "prompt_version": "content_draft@1",
    "provider": "fake",
    "model": "fake-1",
    "prompt_hash": "abcd1234abcd1234",
    "prompt_tokens": 12,
    "completion_tokens": 30,
    "latency_ms": 7,
    "credential_mode": "platform",
    "truncated": False,
}
PRIVATE_KEYS = {
    "provider",
    "model",
    "prompt_hash",
    "prompt_tokens",
    "completion_tokens",
    "latency_ms",
}


def _add_ai_revision(content_repo: FakeContentRepository, item_id: str, **extra: object) -> None:
    import asyncio
    from datetime import UTC, datetime

    asyncio.run(
        content_repo.add_revision(
            UUID(item_id),
            body="AI text",
            provenance={**AI_PROVENANCE, **extra},
            created_by_tenant_id=OWNER_ID,
            created_at=datetime.now(tz=UTC),
        )
    )


def _walk_keys(node: object) -> set[str]:
    """Every dict key anywhere in a JSON document (recursive)."""
    keys: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            keys.add(k)
            keys |= _walk_keys(v)
    elif isinstance(node, list):
        for v in node:
            keys |= _walk_keys(v)
    return keys


def test_b3_u6_generation_log_removed_for_viewer(
    client: TestClient, store: FakeStore, as_: Headers, content_repo: FakeContentRepository
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    item_id = _create(client, as_(OWNER_ID))["id"]
    _add_ai_revision(content_repo, item_id)
    for tenant in (OWNER_ID, AGENCY_ID):
        body = client.get(f"{BASE}/items/{item_id}", headers=as_(tenant)).json()
        assert body["generation_log"] == [
            {
                "revision_no": 2,
                "generated_at": "2026-10-30T12:00:00Z",
                "prompt_version": "content_draft@1",
                "provider": "fake",
                "model": "fake-1",
                "prompt_hash": "abcd1234abcd1234",
                "prompt_tokens": 12,
                "completion_tokens": 30,
                "latency_ms": 7,
                "credential_mode": "platform",
                "truncated": False,
            }
        ]
    viewer_body = client.get(f"{BASE}/items/{item_id}", headers=as_(STRANGER_ID)).json()
    assert "generation_log" not in viewer_body
    assert viewer_body["revisions"][0]["provenance"]["kind"] == "ai_generated"  # evidence stays


# ------------------------------------------------- SPRINT-07 A1-U1 / A1-U2 (D-066) ------------
def test_a1_u1_viewer_response_has_no_private_provenance_key_anywhere(
    client: TestClient, store: FakeStore, as_: Headers, content_repo: FakeContentRepository
) -> None:
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    item_id = _create(client, as_(OWNER_ID))["id"]
    _add_ai_revision(content_repo, item_id)
    body = client.get(f"{BASE}/items/{item_id}", headers=as_(STRANGER_ID)).json()
    assert _walk_keys(body) & PRIVATE_KEYS == set()
    assert body["revisions"][0]["provenance"] == {
        "kind": "ai_generated",
        "generated_at": "2026-10-30T12:00:00+00:00",
        "prompt_version": "content_draft@1",
    }
    # the list endpoint carries no provenance at all
    listing = client.get(f"{BASE}/items", headers=as_(STRANGER_ID)).json()
    assert _walk_keys(listing) & PRIVATE_KEYS == set()


def test_a1_u2_owner_sees_private_fields_only_in_generation_log(
    client: TestClient, as_: Headers, content_repo: FakeContentRepository
) -> None:
    item_id = _create(client, as_(OWNER_ID))["id"]
    _add_ai_revision(content_repo, item_id)
    body = client.get(f"{BASE}/items/{item_id}", headers=as_(OWNER_ID)).json()
    for rev in body["revisions"]:
        assert set(rev["provenance"]) <= {"kind", "generated_at", "prompt_version"}
    assert PRIVATE_KEYS <= set(body["generation_log"][0])


# ------------------------------------------------------- SPRINT-07 A2-U1 / A2-U2 ------------
@pytest.mark.parametrize(
    ("channel", "length", "expected"),
    [
        ("instagram", 2201, 422),
        ("instagram", 2200, 201),
        ("facebook_page", 5000, 201),
    ],
    ids=["ig-2201-rejected", "ig-2200-ok", "fb-5000-ok"],
)
def test_a2_u1_channel_limit_enforced_on_create_and_update(
    client: TestClient, as_: Headers, channel: str, length: int, expected: int
) -> None:
    owner = as_(OWNER_ID)
    created = client.post(
        f"{BASE}/items", json={"channel": channel, "body": "x" * length}, headers=owner
    )
    assert created.status_code == expected
    if expected == 422:
        assert created.json()["detail"] == {
            "error": "body_too_long",
            "message": f"body of {length} chars exceeds the {channel} limit of 2200",
            "channel": channel,
            "limit": 2200,
            "length": length,
        }
    item_id = _create(client, owner, channel=channel)["id"]
    updated = client.put(
        f"{BASE}/items/{item_id}/body", json={"body": "y" * length}, headers=owner
    )
    assert updated.status_code == (200 if expected == 201 else 422)
    if expected == 422:
        assert updated.json()["detail"]["error"] == "body_too_long"


def test_a2_u2_generate_truncates_to_channel_limit_and_records_it(
    client: TestClient, app: FastAPI, as_: Headers
) -> None:
    from src.modules.ai_gateway.contract import Usage
    from src.modules.ai_gateway.dependencies import get_ai_gateway
    from src.modules.ai_gateway.gateway import CompletionResult

    class LongProvider:
        async def complete(self, req):  # noqa: ANN001, ANN202
            return CompletionResult(
                text="ن" * 2300,
                provider="fake",
                model="fake-1",
                latency_ms=1,
                usage=Usage(prompt_tokens=5, completion_tokens=9),
            )

    app.dependency_overrides[get_ai_gateway] = lambda: LongProvider()
    owner = as_(OWNER_ID)
    item_id = _create(client, owner, channel="instagram")["id"]
    assert _generate(client, owner, item_id).status_code == 200
    detail = client.get(f"{BASE}/items/{item_id}", headers=owner).json()
    assert len(detail["revisions"][0]["body"]) == 2200
    assert detail["generation_log"][0]["truncated"] is True
    assert "truncated" not in detail["revisions"][0]["provenance"]  # private field


# ---------------------------------------------------------------- SPRINT-07 A4-U1 ------------
def test_a4_u1_viewer_detail_keeps_null_fields_and_lacks_generation_log_key(
    client: TestClient, store: FakeStore, as_: Headers
) -> None:
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    item_id = _create(client, as_(OWNER_ID), target_page_id=None)["id"]
    body = client.get(f"{BASE}/items/{item_id}", headers=as_(STRANGER_ID)).json()
    assert "target_page_id" in body and body["target_page_id"] is None
    assert "scheduled_for" in body and body["scheduled_for"] is None
    assert "generation_log" not in body
    owner_body = client.get(f"{BASE}/items/{item_id}", headers=as_(OWNER_ID)).json()
    assert owner_body["generation_log"] == []  # no AI revision yet — empty list, not absent
    assert owner_body["scheduled_for"] is None


# ---------------------------------------------------------------- SPRINT-07 A5-U1 ------------
@pytest.mark.parametrize(("used", "expected"), [(199_900, 200), (200_000, 429)])
def test_a5_u1_daily_token_budget(
    client: TestClient, as_: Headers, content_repo: FakeContentRepository, used: int, expected: int
) -> None:
    content_repo.token_usage_override = used
    owner = as_(OWNER_ID)
    item_id = _create(client, owner)["id"]
    response = _generate(client, owner, item_id)
    assert response.status_code == expected
    if expected == 429:
        detail = response.json()["detail"]
        assert detail["error"] == "generation_budget_exceeded"
        assert detail["budget"] == 200_000
        assert detail["used"] == 200_000
        assert 1 <= detail["resets_in_seconds"] <= 86_400
        assert response.headers["Retry-After"] == str(detail["resets_in_seconds"])


# ---------------------------------------------------------------- SPRINT-07 A6-U1 ------------
def test_a6_u1_prompt_version_is_public_for_every_role(
    client: TestClient, store: FakeStore, as_: Headers
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    owner = as_(OWNER_ID)
    item_id = _create(client, owner)["id"]
    assert _generate(client, owner, item_id).status_code == 200
    for tenant in (OWNER_ID, AGENCY_ID, STRANGER_ID):
        body = client.get(f"{BASE}/items/{item_id}", headers=as_(tenant)).json()
        assert body["revisions"][0]["provenance"]["prompt_version"] == "content_draft@1"


# --------------------------------------------------------------- B3-U7 brand isolation --
def test_b3_u7_other_brands_item_is_404_content_not_found(
    client: TestClient, as_: Headers
) -> None:
    owner = as_(OWNER_ID)
    item_id = _create(client, owner)["id"]
    other = f"{API}/brands/{BRAND2_ID}/content/items/{item_id}"
    res = client.get(other, headers=owner)
    assert res.status_code == 404 and res.json()["detail"]["error"] == "content_not_found"
    res = client.post(f"{other}/submit", headers=owner)
    assert res.status_code == 404 and res.json()["detail"]["error"] == "content_not_found"
    res = client.put(f"{other}/body", json={"body": "x"}, headers=owner)
    assert res.status_code == 404 and res.json()["detail"]["error"] == "content_not_found"
    assert (
        client.get(f"{API}/brands/{BRAND2_ID}/content/items", headers=owner).json()["items"] == []
    )
    # A stranger (no grant) sees brand_not_found, never the item.
    res = client.get(f"{BASE}/items/{item_id}", headers=as_(STRANGER_ID))
    assert res.status_code == 404 and res.json()["detail"]["error"] == "brand_not_found"
    assert client.get(f"{BASE}/items/{uuid4()}", headers=owner).status_code == 404


# ---------------------------------------------------- B3-I1 (fake flavour): the CAS guard --
def test_b3_optimistic_guard_on_fake_repository(
    client: TestClient, as_: Headers, content_repo: FakeContentRepository
) -> None:
    owner = as_(OWNER_ID)
    item_id = _to_pending(client, owner)
    # Someone else moved the row between the read and the UPDATE.
    content_repo.items[UUID(item_id)] = content_repo.items[UUID(item_id)].model_copy(
        update={"status": ContentStatus.APPROVED}
    )
    res = _act(client, owner, item_id, "approve")
    assert res.status_code == 409 and res.json()["detail"]["error"] == "invalid_transition"


def test_openapi_lists_exactly_the_content_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    b = f"{API}/brands/{{brand_id}}/content"
    content_paths = {p for p in paths if "/content/" in p}
    assert content_paths == {
        f"{b}/items",
        f"{b}/items/{{item_id}}",
        f"{b}/items/{{item_id}}/body",
        *(
            f"{b}/items/{{item_id}}/{a}"
            for a in (
                "submit",
                "withdraw",
                "approve",
                "reject",
                "schedule",
                "unschedule",
                "archive",
                "generate",
                "publish",
                "publish/retry",
            )
        ),
    }
    # SPRINT-07 B7-U3: content + publishing = 14 routes (operations) over 13 paths.
    assert sum(len(paths[p]) for p in content_paths) == 14
    assert set(paths[f"{b}/items/{{item_id}}/publish"]) == {"get"}
    assert set(paths[f"{b}/items/{{item_id}}/publish/retry"]) == {"post"}
    assert set(paths[f"{b}/items"]) == {"get", "post"}
    assert set(paths[f"{b}/items/{{item_id}}/body"]) == {"put"}


# ================================================================ SPRINT-06 Part B-2 (B5)
BRIEF = "عرض خصم على القهوة المختصة هذا الأسبوع في فرع المعادي"
FAKE_AI_KEY = "".join(("sk-", "abcdefghijkl"))  # assembled: no secret-shaped literal


def _generate(client: TestClient, headers: dict[str, str], item_id: str, **json: object):
    payload = {"brief": BRIEF, **json}
    return client.post(f"{BASE}/items/{item_id}/generate", json=payload, headers=headers)


# B5-U1 — happy path with the Fake provider: new revision, item stays draft, usage counted.
def test_b5_u1_generate_with_fake_provider_creates_ai_revision(
    client: TestClient, store: FakeStore, as_: Headers, content_repo: FakeContentRepository
) -> None:
    from src.core.metrics import counters

    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    before = counters.get("ai_completions")
    item_id = _create(client, as_(OWNER_ID))["id"]
    res = _generate(client, as_(AGENCY_ID), item_id, tone="friendly", language="en")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "draft"
    assert body["current_body"].startswith("[fake:content_draft:")
    assert BRIEF in body["current_body"]
    revs = content_repo.revisions[UUID(item_id)]
    assert [r.revision_no for r in revs] == [1, 2]
    assert revs[1].created_by_tenant_id == AGENCY_ID
    assert counters.get("ai_completions") == before + 1
    viewer = _generate(client, as_(STRANGER_ID), item_id)
    assert viewer.status_code == 403 and viewer.json()["detail"]["error"] == "insufficient_role"
    # body validation
    assert _generate(client, as_(OWNER_ID), item_id, brief="short").status_code == 422
    assert _generate(client, as_(OWNER_ID), item_id, tone="angry").status_code == 422
    assert _generate(client, as_(OWNER_ID), item_id, language="fr").status_code == 422


# B5-U2 — wrong state ⇒ 409 invalid_transition; rejected ⇒ back to draft.
def test_b5_u2_generate_only_from_draft_or_rejected(client: TestClient, as_: Headers) -> None:
    owner = as_(OWNER_ID)
    pending = _to_pending(client, owner)
    res = _generate(client, owner, pending)
    assert res.status_code == 409
    assert res.json()["detail"] == {
        **res.json()["detail"],
        "error": "invalid_transition",
        "from": "pending_approval",
        "action": "generate",
    }
    approved = _to_approved(client, owner)
    assert _generate(client, owner, approved).json()["detail"]["from"] == "approved"
    rejected = _to_pending(client, owner)
    assert _act(client, owner, rejected, "reject", note="claims").status_code == 200
    res = _generate(client, owner, rejected)
    assert res.status_code == 200 and res.json()["status"] == "draft"
    detail = client.get(f"{BASE}/items/{rejected}", headers=owner).json()
    assert [r["revision_no"] for r in detail["revisions"]] == [2, 1]
    assert detail["revisions"][0]["provenance"]["kind"] == "ai_generated"
    archived = _create(client, owner)["id"]
    _act(client, owner, archived, "archive")
    assert _generate(client, owner, archived).status_code == 409


# B5-U3 — 20 generations / hour / brand through the existing RateLimiter ⇒ 429 + Retry-After.
def test_b5_u3_generation_rate_limit_per_brand(
    client: TestClient, as_: Headers, limiter: FakeRateLimiter
) -> None:
    owner = as_(OWNER_ID)
    item_id = _create(client, owner)["id"]
    limiter.reject_after = 2
    assert _generate(client, owner, item_id).status_code == 200
    assert _generate(client, owner, item_id).status_code == 200
    res = _generate(client, owner, item_id)
    assert res.status_code == 429, res.text
    assert res.json()["detail"]["error"] == "generation_rate_limited"
    assert res.headers["Retry-After"] == "3600"
    assert limiter.hits == [("ai_generate_brand", str(BRAND_ID))] * 3
    # The limit is scoped to the brand, not the item: another brand's key is different.
    from src.modules.content.service import GENERATION_LIMIT_PER_HOUR

    assert GENERATION_LIMIT_PER_HOUR == 20


# B5-U4 — provenance evidence: provider, model, prompt_hash = sha256(system+user)[:16],
# generated_at; plus the usage log never contains the prompt or the output text.
def test_b5_u4_provenance_and_usage_log(
    client: TestClient,
    as_: Headers,
    content_repo: FakeContentRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from src.modules.content.domain.models import Channel
    from src.modules.content.prompts import prompt_hash, system_prompt, user_prompt

    owner = as_(OWNER_ID)
    item_id = _create(client, owner, channel="instagram")["id"]
    caplog.set_level(logging.INFO, logger="src.modules.content")
    res = _generate(client, owner, item_id, tone="promotional", language="ar")
    assert res.status_code == 200, res.text
    revision = content_repo.revisions[UUID(item_id)][-1]
    expected_hash = prompt_hash(
        system_prompt(Channel.INSTAGRAM, tone="promotional", language="ar"), user_prompt(BRIEF)
    )
    assert revision.provenance == {
        **revision.provenance,
        "kind": "ai_generated",
        "provider": "fake",
        "model": "fake-1",
        "prompt_hash": expected_hash,
    }
    assert len(revision.provenance["prompt_hash"]) == 16
    datetime.fromisoformat(revision.provenance["generated_at"])  # parseable, tz-aware
    assert revision.provenance["generated_at"].endswith("+00:00")
    assert isinstance(revision.provenance["prompt_tokens"], int)
    usage = [r for r in caplog.records if r.getMessage().startswith("ai completion:")]
    assert len(usage) == 1
    line = usage[0].getMessage()
    assert f"brand={BRAND_ID}" in line and "provider=fake" in line and "model=fake-1" in line
    assert BRIEF not in caplog.text and revision.body not in caplog.text
    # generation_log is visible to the owner with the token counts.
    detail = client.get(f"{BASE}/items/{item_id}", headers=owner).json()
    assert detail["generation_log"][0]["prompt_hash"] == expected_hash
    assert detail["generation_log"][0]["revision_no"] == 2
    # The system prompt pins channel + limit + D-055 rule.
    system = system_prompt(Channel.INSTAGRAM, tone="promotional", language="ar")
    assert "Instagram" in system and "2200" in system and "D-055" in system
    fb = system_prompt(Channel.FACEBOOK_PAGE, tone="professional", language="en")
    assert "Facebook Page" in fb and "5000" in fb and "English" in fb


def test_b5_gateway_errors_map_to_http_by_category(
    client: TestClient,
    as_: Headers,
    app,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    from pydantic import SecretStr
    from src.core.config import get_settings
    from src.modules.ai_gateway.contract import AIError, ErrorCategory
    from src.modules.ai_gateway.dependencies import get_ai_gateway

    owner = as_(OWNER_ID)
    item_id = _create(client, owner)["id"]

    class Boom:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

        async def complete(self, req):  # noqa: ANN001, ANN202
            raise self.exc

    # B-0 (D-067): one HTTP map for the twelve categories.
    expectations = {
        ErrorCategory.RATE_LIMITED: (429, "ai_rate_limited"),
        ErrorCategory.QUOTA_EXCEEDED: (429, "ai_quota_exceeded"),
        ErrorCategory.INVALID_CREDENTIAL: (503, "ai_provider_misconfigured"),
        ErrorCategory.AUTH_EXPIRED: (503, "ai_provider_misconfigured"),
        ErrorCategory.CONTENT_REJECTED: (422, "ai_content_rejected"),
        ErrorCategory.MODEL_UNAVAILABLE: (502, "ai_upstream_unavailable"),
        ErrorCategory.PROVIDER_UNAVAILABLE: (502, "ai_upstream_unavailable"),
        ErrorCategory.UNSUPPORTED_CAPABILITY: (502, "ai_upstream_unavailable"),
        ErrorCategory.BAD_REQUEST: (502, "ai_upstream_unavailable"),
        ErrorCategory.TIMEOUT: (502, "ai_upstream_unavailable"),
        ErrorCategory.RETRYABLE_SERVER_ERROR: (502, "ai_upstream_unavailable"),
        ErrorCategory.NON_RETRYABLE_ERROR: (502, "ai_upstream_unavailable"),
    }
    assert set(expectations) == set(ErrorCategory)
    for category, (code, error) in expectations.items():
        app.dependency_overrides[get_ai_gateway] = lambda c=category: Boom(
            AIError(c, retry_after_ms=42_000, provider_code="x")
        )
        res = _generate(client, owner, item_id)
        assert (res.status_code, res.json()["detail"]) == (
            code,
            {"error": error, "category": category.value},
        ), category
        if category is ErrorCategory.RATE_LIMITED:
            assert res.headers["Retry-After"] == "42"
        else:
            assert "Retry-After" not in res.headers
    app.dependency_overrides.pop(get_ai_gateway)
    # 503 when openai_compatible is selected without a key (real dependency path).
    base = app.dependency_overrides.get(get_settings)
    current = base() if base else get_settings()
    app.dependency_overrides[get_settings] = lambda: current.model_copy(
        update={"ai_provider": "openai_compatible", "ai_provider_api_key": None}
    )
    try:
        res = _generate(client, owner, item_id)
        assert res.status_code == 503, res.text
        assert res.json()["detail"]["error"] == "ai_provider_not_configured"
        # and with a key the real HTTP provider is built (respx-free smoke: 502 on no network)
        app.dependency_overrides[get_settings] = lambda: current.model_copy(
            update={
                "ai_provider": "openai_compatible",
                "ai_provider_api_key": SecretStr(FAKE_AI_KEY),
                "ai_provider_base_url": "http://127.0.0.1:9/v1",
                "ai_http_timeout_seconds": 0.2,
            }
        )
        res = _generate(client, owner, item_id)
        assert res.status_code == 502 and res.json()["detail"] == {
            "error": "ai_upstream_unavailable",
            "category": "provider_unavailable",  # connection refused ⇒ D-067 category
        }
        assert FAKE_AI_KEY not in res.text
    finally:
        if base:
            app.dependency_overrides[get_settings] = base
        else:
            app.dependency_overrides.pop(get_settings, None)
    # No content was written by any of the failures.
    detail = client.get(f"{BASE}/items/{item_id}", headers=owner).json()
    assert [r["revision_no"] for r in detail["revisions"]] == [1]


# ------------------------------------------------------- SPRINT-07 B7 publish status/retry --
def _schedule_fb(client: TestClient, owner: dict[str, str], fake_repo, publish_repo):  # noqa: ANN001, ANN201
    """approved → scheduled on Page 1002 with a queued job; returns ``(item_id, job)``."""
    _active_connection(fake_repo)
    item_id = _to_approved(client, owner)
    when = datetime.now(tz=UTC) + timedelta(hours=1)
    res = _act(
        client, owner, item_id, "schedule", scheduled_for=when.isoformat(), target_page_id="1002"
    )
    assert res.status_code == 200, res.text
    job = next(j for j in publish_repo.jobs.values() if str(j.item_id) == item_id)
    return item_id, job


async def _fail_job(  # noqa: ANN202
    publish_repo, content_repo: FakeContentRepository, job, *, error_class, ambiguous: bool = False
):  # noqa: ANN001
    """Simulate the worker: lease, attempt, then ``failed`` + item ``publish_failed``."""
    from src.modules.publishing.domain.models import AttemptOutcome, ErrorClass, PublishAttempt

    now = datetime.now(tz=UTC)
    leased = await publish_repo.lease_due_jobs(
        now=job.run_at + timedelta(seconds=1), lease_until=now, owner="t:1", batch=10
    )
    assert [j.id for j in leased] == [job.id]
    attempt = await publish_repo.start_attempt(
        PublishAttempt(job_id=job.id, attempt_no=1, started_at=now)
    )
    await publish_repo.finish_attempt(
        attempt.id,
        outcome=AttemptOutcome.AMBIGUOUS if ambiguous else AttemptOutcome.PERMANENT_FAILURE,
        finished_at=now,
        error_class=error_class,
        graph_error_code=None if ambiguous else 10,
        graph_trace_id=None if ambiguous else "AbCdTrace",
    )
    from src.modules.publishing.domain.models import JobStatus

    await publish_repo.complete_job(
        job.id, status=JobStatus.FAILED, now=now, last_error_class=error_class
    )
    assert await content_repo.compare_and_set_status(
        job.item_id,
        expected=ContentStatus.SCHEDULED,
        new=ContentStatus.PUBLISH_FAILED,
        scheduled_for=None,
        updated_at=now,
    )
    assert error_class is not ErrorClass.TOKEN_INVALID or True


def test_b7_u1_publish_status_visibility_by_role(
    client: TestClient,
    store: FakeStore,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
    content_repo: FakeContentRepository,
    publish_repo,  # noqa: ANN001
) -> None:
    import asyncio

    from src.modules.publishing.domain.models import ErrorClass

    owner = as_(OWNER_ID)
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    item_id, job = _schedule_fb(client, owner, fake_repo, publish_repo)

    # Queued, no attempts yet — every role sees the job; VIEWER without the detail fields.
    res = client.get(f"{BASE}/items/{item_id}/publish", headers=owner)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["job_id"] == str(job.id) and body["status"] == "queued"
    assert body["attempts"] == [] and body["scheduled_by_tenant_id"] == str(OWNER_ID)

    asyncio.run(
        _fail_job(publish_repo, content_repo, job, error_class=ErrorClass.PERMISSION_DENIED)
    )

    for tenant in (OWNER_ID, AGENCY_ID):
        detail = client.get(f"{BASE}/items/{item_id}/publish", headers=as_(tenant)).json()
        assert detail["status"] == "failed"
        assert detail["last_error_class"] == "permission_denied"
        assert detail["attempts"][0]["error_class"] == "permission_denied"
        assert detail["attempts"][0]["graph_error_code"] == 10
        assert detail["attempts"][0]["graph_trace_id"] == "AbCdTrace"
        assert detail["attempts"][0]["outcome"] == "permanent_failure"
    viewer = client.get(f"{BASE}/items/{item_id}/publish", headers=as_(STRANGER_ID))
    assert viewer.status_code == 200
    vbody = viewer.json()
    for hidden in (
        "last_error_class",
        "external_post_id",
        "lease_owner",
        "scheduled_by_tenant_id",
    ):
        assert hidden not in vbody
    assert set(vbody["attempts"][0]) == {"attempt_no", "started_at", "finished_at", "outcome"}
    assert vbody["attempts"][0]["outcome"] == "permanent_failure"
    # Never Meta's message text anywhere.
    assert "LEAK" not in viewer.text and "message" not in vbody["attempts"][0]


def test_b7_u1_publish_status_404_when_never_scheduled_and_for_strangers(
    client: TestClient, as_: Headers
) -> None:
    owner = as_(OWNER_ID)
    item_id = _create(client, owner)["id"]
    res = client.get(f"{BASE}/items/{item_id}/publish", headers=owner)
    assert res.status_code == 404 and res.json()["detail"]["error"] == "no_publish_job"
    assert (
        client.get(f"{BASE}/items/{item_id}/publish", headers=as_(STRANGER_ID)).status_code == 404
    )
    assert client.get(f"{BASE}/items/{uuid4()}/publish", headers=owner).status_code == 404


def test_b7_retry_requeues_non_ambiguous_failure(
    client: TestClient,
    store: FakeStore,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
    content_repo: FakeContentRepository,
    publish_repo,  # noqa: ANN001
) -> None:
    import asyncio

    from src.modules.publishing.domain.models import ErrorClass, JobStatus

    owner = as_(OWNER_ID)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    item_id, job = _schedule_fb(client, owner, fake_repo, publish_repo)
    asyncio.run(_fail_job(publish_repo, content_repo, job, error_class=ErrorClass.RATE_LIMITED))
    assert (
        client.get(f"{BASE}/items/{item_id}", headers=owner).json()["status"] == "publish_failed"
    )

    # VIEWER may not retry; not-yet-failed items are 409 invalid_transition (checked below).
    assert _act(client, as_(STRANGER_ID), item_id, "publish/retry").status_code == 403

    before = datetime.now(tz=UTC)
    res = _act(client, owner, item_id, "publish/retry")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "scheduled"
    jobs = sorted(
        (j for j in publish_repo.jobs.values() if str(j.item_id) == item_id),
        key=lambda j: j.created_at,
    )
    assert [j.status for j in jobs] == [JobStatus.FAILED, JobStatus.QUEUED]
    assert before <= jobs[1].run_at <= datetime.now(tz=UTC)
    assert jobs[1].attempt_count == 0 and jobs[1].scheduled_by_tenant_id == OWNER_ID
    # New ``scheduled`` event with the actor (D-055), and the status endpoint shows the new job.
    events = client.get(f"{BASE}/items/{item_id}", headers=owner).json()["approval_events"]
    assert events[-1]["action"] == "scheduled" and events[-1]["note"] == "publish_retry"
    status = client.get(f"{BASE}/items/{item_id}/publish", headers=owner).json()
    assert status["job_id"] == str(jobs[1].id) and status["status"] == "queued"
    # A second retry while the new job is active ⇒ 409 invalid_transition (item is scheduled).
    again = _act(client, owner, item_id, "publish/retry")
    assert again.status_code == 409 and again.json()["detail"]["error"] == "invalid_transition"


def test_b7_u2_retry_after_ambiguous_outcome_is_409_manual_verification(
    client: TestClient,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
    content_repo: FakeContentRepository,
    publish_repo,  # noqa: ANN001
) -> None:
    import asyncio

    from src.modules.publishing.domain.models import ErrorClass

    owner = as_(OWNER_ID)
    item_id, job = _schedule_fb(client, owner, fake_repo, publish_repo)
    asyncio.run(
        _fail_job(
            publish_repo,
            content_repo,
            job,
            error_class=ErrorClass.AMBIGUOUS_OUTCOME,
            ambiguous=True,
        )
    )
    res = _act(client, owner, item_id, "publish/retry")
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["error"] == "manual_verification_required"
    # Nothing changed: still publish_failed, still one (failed) job.
    assert (
        client.get(f"{BASE}/items/{item_id}", headers=owner).json()["status"] == "publish_failed"
    )
    assert len([j for j in publish_repo.jobs.values() if str(j.item_id) == item_id]) == 1
    # The operator's way out: unschedule (publish_failed → approved) or archive still work.
    assert _act(client, owner, item_id, "unschedule").status_code == 200


def test_b7_retry_revalidates_target_like_schedule(
    client: TestClient,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
    content_repo: FakeContentRepository,
    publish_repo,  # noqa: ANN001
) -> None:
    import asyncio

    from src.modules.publishing.domain.models import ErrorClass

    owner = as_(OWNER_ID)
    item_id, job = _schedule_fb(client, owner, fake_repo, publish_repo)
    asyncio.run(_fail_job(publish_repo, content_repo, job, error_class=ErrorClass.TOKEN_INVALID))
    # The worker marked the connection reauth_required (B6-I5) ⇒ retry is refused until reconnect.
    cid = next(iter(fake_repo.rows))
    fake_repo.rows[cid] = fake_repo.rows[cid].model_copy(
        update={"status": AccountStatus.REAUTH_REQUIRED}
    )
    res = _act(client, owner, item_id, "publish/retry")
    assert res.status_code == 422 and res.json()["detail"]["error"] == "target_page_not_selected"
    # Active again but scopes unverified ⇒ 409 reauth_required.
    fake_repo.rows[cid] = fake_repo.rows[cid].model_copy(
        update={"status": AccountStatus.ACTIVE, "scopes_verified": False}
    )
    res = _act(client, owner, item_id, "publish/retry")
    assert res.status_code == 409 and res.json()["detail"]["error"] == "reauth_required"


# ------------------------------------------------------------ SPRINT-08 A-3 PUBLISH_MAX_ATTEMPTS
def test_a3_u1_publish_max_attempts_setting_is_written_into_new_job(
    app: FastAPI,
    client: TestClient,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
    publish_repo,  # noqa: ANN001
) -> None:
    """A-3: ``PUBLISH_MAX_ATTEMPTS=5`` ⇒ the job created by ``schedule`` has
    ``max_attempts == 5`` (policy at creation, written through ``_job_for``)."""
    from src.core.config import get_settings

    base = app.dependency_overrides.get(get_settings)
    current = base() if base else get_settings()
    assert current.publish_max_attempts == 3  # default documented in A-3
    app.dependency_overrides[get_settings] = lambda: current.model_copy(
        update={"publish_max_attempts": 5}
    )
    try:
        _, job = _schedule_fb(client, as_(OWNER_ID), fake_repo, publish_repo)
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert job.max_attempts == 5


def test_a3_u2_changing_setting_after_creation_does_not_touch_existing_job(
    app: FastAPI,
    client: TestClient,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
    publish_repo,  # noqa: ANN001
) -> None:
    """A-3: the row is the truth — a job created under ``max_attempts=3`` keeps 3 after the
    setting changes to 1, and the scheduler honours the row (retryable ⇒ requeued, not failed)."""
    import asyncio

    from src.core.config import get_settings
    from src.modules.publishing.domain.models import ErrorClass, JobStatus

    owner = as_(OWNER_ID)
    item_id, job = _schedule_fb(client, owner, fake_repo, publish_repo)
    assert job.max_attempts == 3
    base = app.dependency_overrides.get(get_settings)
    current = base() if base else get_settings()
    app.dependency_overrides[get_settings] = lambda: current.model_copy(
        update={"publish_max_attempts": 1}
    )
    try:
        # The existing job is untouched by the setting change …
        assert publish_repo.jobs[job.id].max_attempts == 3
        # … and a *new* job on another item picks the new policy.
        _, fresh = _schedule_fb(client, owner, fake_repo, publish_repo)
        assert fresh.max_attempts == 1 and fresh.id != job.id
    finally:
        app.dependency_overrides.pop(get_settings, None)

    # The scheduler decides from ``job.max_attempts`` only (never from settings).
    async def _run() -> None:
        now = datetime.now(tz=UTC) + timedelta(hours=2)
        leased = await publish_repo.lease_due_jobs(
            now=now, lease_until=now + timedelta(minutes=2), owner="unit:a3", batch=10
        )
        old = next(j for j in leased if j.id == job.id)
        assert old.attempt_count == 1 and old.attempt_count < old.max_attempts  # 1 < 3
        assert await publish_repo.complete_job(
            old.id,
            status=JobStatus.QUEUED,
            now=now,
            last_error_class=ErrorClass.TRANSIENT,
            next_attempt_at=now + timedelta(seconds=60),
        )

    asyncio.run(_run())
    assert publish_repo.jobs[job.id].status is JobStatus.QUEUED
    assert publish_repo.jobs[job.id].max_attempts == 3


def test_a3_u3_publish_max_attempts_zero_is_rejected_by_settings() -> None:
    """A-3: ``PUBLISH_MAX_ATTEMPTS=0`` (and 11) ⇒ ``ValidationError`` (``ge=1, le=10``)."""
    from pydantic import ValidationError
    from src.core.config import Settings

    for bad in (0, 11):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, publish_max_attempts=bad)  # type: ignore[call-arg]
    assert Settings(_env_file=None, publish_max_attempts=10).publish_max_attempts == 10  # type: ignore[call-arg]


# ------------------------------------------------------------ SPRINT-08 A-4 (= SPRINT-07 B7-U3)
def test_b7_u3_protected_route_count_and_openapi_operations(client: TestClient) -> None:
    """B7-U3 under its own name (A-4): the auth guard covers every non-auth route
    (``PROTECTED_ROUTE_COUNT`` imported from ``tests/test_auth_guard.py``, not repeated) and
    the content + publishing module exposes exactly 14 operations over 13 paths."""
    from tests.test_auth_guard import PROTECTED, PROTECTED_ROUTE_COUNT

    assert PROTECTED_ROUTE_COUNT == 43, PROTECTED  # SPRINT-09 B-2: 34 + 9 whatsapp routes
    paths = client.get("/openapi.json").json()["paths"]
    content_paths = {p for p in paths if "/content/" in p}
    assert len(content_paths) == 13
    assert sum(len(paths[p]) for p in content_paths) == 14
    # Every content path is in the guard's protected list (``PROTECTED`` = ``(METHOD, path)``).
    assert content_paths <= {path for _method, path in PROTECTED}


# ------------------------------------------------ SPRINT-08 B-2 channels API + 422 --
def test_channels_endpoint_shape_and_no_secrets(client: TestClient, as_: Headers) -> None:
    """B2-U2 — ``GET /channels`` for any authenticated user: five items, the documented
    shape, no key anywhere that smells like a secret, no brand data; ``/{channel}`` mirrors
    one item and an unknown value is 404 ``unknown_channel``; anonymous ⇒ 401."""
    res = client.get(f"{API}/channels", headers=as_(STRANGER_ID))  # any user, no brand role
    assert res.status_code == 200, res.text
    channels = res.json()["channels"]
    assert [c["channel"] for c in channels] == [
        "facebook_page",
        "instagram",
        "linkedin",
        "x",
        "tiktok",
    ]
    assert {c["activation_status"] for c in channels if c["provider"] == "meta"} == {"active"}
    assert {c["activation_status"] for c in channels if c["provider"] != "meta"} == {
        "not_configured"
    }
    expected_keys = {
        "channel",
        "display_name",
        "provider",
        "activation_status",
        "capabilities",
        "required_scopes",
        "activation_requirements",
    }

    def _walk(node: object, path: str = "") -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                found.append(f"{path}.{key}")
                found += _walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for value in node:
                found += _walk(value, path)
        return found

    for item in channels:
        assert set(item) == expected_keys, item
        assert set(item["capabilities"]) == {
            "text_limit",
            "requires_media",
            "max_media",
            "media_kinds",
            "supports_link",
            "supports_scheduling",
        }
        for req in item["activation_requirements"]:
            assert set(req) == {"key", "title", "owner", "status", "note"}
    # No key anywhere in the payload contains token|secret|key — ``requirement.key`` is the
    # row label (``developer_app`` …), the only accepted exception; no secret-like values.
    keys = _walk(res.json())
    suspicious = [
        k
        for k in keys
        if any(w in k.rsplit(".", 1)[-1].lower() for w in ("token", "secret", "key"))
        and not k.endswith(".activation_requirements.key")
    ]
    assert suspicious == []
    text = res.text.lower()
    assert "gaaaaa" not in text and str(BRAND_ID) not in text and "nile" not in text
    assert "secret" not in text and "token" not in text

    one = client.get(f"{API}/channels/x", headers=as_(STRANGER_ID))
    assert one.status_code == 200 and one.json() == channels[3]
    assert one.json()["capabilities"]["text_limit"] == 280
    missing = client.get(f"{API}/channels/myspace", headers=as_(STRANGER_ID))
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"] == "unknown_channel"
    assert client.get(f"{API}/channels").status_code == 401


def test_schedule_on_not_configured_channel_is_422_before_connection_checks(
    client: TestClient,
    as_: Headers,
    fake_repo: FakeConnectedAccountRepository,
    publish_repo,  # noqa: ANN001
) -> None:
    """B2-U3 — brand with **no** connection + approved ``x`` item ⇒ ``schedule`` is 422
    ``channel_not_activated`` (not 409 / not ``target_page_not_selected``), the payload lists
    only incomplete requirements (``developer_account`` among them), no job is created; the
    draft/submit/approve path for a structural channel is untouched (201/200/200)."""
    owner = as_(OWNER_ID)
    later = (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat()
    assert fake_repo.rows == {}
    created = client.post(
        f"{BASE}/items", json={"channel": "x", "body": "short enough for x"}, headers=owner
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]
    assert _act(client, owner, item_id, "submit").status_code == 200
    assert _act(client, owner, item_id, "approve").status_code == 200
    # Even with a page id and no connection: the activation check comes first.
    res = _act(client, owner, item_id, "schedule", scheduled_for=later, target_page_id="1001")
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert detail["error"] == "channel_not_activated"
    assert detail["channel"] == "x"
    keys = {r["key"] for r in detail["requirements"]}
    assert "developer_account" in keys
    assert all(r["status"] in {"pending", "not_started"} for r in detail["requirements"])
    assert all(
        set(r) == {"key", "title", "owner", "status", "note"} for r in detail["requirements"]
    )
    assert publish_repo.jobs == {}
    # Still approved (no state change), and also 422 with a connection present.
    assert client.get(f"{BASE}/items/{item_id}", headers=owner).json()["status"] == "approved"
    _active_connection(fake_repo)
    res = _act(client, owner, item_id, "schedule", scheduled_for=later, target_page_id="1002")
    assert res.status_code == 422 and res.json()["detail"]["error"] == "channel_not_activated"
    # linkedin: x limit is 280 — a 281-char body on ``x`` is body_too_long from the registry.
    too_long = client.post(
        f"{BASE}/items", json={"channel": "x", "body": "a" * 281}, headers=owner
    )
    assert too_long.status_code == 422 and too_long.json()["detail"]["limit"] == 280
    li = client.post(f"{BASE}/items", json={"channel": "linkedin", "body": BODY}, headers=owner)
    assert li.status_code == 201 and li.json()["channel"] == "linkedin"


def test_meta_channels_schedule_path_unchanged(
    client: TestClient, as_: Headers, fake_repo: FakeConnectedAccountRepository
) -> None:
    """B2-U4 — the three SPRINT-07 B2-U3 scenarios keep their codes after the registry move:
    (1) Instagram without an https image ⇒ 422 ``instagram_requires_image``; (2) Instagram
    with https jpeg + query ⇒ 200; (3) Facebook without media ⇒ 200 — plus the SPRINT-07
    B2-U2 409 ``reauth_required`` list for a connection lacking the publishing scopes."""
    owner = as_(OWNER_ID)
    later = (datetime.now(tz=UTC) + timedelta(hours=1)).isoformat()
    _active_connection(fake_repo)
    # (1)
    ig = _create(
        client, owner, channel="instagram", media_refs=[{"url": "http://x.example/a.jpg"}]
    )
    _act(client, owner, ig["id"], "submit")
    _act(client, owner, ig["id"], "approve")
    res = _act(client, owner, ig["id"], "schedule", scheduled_for=later, target_page_id="1001")
    assert res.status_code == 422 and res.json()["detail"]["error"] == "instagram_requires_image"
    # (2)
    ig2 = _create(
        client,
        owner,
        channel="instagram",
        media_refs=[{"url": "https://cdn.example.com/a.JPEG?w=1"}],
    )
    _act(client, owner, ig2["id"], "submit")
    _act(client, owner, ig2["id"], "approve")
    res = _act(client, owner, ig2["id"], "schedule", scheduled_for=later, target_page_id="1001")
    assert res.status_code == 200, res.text
    # (3)
    fb = _to_approved(client, owner)
    res = _act(client, owner, fb, "schedule", scheduled_for=later, target_page_id="1002")
    assert res.status_code == 200, res.text
    # scopes still come from the registry definition (facebook_page ⇒ pages_manage_posts).
    for cid, acc in list(fake_repo.rows.items()):
        fake_repo.rows[cid] = acc.model_copy(update={"granted_scopes": ["pages_show_list"]})
    fb2 = _to_approved(client, owner)
    res = _act(client, owner, fb2, "schedule", scheduled_for=later, target_page_id="1002")
    assert res.status_code == 409
    assert res.json()["detail"]["missing_scopes"] == ["pages_manage_posts"]


# ================================================================ SPRINT-09 A-3 (F3) ===
# ``generate()`` keeps the current revision's ``media_refs``; ``PUT body`` without
# ``media_refs`` keeps them, with ``[]`` removes them; the row lock is taken **after** the
# gateway call and the state is re-checked under it.
IMG = "https://cdn.example.com/a3.jpg"
IMG_REF = {"url": IMG, "kind": "image"}  # ``MediaRef`` default kind


def test_a3_u1_generate_keeps_media_refs_of_current_revision(
    client: TestClient, as_: Headers
) -> None:
    owner = as_(OWNER_ID)
    item_id = _create(client, owner, channel="instagram", media_refs=[{"url": IMG}])["id"]
    assert _generate(client, owner, item_id).status_code == 200
    detail = client.get(f"{BASE}/items/{item_id}", headers=owner).json()
    assert [r["revision_no"] for r in detail["revisions"]] == [2, 1]
    assert detail["revisions"][0]["media_refs"] == [IMG_REF]
    assert detail["revisions"][0]["provenance"]["kind"] == "ai_generated"


def test_a3_u2_put_body_without_media_refs_keeps_them_and_empty_list_removes_them(
    client: TestClient, as_: Headers
) -> None:
    owner = as_(OWNER_ID)
    item_id = _create(client, owner, channel="instagram", media_refs=[{"url": IMG}])["id"]
    kept = client.put(f"{BASE}/items/{item_id}/body", json={"body": "نص جديد"}, headers=owner)
    assert kept.status_code == 200, kept.text
    detail = client.get(f"{BASE}/items/{item_id}", headers=owner).json()
    assert detail["revisions"][0]["revision_no"] == 2
    assert detail["revisions"][0]["media_refs"] == [IMG_REF]

    removed = client.put(
        f"{BASE}/items/{item_id}/body", json={"body": "بدون صورة", "media_refs": []}, headers=owner
    )
    assert removed.status_code == 200, removed.text
    detail = client.get(f"{BASE}/items/{item_id}", headers=owner).json()
    assert detail["revisions"][0]["revision_no"] == 3
    assert detail["revisions"][0]["media_refs"] == []

    # explicit ``null`` behaves like omission (keep — here: keep the now-empty list)
    nulled = client.put(
        f"{BASE}/items/{item_id}/body",
        json={"body": "مرة أخرى", "media_refs": None},
        headers=owner,
    )
    assert nulled.status_code == 200, nulled.text
    assert (
        client.get(f"{BASE}/items/{item_id}", headers=owner).json()["revisions"][0]["media_refs"]
        == []
    )


def test_a3_u3_generate_recheck_after_gateway_returns_409_and_writes_no_revision(
    client: TestClient, app: FastAPI, as_: Headers, content_repo: FakeContentRepository
) -> None:
    """The gateway call happens *without* the row lock; if the item left ``draft`` meanwhile
    (here: moved to ``pending_approval`` inside ``complete()``), the re-check under the lock
    raises 409 ``invalid_transition`` and **no** revision is written."""
    from src.modules.ai_gateway.contract import Usage
    from src.modules.ai_gateway.dependencies import get_ai_gateway
    from src.modules.ai_gateway.gateway import CompletionResult
    from src.modules.content.domain.models import ContentStatus

    owner = as_(OWNER_ID)
    item_id = _create(client, owner)["id"]
    uid = UUID(item_id)

    class MovingGateway:
        async def complete(self, req):  # noqa: ANN001, ANN202
            # Simulate a concurrent ``submit`` while the completion is in flight.
            current = content_repo.items[uid]
            content_repo.items[uid] = current.model_copy(
                update={"status": ContentStatus.PENDING_APPROVAL}
            )
            return CompletionResult(
                text="generated",
                provider="fake",
                model="fake-1",
                latency_ms=1,
                usage=Usage(prompt_tokens=1, completion_tokens=1),
            )

    app.dependency_overrides[get_ai_gateway] = lambda: MovingGateway()
    try:
        res = _generate(client, owner, item_id)
    finally:
        app.dependency_overrides.pop(get_ai_gateway)
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["error"] == "invalid_transition"
    assert res.json()["detail"]["from"] == "pending_approval"
    assert len(content_repo.revisions[uid]) == 1  # only the human revision
