"""SPRINT-11.1 A-2 (E2) — ``monthly_posts`` is enforced at **approval** through the
consumer-owned ``content/entitlements_port.py::CounterGuard`` bound by ``src/main.py``:
the 11th approval of a trial brand (10/month) ⇒ **402 ``entitlement_quota_exceeded``**."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.modules.content.entitlements_port import get_counter_guard

from tests.conftest import BRAND_ID, OWNER_ID
from tests.fakes import FakeStore
from tests.modules.content.test_router import _act, _to_pending

Headers = Callable[[UUID], dict[str, str]]


def test_a2_port_is_bound_in_main_and_defaults_to_none(app: FastAPI) -> None:
    """``src/main.py`` overrides the port; the un-overridden default is ``None`` (no check)."""
    assert get_counter_guard in app.dependency_overrides
    assert get_counter_guard() is None


def test_a2_eleventh_approval_is_402_and_tenth_passes(
    app: FastAPI, client: TestClient, auth_headers: Headers, store: FakeStore
) -> None:
    owner = auth_headers(OWNER_ID)
    counter = app.state.counter_reader
    counter.used[(BRAND_ID, "monthly_posts")] = 9  # trial = 10/month
    tenth = _act(client, owner, _to_pending(client, owner), "approve")
    assert tenth.status_code == 200, tenth.text
    counter.used[(BRAND_ID, "monthly_posts")] = 10
    item_id = _to_pending(client, owner)
    res = _act(client, owner, item_id, "approve")
    assert res.status_code == 402, res.text
    assert res.json()["detail"] == {
        "error": "entitlement_quota_exceeded",
        "message": "monthly plan quota reached for monthly_posts",
        "counter": "monthly_posts",
        "limit": 10,
        "used": 10,
    }
    # The item is untouched — still pending (the 402 fires before the lifecycle).
    detail = client.get(f"/api/v1/brands/{BRAND_ID}/content/items/{item_id}", headers=owner)
    assert detail.json()["status"] == "pending_approval"
    # Reject is not metered.
    assert _act(client, owner, item_id, "reject", note="no thanks").status_code == 200


def test_a2_kill_switch_disables_the_post_quota(
    app: FastAPI, client: TestClient, auth_headers: Headers, settings
) -> None:  # noqa: ANN001
    from src.core.config import get_settings

    off = settings.model_copy(update={"feature_entitlements_enforced": False})
    app.dependency_overrides[get_settings] = lambda: off
    owner = auth_headers(OWNER_ID)
    app.state.counter_reader.used[(BRAND_ID, "monthly_posts")] = 10**6
    assert _act(client, owner, _to_pending(client, owner), "approve").status_code == 200
