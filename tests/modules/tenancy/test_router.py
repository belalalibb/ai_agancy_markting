"""Contract tests — tenancy routes (SPRINT-03 B-U1..B-U5, B9) against the in-memory fakes."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from src.core.oauth_state import OAuthStateSigner
from src.modules.tenancy.domain.models import GrantRole

from tests.conftest import (
    AGENCY_ID,
    BRAND2_ID,
    BRAND_ID,
    FAKE_STATE_SECRET,
    OWNER_ID,
    STRANGER_ID,
    debug_token_payload,
    grant,
    me_payload,
    pages_payload,
    token_payload,
)
from tests.fakes import FakeConnectedAccountRepository, FakeStore

pytestmark = pytest.mark.contract

API = "/api/v1"


@pytest.fixture
def as_(auth_headers: Headers) -> Headers:
    """``as_(tenant_id)`` ⇒ Bearer headers for that tenant's user (SPRINT-04 B5)."""
    return auth_headers


Headers = Callable[[UUID], dict[str, str]]


def _grant_body(grantee: UUID, role: str = "manager") -> dict[str, str]:
    return {"grantee_tenant_id": str(grantee), "role": role}


# ------------------------------------------------- SPRINT-04 B-U6 / B4 ---
def test_post_tenants_is_gone_tenants_come_from_register(client: TestClient, as_: Headers) -> None:
    """B-U6 — ``POST /tenants`` was removed; ``POST /auth/register`` creates the tenant."""
    res = client.post(
        f"{API}/tenants", json={"slug": "acme-co", "name": "Acme"}, headers=as_(OWNER_ID)
    )
    assert res.status_code in (404, 405)
    assert client.post(f"{API}/tenants", json={"slug": "acme-co", "name": "Acme"}).status_code in (
        404,
        405,
    )
    spec = client.get("/openapi.json").json()
    assert "/api/v1/tenants" not in spec["paths"]


# ----------------------------------------------------------------- B-U1 ---
def test_get_own_tenant_and_mismatch_404(client: TestClient, as_: Headers) -> None:
    ok = client.get(f"{API}/tenants/{OWNER_ID}", headers=as_(OWNER_ID))
    assert ok.status_code == 200
    assert ok.json()["slug"] == "nile-owner"
    other = client.get(f"{API}/tenants/{OWNER_ID}", headers=as_(AGENCY_ID))
    assert other.status_code == 404
    assert other.json()["detail"]["error"] == "tenant_not_found"
    unknown = client.get(f"{API}/tenants/{uuid4()}", headers=as_(OWNER_ID))
    assert unknown.status_code == 404
    # SPRINT-04: identity comes only from the Bearer token — no token ⇒ 401, not 422.
    anon = client.get(f"{API}/tenants/{OWNER_ID}")
    assert anon.status_code == 401
    assert anon.headers["www-authenticate"] == "Bearer"
    # B-U5: a stray ``?tenant_id=`` is ignored; the JWT wins.
    spoof = client.get(
        f"{API}/tenants/{OWNER_ID}", params={"tenant_id": str(OWNER_ID)}, headers=as_(AGENCY_ID)
    )
    assert spoof.status_code == 404


# ----------------------------------------------------------------- B-U2 ---
def test_create_brand_201_and_duplicates(client: TestClient, as_: Headers) -> None:
    """B-U2 — the owner is the JWT ``tid``; a body ``tenant_id`` is rejected (extra=forbid)."""
    body = {"slug": "new-brand", "name": "New"}
    res = client.post(f"{API}/brands", json=body, headers=as_(OWNER_ID))
    assert res.status_code == 201, res.text
    assert res.json()["owner_tenant_id"] == str(OWNER_ID)
    assert res.json()["slug"] == "new-brand"

    dup = client.post(f"{API}/brands", json=body, headers=as_(OWNER_ID))
    assert dup.status_code == 409
    assert dup.json()["detail"]["error"] == "brand_slug_taken"

    # Same slug, different owner → fine.
    other = client.post(f"{API}/brands", json=body, headers=as_(AGENCY_ID))
    assert other.status_code == 201
    assert other.json()["owner_tenant_id"] == str(AGENCY_ID)

    # B-U2: trying to pick the owner through the body is a validation error, not a takeover.
    spoof = client.post(
        f"{API}/brands",
        json={**body, "slug": "spoofed", "tenant_id": str(OWNER_ID)},
        headers=as_(AGENCY_ID),
    )
    assert spoof.status_code == 422


def test_create_brand_unknown_tenant_404_and_validation(client: TestClient, as_: Headers) -> None:
    # A valid JWT for a tenant that no longer exists ⇒ tenant_not_found (defence in depth).
    res = client.post(f"{API}/brands", json={"slug": "abc", "name": "x"}, headers=as_(uuid4()))
    assert res.status_code == 404
    assert res.json()["detail"]["error"] == "tenant_not_found"
    bad = client.post(f"{API}/brands", json={"slug": "Bad", "name": "x"}, headers=as_(OWNER_ID))
    assert bad.status_code == 422
    assert client.post(f"{API}/brands", json={"slug": "abc", "name": "x"}).status_code == 401


def test_list_brands_owned_and_granted_with_role(
    client: TestClient, store: FakeStore, as_: Headers
) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.VIEWER)
    revoked = grant(store, BRAND2_ID, AGENCY_ID, GrantRole.MANAGER)
    store.grants[revoked.id] = revoked.model_copy(update={"revoked_at": revoked.granted_at})

    owner = client.get(f"{API}/brands", headers=as_(OWNER_ID)).json()
    assert {(b["slug"], b["role"]) for b in owner} == {
        ("nile-coffee", "owner"),
        ("cairo-dental", "owner"),
    }
    agency = client.get(f"{API}/brands", headers=as_(AGENCY_ID)).json()
    assert [(b["slug"], b["role"]) for b in agency] == [("nile-coffee", "viewer")]
    assert client.get(f"{API}/brands", headers=as_(STRANGER_ID)).json() == []
    assert client.get(f"{API}/brands").status_code == 401  # SPRINT-04: no Bearer ⇒ 401


def test_get_brand_any_role_or_404(client: TestClient, store: FakeStore, as_: Headers) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.VIEWER)
    assert client.get(f"{API}/brands/{BRAND_ID}", headers=as_(OWNER_ID)).json()["role"] == "owner"
    assert (
        client.get(f"{API}/brands/{BRAND_ID}", headers=as_(AGENCY_ID)).json()["role"] == "viewer"
    )
    res = client.get(f"{API}/brands/{BRAND_ID}", headers=as_(STRANGER_ID))
    assert res.status_code == 404
    assert res.json()["detail"]["error"] == "brand_not_found"
    assert client.get(f"{API}/brands/{uuid4()}", headers=as_(OWNER_ID)).status_code == 404


# ----------------------------------------------------------------- B-U4 ---
def test_grant_lifecycle(client: TestClient, as_: Headers) -> None:
    url = f"{API}/brands/{BRAND_ID}/grants"
    res = client.post(url, json=_grant_body(AGENCY_ID, "manager"), headers=as_(OWNER_ID))
    assert res.status_code == 201, res.text
    g = res.json()
    assert g["role"] == "manager"
    assert g["grantee_tenant_id"] == str(AGENCY_ID)
    assert g["granted_by_tenant_id"] == str(OWNER_ID)
    assert g["revoked_at"] is None

    # Duplicate active grant → 409.
    dup = client.post(url, json=_grant_body(AGENCY_ID, "viewer"), headers=as_(OWNER_ID))
    assert dup.status_code == 409
    assert dup.json()["detail"]["error"] == "grant_already_active"

    # List (owner only) shows it.
    listed = client.get(url, headers=as_(OWNER_ID))
    assert listed.status_code == 200
    assert [x["id"] for x in listed.json()] == [g["id"]]
    assert client.get(url, headers=as_(AGENCY_ID)).status_code == 403
    assert client.get(url, headers=as_(STRANGER_ID)).status_code == 404

    # Revoke → 204; listed with revoked_at; revoke again → 404.
    assert client.delete(f"{url}/{g['id']}", headers=as_(OWNER_ID)).status_code == 204
    after = client.get(url, headers=as_(OWNER_ID)).json()
    assert after[0]["revoked_at"] is not None
    gone = client.delete(f"{url}/{g['id']}", headers=as_(OWNER_ID))
    assert gone.status_code == 404
    assert gone.json()["detail"]["error"] == "grant_not_found"

    # Re-grant after revoke → 201 (audit trail keeps both).
    again = client.post(url, json=_grant_body(AGENCY_ID, "viewer"), headers=as_(OWNER_ID))
    assert again.status_code == 201
    assert len(client.get(url, headers=as_(OWNER_ID)).json()) == 2


def test_grant_by_non_owner_is_404_not_403(
    client: TestClient, store: FakeStore, as_: Headers
) -> None:
    url = f"{API}/brands/{BRAND_ID}/grants"
    stranger = client.post(url, json=_grant_body(AGENCY_ID), headers=as_(STRANGER_ID))
    assert stranger.status_code == 404
    assert stranger.json()["detail"]["error"] == "brand_not_found"
    # A manager *does* have access, so it is a 403 for them (insufficient role).
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    manager = client.post(url, json=_grant_body(STRANGER_ID), headers=as_(AGENCY_ID))
    assert manager.status_code == 403
    assert manager.json()["detail"]["error"] == "insufficient_role"


def test_grant_to_owner_422_and_unknown_grantee_404(client: TestClient, as_: Headers) -> None:
    url = f"{API}/brands/{BRAND_ID}/grants"
    res = client.post(url, json=_grant_body(OWNER_ID), headers=as_(OWNER_ID))
    assert res.status_code == 422
    assert res.json()["detail"]["error"] == "grantee_is_owner"
    unknown = client.post(url, json=_grant_body(uuid4()), headers=as_(OWNER_ID))
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["error"] == "tenant_not_found"
    bad_role = client.post(url, json=_grant_body(AGENCY_ID, "owner"), headers=as_(OWNER_ID))
    assert bad_role.status_code == 422


def test_revoke_unknown_grant_or_wrong_brand_404(
    client: TestClient, store: FakeStore, as_: Headers
) -> None:
    g = grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    wrong_brand = client.delete(f"{API}/brands/{BRAND2_ID}/grants/{g.id}", headers=as_(OWNER_ID))
    assert wrong_brand.status_code == 404
    assert (
        client.delete(
            f"{API}/brands/{BRAND_ID}/grants/{uuid4()}", headers=as_(OWNER_ID)
        ).status_code
        == 404
    )
    assert (
        client.delete(f"{API}/brands/{BRAND_ID}/grants/{g.id}", headers=as_(AGENCY_ID)).status_code
        == 403
    )


# ------------------------------------------------------------ B-U5 / B8 ---
def _stub_graph(graph_mock: respx.MockRouter, user_id: str, name: str) -> None:
    graph_mock.get("/oauth/access_token").mock(
        return_value=httpx.Response(200, json=token_payload("LONG_LIVED", 5183944))
    )
    graph_mock.get("/me", params__contains={"fields": "id,name"}).mock(
        return_value=httpx.Response(200, json=me_payload(user_id, name))
    )
    graph_mock.get("/debug_token").mock(
        return_value=httpx.Response(200, json=debug_token_payload(user_id))
    )
    graph_mock.get("/me/accounts").mock(return_value=httpx.Response(200, json=pages_payload(1)))


def _connect(client: TestClient, tenant: UUID, brand: UUID) -> str:
    state = OAuthStateSigner(FAKE_STATE_SECRET).issue(tenant, brand)
    res = client.get(f"{API}/social/meta/callback", params={"code": "C", "state": state})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_revoke_grant_purges_only_grantee_connections_in_this_brand(
    client: TestClient,
    graph_mock: respx.MockRouter,
    store: FakeStore,
    fake_repo: FakeConnectedAccountRepository,
    as_: Headers,
) -> None:
    g1 = grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND2_ID, AGENCY_ID, GrantRole.MANAGER)

    # Owner connects their own Meta user to brand 1; agency connects its user to both brands.
    _stub_graph(graph_mock, "900", "Owner Person")
    owner_conn = _connect(client, OWNER_ID, BRAND_ID)
    _stub_graph(graph_mock, "901", "Agency Person")
    agency_b1 = _connect(client, AGENCY_ID, BRAND_ID)
    agency_b2 = _connect(client, AGENCY_ID, BRAND2_ID)
    assert len(fake_repo.rows) == 3

    res = client.delete(f"{API}/brands/{BRAND_ID}/grants/{g1.id}", headers=as_(OWNER_ID))
    assert res.status_code == 204

    remaining = set(map(str, fake_repo.rows))
    assert agency_b1 not in remaining  # purged (B8)
    assert owner_conn in remaining  # owner's own connection untouched
    assert agency_b2 in remaining  # same agency, other brand untouched
    # B7: the agency person's name is gone from brand 1.
    assert not any(
        c.provider_user_name == "Agency Person" and c.brand_id == BRAND_ID
        for c in fake_repo.rows.values()
    )
    # And the agency lost access to brand 1 but keeps brand 2.
    assert client.get(f"{API}/brands/{BRAND_ID}", headers=as_(AGENCY_ID)).status_code == 404
    assert client.get(f"{API}/brands/{BRAND2_ID}", headers=as_(AGENCY_ID)).status_code == 200
