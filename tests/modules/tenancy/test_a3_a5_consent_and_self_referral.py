"""SPRINT-11.1 A-3 (E3) + A-5 (E5) — ``PATCH /brands/{id}/learning-consent`` is a **tenancy**
route (OWNER only, fresh session); ``POST /brands`` refuses a referrer of the same tenant with
**422 ``referral_self_tenant``** and stores nothing."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.modules.tenancy.router import router as tenancy_router

from tests.conftest import AGENCY_ID, BRAND_ID, OWNER_ID, STRANGER_ID, grant
from tests.fakes import FakeStore

Headers = Callable[[UUID], dict[str, str]]
API = "/api/v1"


def test_a3_consent_route_lives_in_tenancy_router(app: FastAPI) -> None:
    """Exactly one ``learning-consent`` operation in the OpenAPI document, tagged ``Tenancy``,
    declared on ``tenancy.router`` (and no longer on ``entitlements``)."""
    spec = app.openapi()
    ops = [
        (path, method, op.get("tags"))
        for path, methods in spec["paths"].items()
        if path.endswith("/learning-consent")
        for method, op in methods.items()
    ]
    assert ops == [(f"{API}/brands/{{brand_id}}/learning-consent", "patch", ["Tenancy"])]
    assert any(
        getattr(r, "path", None) == "/brands/{brand_id}/learning-consent"
        for r in tenancy_router.routes
    )


def test_a3_owner_sets_consent_viewer_403_unknown_404(
    client: TestClient, auth_headers: Headers, store: FakeStore
) -> None:
    from src.modules.tenancy.domain.models import GrantRole

    url = f"{API}/brands/{BRAND_ID}/learning-consent"
    res = client.patch(url, json={"learning_consent": "none"}, headers=auth_headers(OWNER_ID))
    assert res.status_code == 200 and res.json() == {"learning_consent": "none"}
    assert store.brands[BRAND_ID].learning_consent == "none"
    brand = client.get(f"{API}/brands/{BRAND_ID}", headers=auth_headers(OWNER_ID)).json()
    assert brand["learning_consent"] == "none"
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    res = client.patch(url, json={"learning_consent": "full"}, headers=auth_headers(AGENCY_ID))
    assert res.status_code == 403 and res.json()["detail"]["error"] == "insufficient_role"
    res = client.patch(url, json={"learning_consent": "full"}, headers=auth_headers(STRANGER_ID))
    assert res.status_code == 404
    res = client.patch(url, json={"learning_consent": "loud"}, headers=auth_headers(OWNER_ID))
    assert res.status_code == 422
    res = client.patch(
        url, json={"learning_consent": "full", "x": 1}, headers=auth_headers(OWNER_ID)
    )
    assert res.status_code == 422  # extra="forbid"


def test_a5_self_tenant_referral_is_422_and_stores_nothing(
    client: TestClient, auth_headers: Headers, store: FakeStore
) -> None:
    before = set(store.brands)
    res = client.post(
        f"{API}/brands",
        json={"slug": "second-shop", "name": "Second", "referred_by_brand_id": str(BRAND_ID)},
        headers=auth_headers(OWNER_ID),  # BRAND_ID belongs to OWNER_ID's tenant
    )
    assert res.status_code == 422, res.text
    assert res.json()["detail"] == {
        "error": "referral_self_tenant",
        "message": "a brand cannot be referred by a brand of the same tenant",
    }
    assert set(store.brands) == before
    # Another tenant's brand as referrer is fine (D-078 happy path).
    res = client.post(
        f"{API}/brands",
        json={"slug": "agency-shop", "name": "Agency Shop", "referred_by_brand_id": str(BRAND_ID)},
        headers=auth_headers(AGENCY_ID),
    )
    assert res.status_code == 201, res.text
    assert store.brands[UUID(res.json()["id"])].referred_by_brand_id == BRAND_ID
