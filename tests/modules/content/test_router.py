"""SPRINT-06 B3 — content routes against the fakes (roles, visibility, lifecycle, isolation)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
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
from tests.fakes import FakeConnectedAccountRepository, FakeContentRepository, FakeStore

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


def _active_connection(fake_repo: FakeConnectedAccountRepository) -> None:
    fake_repo.rows[uuid4()] = ConnectedAccount(
        brand_id=BRAND_ID,
        connected_by_tenant_id=OWNER_ID,
        provider_user_id="90001",
        encrypted_user_token="gAAAAA-user",
        status=AccountStatus.ACTIVE,
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
    assert (
        client.post(
            f"{BASE}/items", json={"channel": "tiktok", "body": BODY}, headers=owner
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
    assert (
        client.post(
            f"{BASE}/items", json={"channel": "instagram", "body": "x" * 5000}, headers=owner
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
    ig_item = _create(client, owner, channel="instagram", target_page_id="1002")["id"]
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
def test_b3_u6_generation_log_removed_for_viewer(
    client: TestClient, store: FakeStore, as_: Headers, content_repo: FakeContentRepository
) -> None:
    from src.modules.content.domain.models import ContentRevision

    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    item_id = _create(client, as_(OWNER_ID))["id"]
    # Simulate an AI revision (Part B-2 writes exactly this shape).
    import asyncio

    asyncio.run(
        content_repo.add_revision(
            ContentRevision(
                item_id=UUID(item_id),
                revision_no=2,
                body="AI text",
                provenance={
                    "kind": "ai_generated",
                    "provider": "fake",
                    "model": "fake-1",
                    "prompt_hash": "abcd1234abcd1234",
                    "generated_at": "2026-10-30T12:00:00+00:00",
                    "prompt_tokens": 12,
                    "completion_tokens": 30,
                },
                created_by_tenant_id=OWNER_ID,
            )
        )
    )
    for tenant in (OWNER_ID, AGENCY_ID):
        body = client.get(f"{BASE}/items/{item_id}", headers=as_(tenant)).json()
        assert body["generation_log"] == [
            {
                "provider": "fake",
                "model": "fake-1",
                "prompt_hash": "abcd1234abcd1234",
                "generated_at": "2026-10-30T12:00:00+00:00",
                "prompt_tokens": 12,
                "completion_tokens": 30,
                "revision_no": 2,
            }
        ]
    viewer_body = client.get(f"{BASE}/items/{item_id}", headers=as_(STRANGER_ID)).json()
    assert "generation_log" not in viewer_body
    assert viewer_body["revisions"][0]["provenance"]["kind"] == "ai_generated"  # evidence stays


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
            )
        ),
    }
    assert set(paths[f"{b}/items"]) == {"get", "post"}
    assert set(paths[f"{b}/items/{{item_id}}/body"]) == {"put"}
