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
    from src.modules.ai_gateway.dependencies import get_ai_gateway
    from src.modules.ai_gateway.gateway import CompletionResult

    class LongProvider:
        async def complete(self, req):  # noqa: ANN001, ANN202
            return CompletionResult(
                text="ن" * 2300,
                provider="fake",
                model="fake-1",
                latency_ms=1,
                prompt_tokens=5,
                completion_tokens=9,
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
            )
        ),
    }
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


def test_b5_gateway_errors_map_to_429_502_503(
    client: TestClient,
    as_: Headers,
    app,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    from pydantic import SecretStr
    from src.core.config import get_settings
    from src.modules.ai_gateway.dependencies import get_ai_gateway
    from src.modules.ai_gateway.exceptions import AIRateLimitedError, AIUpstreamError

    owner = as_(OWNER_ID)
    item_id = _create(client, owner)["id"]

    class Boom:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

        async def complete(self, req):  # noqa: ANN001, ANN202
            raise self.exc

    app.dependency_overrides[get_ai_gateway] = lambda: Boom(AIRateLimitedError(42))
    res = _generate(client, owner, item_id)
    assert res.status_code == 429 and res.json()["detail"]["error"] == "ai_rate_limited"
    assert res.headers["Retry-After"] == "42"
    app.dependency_overrides[get_ai_gateway] = lambda: Boom(AIUpstreamError())
    res = _generate(client, owner, item_id)
    assert res.status_code == 502 and res.json()["detail"] == {"error": "ai_upstream_unavailable"}
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
            "error": "ai_upstream_unavailable"
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
