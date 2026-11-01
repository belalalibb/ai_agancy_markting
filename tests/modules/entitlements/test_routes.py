"""SPRINT-11 C-3/C-5 — the four entitlement routes over the fakes.

* platform admin: empty list ⇒ 403 for every ``/admin/*``; regular user ⇒ 403; listed user
  ⇒ 200 **and** an audited ``entitlement_events`` row with ``actor_user_id``;
* ``GET /brands/{id}/entitlement`` — any role, implicit ``trial`` + usage, no events;
* ``PATCH /brands/{id}/learning-consent`` — OWNER only (MANAGER ⇒ 403), default
  ``signals_only``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.config import Settings
from src.modules.entitlements.domain.models import DEFAULT_LIMITS, BrandEntitlement, Plan
from src.modules.tenancy.domain.models import GrantRole

from tests.conftest import AGENCY_ID, BRAND2_ID, BRAND_ID, OWNER_ID, OWNER_USER_ID, grant
from tests.fakes import FakeStore

API = "/api/v1"
ADMIN = f"{API}/admin/entitlements"
Headers = Callable[[UUID], dict[str, str]]


@pytest.fixture
def admin_app(app: FastAPI, settings: Settings) -> FastAPI:
    """The owner user of the fixed world is a platform admin."""
    from src.core.config import get_settings

    admin_settings = settings.model_copy(update={"platform_admin_user_ids": [OWNER_USER_ID]})
    app.dependency_overrides[get_settings] = lambda: admin_settings
    return app


# ------------------------------------------------------------------ admin guard --
def test_c3_empty_admin_list_refuses_every_admin_route(client: TestClient, auth_headers: Headers):
    """Safe by default: ``PLATFORM_ADMIN_USER_IDS`` unset ⇒ 403 ``platform_admin_required``."""
    owner = auth_headers(OWNER_ID)
    res = client.get(f"{ADMIN}/{BRAND_ID}", headers=owner)
    assert res.status_code == 403 and res.json()["detail"]["error"] == "platform_admin_required"
    res = client.post(
        f"{ADMIN}/activate",
        json={"brand_id": str(BRAND_ID), "plan": "growth", "months": 1},
        headers=owner,
    )
    assert res.status_code == 403 and res.json()["detail"]["error"] == "platform_admin_required"


def test_c3_regular_user_is_refused_even_when_admins_exist(
    admin_app: FastAPI, auth_headers: Headers
):
    client = TestClient(admin_app)
    res = client.get(f"{ADMIN}/{BRAND_ID}", headers=auth_headers(AGENCY_ID))
    assert res.status_code == 403 and res.json()["detail"]["error"] == "platform_admin_required"


def test_c3_admin_requires_bearer(client: TestClient):
    assert client.get(f"{ADMIN}/{BRAND_ID}").status_code == 401


# ------------------------------------------------------------------ activation --
def test_c4_activate_writes_audited_event_and_extends(admin_app: FastAPI, auth_headers: Headers):
    client = TestClient(admin_app)
    admin = auth_headers(OWNER_ID)
    res = client.post(
        f"{ADMIN}/activate",
        json={"brand_id": str(BRAND_ID), "plan": "growth", "months": 2, "note": "paid"},
        headers=admin,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["plan"] == "growth" and body["is_active"] is True
    assert body["limits"] == DEFAULT_LIMITS[Plan.GROWTH].model_dump()
    until = datetime.fromisoformat(body["active_until"])
    assert timedelta(days=59) < until - datetime.now(tz=UTC) < timedelta(days=61)
    assert [e["kind"] for e in body["events"]] == ["activated"]
    assert body["events"][0]["actor_user_id"] == str(OWNER_USER_ID)  # C-3: audited, no exception
    assert body["events"][0]["payload"]["note"] == "paid"

    # a second activation extends from the *existing* active_until ⇒ ``extended``
    res = client.post(
        f"{ADMIN}/activate",
        json={"brand_id": str(BRAND_ID), "plan": "growth", "months": 1},
        headers=admin,
    )
    assert res.status_code == 200
    body2 = res.json()
    until2 = datetime.fromisoformat(body2["active_until"])
    assert timedelta(days=29) < until2 - until < timedelta(days=31)
    assert [e["kind"] for e in body2["events"]] == ["extended", "activated"]  # newest first

    read = client.get(f"{ADMIN}/{BRAND_ID}", headers=admin)
    assert read.status_code == 200 and read.json()["plan"] == "growth"
    assert len(read.json()["events"]) == 2


def test_c4_activate_unknown_brand_404_and_bad_months_422(
    admin_app: FastAPI, auth_headers: Headers
):
    client = TestClient(admin_app)
    admin = auth_headers(OWNER_ID)
    res = client.post(
        f"{ADMIN}/activate",
        json={"brand_id": str(uuid4()), "plan": "starter", "months": 1},
        headers=admin,
    )
    assert res.status_code == 404 and res.json()["detail"]["error"] == "brand_not_found"
    for months in (0, 13):
        res = client.post(
            f"{ADMIN}/activate",
            json={"brand_id": str(BRAND_ID), "plan": "starter", "months": months},
            headers=admin,
        )
        assert res.status_code == 422, months
    res = client.get(f"{ADMIN}/{uuid4()}", headers=admin)
    assert res.status_code == 404


def test_c4_admin_read_of_brand_without_row_is_implicit_trial(
    admin_app: FastAPI, auth_headers: Headers
):
    client = TestClient(admin_app)
    res = client.get(f"{ADMIN}/{BRAND2_ID}", headers=auth_headers(OWNER_ID))
    assert res.status_code == 200
    body = res.json()
    assert body["plan"] == "trial" and body["is_active"] is True and body["events"] == []
    assert body["limits"]["daily_tokens"] == 20_000


def test_c4_referral_credit_once_on_first_activation(
    admin_app: FastAPI, auth_headers: Headers, store: FakeStore
):
    """D-078: the referred brand's first ``activated`` ⇒ one ``referral_credit`` event on the
    referrer with the agreed payload; later activations add none."""
    client = TestClient(admin_app)
    admin = auth_headers(OWNER_ID)
    referred = client.post(
        f"{API}/brands",
        json={"slug": "referred-shop", "name": "Referred", "referred_by_brand_id": str(BRAND_ID)},
        headers=admin,
    )
    assert referred.status_code == 201, referred.text
    referred_id = referred.json()["id"]
    assert "referred_by_brand_id" not in referred.json()  # never exposed on BrandOut

    for _ in range(2):
        res = client.post(
            f"{ADMIN}/activate",
            json={"brand_id": referred_id, "plan": "starter", "months": 1},
            headers=admin,
        )
        assert res.status_code == 200
    assert res.json()["referred_by_brand_id"] == str(BRAND_ID)
    events = admin_app.state.entitlement_repo.events
    credits = [e for e in events if e.kind == "referral_credit"]
    assert len(credits) == 1 and credits[0].brand_id == BRAND_ID
    assert credits[0].payload == {
        "referred_brand_id": referred_id,
        "percent": 10,
        "plan": "starter",
        "months": 1,
    }
    # no automatic credit: the referrer's entitlement row is untouched
    assert BRAND_ID not in admin_app.state.entitlement_repo.entitlements


def test_c6_create_brand_with_unknown_referrer_is_404(client: TestClient, auth_headers: Headers):
    res = client.post(
        f"{API}/brands",
        json={"slug": "lonely-shop", "name": "Lonely", "referred_by_brand_id": str(uuid4())},
        headers=auth_headers(OWNER_ID),
    )
    assert res.status_code == 404 and res.json()["detail"]["error"] == "brand_not_found"


# --------------------------------------------------------------- brand-facing --
def test_c5_brand_entitlement_public_view_for_any_role(
    app: FastAPI, client: TestClient, auth_headers: Headers, store: FakeStore
):
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.VIEWER)
    app.state.counter_reader.used[(BRAND_ID, "monthly_posts")] = 3
    res = client.get(f"{API}/brands/{BRAND_ID}/entitlement", headers=auth_headers(AGENCY_ID))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["plan"] == "trial" and body["is_active"] is True
    assert body["usage"] == {"daily_tokens": 0, "monthly_posts": 3, "monthly_wa_conversations": 0}
    assert "events" not in body and "referred_by_brand_id" not in body
    # stranger ⇒ 404 (hides existence), unauthenticated ⇒ 401
    assert client.get(f"{API}/brands/{BRAND_ID}/entitlement").status_code == 401
    assert (
        client.get(f"{API}/brands/{BRAND2_ID}/entitlement", headers=auth_headers(AGENCY_ID))
    ).status_code == 404


def test_c5_brand_entitlement_reflects_stored_row(
    app: FastAPI, client: TestClient, auth_headers: Headers
):
    app.state.entitlement_repo.entitlements[BRAND_ID] = BrandEntitlement(
        brand_id=BRAND_ID,
        plan=Plan.AGENCY,
        limits=DEFAULT_LIMITS[Plan.AGENCY],
        active_until=datetime.now(tz=UTC) + timedelta(days=10),
        is_active=True,
    )
    res = client.get(f"{API}/brands/{BRAND_ID}/entitlement", headers=auth_headers(OWNER_ID))
    assert res.status_code == 200 and res.json()["plan"] == "agency"
    assert res.json()["limits"]["seats"] == 10


def test_c6_learning_consent_owner_only_default_signals_only(
    client: TestClient, auth_headers: Headers, store: FakeStore
):
    owner = auth_headers(OWNER_ID)
    brand = client.get(f"{API}/brands/{BRAND_ID}", headers=owner)
    assert brand.json()["learning_consent"] == "signals_only"

    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    res = client.patch(
        f"{API}/brands/{BRAND_ID}/learning-consent",
        json={"learning_consent": "none"},
        headers=auth_headers(AGENCY_ID),
    )
    assert res.status_code == 403 and res.json()["detail"]["error"] == "insufficient_role"

    res = client.patch(
        f"{API}/brands/{BRAND_ID}/learning-consent",
        json={"learning_consent": "none"},
        headers=owner,
    )
    assert res.status_code == 200 and res.json() == {"learning_consent": "none"}
    assert store.brands[BRAND_ID].learning_consent == "none"
    assert client.get(f"{API}/brands/{BRAND_ID}", headers=owner).json()["learning_consent"] == (
        "none"
    )

    res = client.patch(
        f"{API}/brands/{BRAND_ID}/learning-consent",
        json={"learning_consent": "everything"},
        headers=owner,
    )
    assert res.status_code == 422
    assert (
        client.patch(
            f"{API}/brands/{BRAND2_ID}/learning-consent",
            json={"learning_consent": "full"},
            headers=auth_headers(AGENCY_ID),
        ).status_code
        == 404
    )
