"""SPRINT-12.1 A-2 — ``POST /brands/{id}/content/approvals/batch`` (SPRINT-12 C-2, D-055).

One admission check, then the single-item approve path **per item**: plan check, lifecycle,
one ``approval_events`` row per approved item. A refused item never fails the batch — it is
``skipped`` with the refusal **code** and the request stays 200.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.metrics import counters
from src.modules.entitlements.domain.models import DEFAULT_LIMITS, BrandEntitlement, Plan
from src.modules.tenancy.domain.models import GrantRole

from tests.conftest import AGENCY_ID, BRAND_ID, OWNER_ID, STRANGER_ID, grant
from tests.fakes import FakeStore
from tests.modules.content.test_router import BASE, _act, _create, _to_pending

pytestmark = pytest.mark.contract

Headers = Callable[[UUID], dict[str, str]]
URL = f"{BASE}/approvals/batch"


def _batch(client: TestClient, headers: dict[str, str], ids: list[object], **extra: object):
    return client.post(URL, json={"item_ids": [str(i) for i in ids], **extra}, headers=headers)


def _events(client: TestClient, headers: dict[str, str], item_id: str) -> list[str]:
    detail = client.get(f"{BASE}/items/{item_id}", headers=headers).json()
    return [e["action"] for e in detail["approval_events"]]


def test_a2_mixed_batch_is_200_with_one_line_per_item_and_events_only_for_approved(
    client: TestClient, auth_headers: Headers
) -> None:
    owner = auth_headers(OWNER_ID)
    pending = [_to_pending(client, owner) for _ in range(3)]
    draft = _create(client, owner)["id"]  # never submitted ⇒ invalid_transition
    missing = uuid4()
    before = counters.snapshot().get("content_batch_approved", 0)
    res = _batch(client, owner, [pending[0], missing, draft, pending[1], pending[2]], note="ok")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["approved"] == 3 and body["skipped"] == 2
    assert [(r["status"], r["error"]) for r in body["results"]] == [
        ("approved", None),
        ("skipped", "content_not_found"),
        ("skipped", "invalid_transition"),
        ("approved", None),
        ("approved", None),
    ]
    assert [r["item_id"] for r in body["results"]] == [
        pending[0],
        str(missing),
        draft,
        pending[1],
        pending[2],
    ]
    # D-055: one ``approved`` event per approved item, none for the skipped ones
    for item_id in pending:
        assert _events(client, owner, item_id) == ["submitted", "approved"]
    assert _events(client, owner, draft) == []
    assert counters.snapshot()["content_batch_approved"] == before + 3
    # idempotence: the same batch again ⇒ every pending item is now ``invalid_transition``
    again = _batch(client, owner, pending).json()
    assert again["approved"] == 0 and {r["error"] for r in again["results"]} == {
        "invalid_transition"
    }


def test_a2_fifty_one_or_duplicate_or_empty_ids_are_422(
    client: TestClient, auth_headers: Headers
) -> None:
    owner = auth_headers(OWNER_ID)
    assert _batch(client, owner, [uuid4() for _ in range(51)]).status_code == 422
    dup = uuid4()
    assert _batch(client, owner, [dup, dup]).status_code == 422
    assert _batch(client, owner, []).status_code == 422
    assert client.post(URL, json={"item_ids": ["x"]}, headers=owner).status_code == 422
    # exactly 50 unknown ids ⇒ 200 with 50 skipped lines (the ceiling is inclusive)
    res = _batch(client, owner, [uuid4() for _ in range(50)])
    assert res.status_code == 200 and res.json()["skipped"] == 50


def test_a2_viewer_is_403_stranger_404_manager_gets_per_item_owner_refusal(
    client: TestClient, store: FakeStore, auth_headers: Headers
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    item_id = _to_pending(client, auth_headers(OWNER_ID))
    viewer = _batch(client, auth_headers(STRANGER_ID), [item_id])
    assert viewer.status_code == 403 and viewer.json()["detail"]["error"] == "insufficient_role"
    # MANAGER is admitted (same as the single route) but D-055 refuses each approval
    manager = _batch(client, auth_headers(AGENCY_ID), [item_id])
    assert manager.status_code == 200
    assert manager.json()["results"] == [
        {"item_id": item_id, "status": "skipped", "error": "approval_requires_owner"}
    ]
    assert _events(client, auth_headers(OWNER_ID), item_id) == ["submitted"]
    other = f"/api/v1/brands/{uuid4()}/content/approvals/batch"
    res = client.post(other, json={"item_ids": [item_id]}, headers=auth_headers(OWNER_ID))
    assert res.status_code == 404 and res.json()["detail"]["error"] == "brand_not_found"


def test_a2_post_quota_is_checked_per_item_and_stops_at_the_ceiling(
    app: FastAPI, client: TestClient, auth_headers: Headers
) -> None:
    """Trial = 10 posts/month. With 8 used, a batch of 4 approves 2 and skips 2 with
    ``entitlement_quota_exceeded`` — the request itself is never 402."""
    owner = auth_headers(OWNER_ID)
    pending = [_to_pending(client, owner) for _ in range(4)]

    class _Counting:
        """Mirror what PG does: every prior approval raised the month's usage by one, so the
        n-th plan check of the batch sees ``8 + (n - 1)`` — 8, 9 pass; 10, 11 are refused."""

        def __init__(self) -> None:
            self.calls = 0

        async def monthly_used(self, brand_id: UUID, counter: str, *, since) -> int:  # noqa: ANN001
            self.calls += 1
            return 8 + self.calls - 1

    # the fake app builds its limits reader per request from ``app.state.counter_reader``
    app.state.counter_reader = _Counting()
    res = _batch(client, owner, pending)
    assert res.status_code == 200, res.text
    assert [(r["status"], r["error"]) for r in res.json()["results"]] == [
        ("approved", None),
        ("approved", None),
        ("skipped", "entitlement_quota_exceeded"),
        ("skipped", "entitlement_quota_exceeded"),
    ]


def test_a2_plan_inactive_marks_every_item_skipped_not_402(
    app: FastAPI, client: TestClient, auth_headers: Headers
) -> None:
    owner = auth_headers(OWNER_ID)
    pending = [_to_pending(client, owner) for _ in range(2)]
    app.state.entitlement_repo.entitlements[BRAND_ID] = BrandEntitlement(
        brand_id=BRAND_ID, plan=Plan.STARTER, limits=DEFAULT_LIMITS[Plan.STARTER], is_active=False
    )
    res = _batch(client, owner, pending)
    assert res.status_code == 200, res.text
    assert res.json()["approved"] == 0
    assert {r["error"] for r in res.json()["results"]} == {"entitlement_plan_inactive"}
    for item_id in pending:
        assert _events(client, owner, item_id) == ["submitted"]
    # the single-item route still answers 402 for the same state (unchanged contract)
    assert _act(client, owner, pending[0], "approve").status_code == 402
