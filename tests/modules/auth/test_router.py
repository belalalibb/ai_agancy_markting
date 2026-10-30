"""SPRINT-04 Part A router tests (A-U1 … A-U11) against the fake-backed app."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from src.core.config import Settings, get_settings
from src.modules.auth.dependencies import get_current_user_fresh
from src.modules.auth.domain.models import AuthenticatedActor, RefreshSession, User
from src.modules.auth.service import hash_refresh_secret

from tests.conftest import AGENCY_ID, OWNER_ID, OWNER_USER_ID, TEST_PASSWORD, USER_BY_TENANT
from tests.fakes import (
    FakePasswordHasher,
    FakeRateLimiter,
    FakeRefreshSessionRepository,
    FakeStore,
)

AUTH = "/api/v1/auth"
CSRF = {"X-Requested-With": "XMLHttpRequest"}
COOKIE = "refresh"  # AUTH_COOKIE_SECURE=false in tests ⇒ no ``__Host-`` prefix
COOKIE_PATH = "/api/v1/auth"
OWNER_LOGIN = {"email": "nile-owner@example.com", "password": TEST_PASSWORD}
# Deliberately *incorrect* credential used to exercise the 401 path - not a secret.
WRONG_PASSWORD = "wrong-pass-123"  # noqa: S105 - fake fixture value  # gitleaks:allow


def register_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "email": "new.owner@example.com",
        "password": "a-strong-passphrase-42",
        "tenant_slug": "new-owner",
        "tenant_name": "New Owner LLC",
    }
    payload.update(overrides)
    return payload


def set_cookie_header(response: Any) -> SimpleCookie:
    jar = SimpleCookie()
    for raw in response.headers.get_list("set-cookie"):
        jar.load(raw)
    return jar


def _login(client: TestClient) -> tuple[str, str]:
    response = client.post(f"{AUTH}/login", json=OWNER_LOGIN)
    assert response.status_code == 200, response.text
    return response.json()["access_token"], set_cookie_header(response)[COOKIE].value


def _override_settings(app: FastAPI) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)  # type: ignore[call-arg]


# ----------------------------------------------------------------- A-U1 --
def test_register_creates_tenant_and_user_and_signs_in(
    client: TestClient, store: FakeStore
) -> None:
    before_tenants, before_users = len(store.tenants), len(store.users)
    response = client.post(f"{AUTH}/register", json=register_payload())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["token_type"] == "bearer"  # noqa: S105
    assert body["expires_in"] == 900
    assert body["access_token"].count(".") == 2
    assert set(body["user"]) == {"id", "email", "tenant_id"}
    assert len(store.tenants) == before_tenants + 1
    assert len(store.users) == before_users + 1
    user = store.users[UUID(body["user"]["id"])]
    assert user.tenant_id == UUID(body["user"]["tenant_id"])
    assert store.tenants[user.tenant_id].slug == "new-owner"
    assert user.password_hash != "a-strong-passphrase-42"  # noqa: S105
    assert [s for s in store.sessions.values() if s.user_id == user.id]


def test_register_duplicate_email_is_case_insensitive_409(
    client: TestClient, store: FakeStore
) -> None:
    assert client.post(f"{AUTH}/register", json=register_payload()).status_code == 201
    users_before = len(store.users)
    response = client.post(
        f"{AUTH}/register",
        json=register_payload(email="NEW.OWNER@example.com", tenant_slug="another-slug"),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "email_taken"
    assert len(store.users) == users_before


def test_register_duplicate_slug_409_without_orphan_user(
    client: TestClient, store: FakeStore
) -> None:
    users_before, tenants_before = len(store.users), len(store.tenants)
    # ``nile-owner`` is already taken by the fixed world.
    response = client.post(f"{AUTH}/register", json=register_payload(tenant_slug="nile-owner"))
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "tenant_slug_taken"
    assert len(store.users) == users_before
    assert len(store.tenants) == tenants_before


# ----------------------------------------------------------------- A-U2 --
@pytest.mark.parametrize(
    ("password", "email"),
    [
        ("short9ch", "new.owner@example.com"),  # 8 chars < 10
        ("x" * 129, "new.owner@example.com"),  # > 128
        ("same.as.email@example.com", "same.as.email@example.com"),
        ("Same.As.Email@Example.com", "same.as.email@example.com"),  # case-insensitive
    ],
)
def test_register_password_policy_422(client: TestClient, password: str, email: str) -> None:
    response = client.post(
        f"{AUTH}/register", json=register_payload(password=password, email=email)
    )
    assert response.status_code == 422


def test_register_rejects_unknown_fields_and_bad_email(client: TestClient) -> None:
    assert client.post(f"{AUTH}/register", json=register_payload(extra=1)).status_code == 422
    assert client.post(f"{AUTH}/register", json=register_payload(email="nope")).status_code == 422


# ----------------------------------------------------------------- A-U3 --
def test_login_success_sets_httponly_refresh_cookie(client: TestClient) -> None:
    response = client.post(f"{AUTH}/login", json=OWNER_LOGIN)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["tenant_id"] == str(OWNER_ID)
    assert body["user"]["id"] == str(OWNER_USER_ID)
    raw = response.headers["set-cookie"]
    assert raw.startswith(f"{COOKIE}=")
    assert "HttpOnly" in raw
    assert "samesite=strict" in raw.lower()
    assert "Path=/api/v1/auth" in raw
    assert "Max-Age=2592000" in raw
    assert "Secure" not in raw  # AUTH_COOKIE_SECURE=false in tests
    sid, _, secret = set_cookie_header(response)[COOKIE].value.partition(".")
    UUID(sid)
    assert len(secret) >= 32


def test_login_email_is_case_insensitive(client: TestClient) -> None:
    response = client.post(
        f"{AUTH}/login", json={"email": "Nile-Owner@Example.com", "password": TEST_PASSWORD}
    )
    assert response.status_code == 200


def test_login_unknown_email_and_wrong_password_are_indistinguishable(
    client: TestClient,
) -> None:
    ghost = {"email": "ghost@example.com", "password": "x" * 12}
    missing = client.post(f"{AUTH}/login", json=ghost)
    wrong = client.post(f"{AUTH}/login", json={**OWNER_LOGIN, "password": WRONG_PASSWORD})
    assert missing.status_code == wrong.status_code == 401
    assert missing.json() == wrong.json()
    assert missing.json()["detail"]["error"] == "invalid_credentials"
    assert missing.headers["www-authenticate"] == "Bearer"
    assert "set-cookie" not in missing.headers


def test_login_inactive_user_403(client: TestClient, store: FakeStore) -> None:
    store.users[OWNER_USER_ID] = store.users[OWNER_USER_ID].model_copy(update={"is_active": False})
    response = client.post(f"{AUTH}/login", json=OWNER_LOGIN)
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "user_inactive"


# ----------------------------------------------------------------- A-U4 --
def test_login_verifies_exactly_once_whether_or_not_user_exists(
    client: TestClient, hasher: FakePasswordHasher
) -> None:
    client.post(f"{AUTH}/login", json={"email": "ghost@example.com", "password": "x" * 12})
    assert hasher.verify_calls == 1  # dummy hash verified ⇒ constant work
    client.post(f"{AUTH}/login", json={**OWNER_LOGIN, "password": "bad-pw-123"})
    assert hasher.verify_calls == 2


# ----------------------------------------------------------------- A-U6 --
def test_refresh_rotates_session_and_reuse_revokes_everything(
    client: TestClient, store: FakeStore
) -> None:
    _, old_cookie = _login(client)
    old_sid = UUID(old_cookie.partition(".")[0])
    # A second device session must survive a normal rotation but die on reuse.
    _, other_cookie = _login(client)
    other_sid = UUID(other_cookie.partition(".")[0])

    client.cookies.set(COOKIE, old_cookie, path=COOKIE_PATH)
    rotated = client.post(f"{AUTH}/refresh", headers=CSRF)
    assert rotated.status_code == 200, rotated.text
    new_cookie = set_cookie_header(rotated)[COOKIE].value
    assert new_cookie != old_cookie
    new_sid = UUID(new_cookie.partition(".")[0])
    assert store.sessions[old_sid].revoked_at is not None
    assert store.sessions[old_sid].replaced_by == new_sid
    assert store.sessions[new_sid].revoked_at is None
    assert store.sessions[other_sid].revoked_at is None
    assert rotated.json()["access_token"]

    # Replay the *old* cookie ⇒ reuse detected ⇒ all sessions of the user revoked.
    client.cookies.set(COOKIE, old_cookie, path=COOKIE_PATH)
    replay = client.post(f"{AUTH}/refresh", headers=CSRF)
    assert replay.status_code == 401
    assert replay.json()["detail"]["error"] == "refresh_reuse_detected"
    assert "Max-Age=0" in replay.headers["set-cookie"]
    assert all(
        s.revoked_at is not None for s in store.sessions.values() if s.user_id == OWNER_USER_ID
    )
    # The freshly rotated cookie is now dead too.
    client.cookies.set(COOKIE, new_cookie, path=COOKIE_PATH)
    assert client.post(f"{AUTH}/refresh", headers=CSRF).status_code == 401


def test_refresh_expired_session_401(client: TestClient, store: FakeStore) -> None:
    _, cookie = _login(client)
    sid = UUID(cookie.partition(".")[0])
    store.sessions[sid] = store.sessions[sid].model_copy(
        update={"expires_at": datetime.now(tz=UTC) - timedelta(seconds=1)}
    )
    client.cookies.set(COOKIE, cookie, path=COOKIE_PATH)
    response = client.post(f"{AUTH}/refresh", headers=CSRF)
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "refresh_expired"


def test_refresh_without_cookie_401_missing(client: TestClient) -> None:
    response = client.post(f"{AUTH}/refresh", headers=CSRF)
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "refresh_missing"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "value",
    ["garbage", "not-a-uuid.secret", "00000000-0000-4000-8000-000000000001.", f"{OWNER_ID}.wrong"],
)
def test_refresh_malformed_or_unknown_cookie_401_invalid(client: TestClient, value: str) -> None:
    client.cookies.set(COOKIE, value, path=COOKIE_PATH)
    response = client.post(f"{AUTH}/refresh", headers=CSRF)
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "refresh_invalid"


def test_refresh_wrong_secret_for_existing_session_401_invalid(
    client: TestClient, store: FakeStore
) -> None:
    _, cookie = _login(client)
    sid = cookie.partition(".")[0]
    client.cookies.set(COOKIE, f"{sid}.another-secret", path=COOKIE_PATH)
    response = client.post(f"{AUTH}/refresh", headers=CSRF)
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "refresh_invalid"
    assert store.sessions[UUID(sid)].revoked_at is None  # wrong secret is *not* reuse


def test_refresh_inactive_user_403(client: TestClient, store: FakeStore) -> None:
    _, cookie = _login(client)
    store.users[OWNER_USER_ID] = store.users[OWNER_USER_ID].model_copy(update={"is_active": False})
    client.cookies.set(COOKIE, cookie, path=COOKIE_PATH)
    assert client.post(f"{AUTH}/refresh", headers=CSRF).status_code == 403


def test_refresh_for_deleted_user_401_invalid(client: TestClient, store: FakeStore) -> None:
    _, cookie = _login(client)
    del store.users[OWNER_USER_ID]
    client.cookies.set(COOKIE, cookie, path=COOKIE_PATH)
    response = client.post(f"{AUTH}/refresh", headers=CSRF)
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "refresh_invalid"


# ----------------------------------------------------------------- A-U7 --
def test_refresh_without_csrf_header_or_allowed_origin_403(client: TestClient) -> None:
    _, cookie = _login(client)
    client.cookies.set(COOKIE, cookie, path=COOKIE_PATH)
    response = client.post(f"{AUTH}/refresh")
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "csrf_rejected"
    # Header present but a foreign Origin ⇒ still rejected (empty allowlist).
    response = client.post(f"{AUTH}/refresh", headers={**CSRF, "Origin": "https://evil.example"})
    assert response.status_code == 403


def test_refresh_with_allowed_origin_200(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, app: FastAPI
) -> None:
    monkeypatch.setenv(
        "AUTH_ALLOWED_ORIGINS", "https://app.example.com, https://admin.example.com/"
    )
    _override_settings(app)
    _, cookie = _login(client)
    client.cookies.set(COOKIE, cookie, path=COOKIE_PATH)
    ok = client.post(f"{AUTH}/refresh", headers={"Origin": "https://admin.example.com"})
    assert ok.status_code == 200, ok.text
    client.cookies.set(COOKIE, set_cookie_header(ok)[COOKIE].value, path=COOKIE_PATH)
    bad = client.post(f"{AUTH}/refresh", headers={"Origin": "https://evil.example"})
    assert bad.status_code == 403


# ----------------------------------------------------------------- A-U8 --
def test_logout_clears_cookie_and_revokes_session(client: TestClient, store: FakeStore) -> None:
    _, cookie = _login(client)
    sid = UUID(cookie.partition(".")[0])
    client.cookies.set(COOKIE, cookie, path=COOKIE_PATH)
    response = client.post(f"{AUTH}/logout", headers=CSRF)
    assert response.status_code == 204
    raw = response.headers["set-cookie"]
    assert raw.startswith(f'{COOKIE}=""') or raw.startswith(f"{COOKIE}=;")
    assert "Max-Age=0" in raw
    assert "Path=/api/v1/auth" in raw
    assert store.sessions[sid].revoked_at is not None
    # Idempotent: a second logout with the same (revoked) cookie is still 204.
    client.cookies.set(COOKIE, cookie, path=COOKIE_PATH)
    assert client.post(f"{AUTH}/logout", headers=CSRF).status_code == 204


def test_logout_with_bearer_only_revokes_that_sid(
    client: TestClient, store: FakeStore, auth_headers: Callable[[UUID], dict[str, str]]
) -> None:
    headers = auth_headers(OWNER_ID)
    active_before = [s.id for s in store.sessions.values() if s.revoked_at is None]
    assert client.post(f"{AUTH}/logout", headers=headers).status_code == 204
    active_after = [s.id for s in store.sessions.values() if s.revoked_at is None]
    assert len(active_after) == len(active_before) - 1


def test_logout_without_cookie_or_bearer_401(client: TestClient) -> None:
    response = client.post(f"{AUTH}/logout")
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "refresh_missing"


def test_logout_with_cookie_requires_csrf(client: TestClient) -> None:
    _, cookie = _login(client)
    client.cookies.set(COOKIE, cookie, path=COOKIE_PATH)
    assert client.post(f"{AUTH}/logout").status_code == 403


def test_logout_all_revokes_every_session(client: TestClient, store: FakeStore) -> None:
    token, _ = _login(client)
    _login(client)
    _login(client)
    assert len([s for s in store.sessions.values() if s.user_id == OWNER_USER_ID]) == 3
    response = client.post(f"{AUTH}/logout-all", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert all(
        s.revoked_at is not None for s in store.sessions.values() if s.user_id == OWNER_USER_ID
    )


def test_logout_all_requires_bearer(client: TestClient) -> None:
    assert client.post(f"{AUTH}/logout-all").status_code == 401


def test_current_user_fresh_rejects_revoked_session_but_current_user_still_accepts(
    client: TestClient, auth_headers: Callable[[UUID], dict[str, str]], store: FakeStore
) -> None:
    """Documented trade-off: ``CurrentUser`` is stateless and valid until ``exp`` (≤ 15 min);
    ``CurrentUserFresh`` (destructive routes) checks ``refresh_sessions`` on every call."""
    headers = auth_headers(OWNER_ID)
    assert client.get(f"{AUTH}/me", headers=headers).status_code == 200
    assert client.post(f"{AUTH}/logout", headers=headers).status_code == 204
    # ``CurrentUser`` (no DB) still accepts the token …
    assert client.get(f"{AUTH}/me", headers=headers).status_code == 200
    # … while ``CurrentUserFresh`` refuses the revoked sid.
    sid = next(s.id for s in store.sessions.values() if s.revoked_at is not None)
    actor = AuthenticatedActor(user_id=OWNER_USER_ID, tenant_id=OWNER_ID, sid=sid)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_current_user_fresh(actor, FakeRefreshSessionRepository(store)))
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail["error"] == "session_revoked"
    assert exc_info.value.headers["WWW-Authenticate"] == "Bearer"


# ----------------------------------------------------------------- A-U9 --
def test_ip_rate_limit_11th_request_429_with_retry_after(
    client: TestClient, limiter: FakeRateLimiter
) -> None:
    limiter.reject_after = 10
    # Distinct emails so the per-email failure block (A-U9b) never interferes.
    for i in range(10):
        body = {"email": f"ghost{i}@example.com", "password": "whatever-123"}
        assert client.post(f"{AUTH}/login", json=body).status_code == 401
    response = client.post(
        f"{AUTH}/login", json={"email": "ghost99@example.com", "password": "x" * 12}
    )
    assert response.status_code == 429
    assert response.json()["detail"]["error"] == "rate_limited"
    assert int(response.headers["retry-after"]) >= 1
    # register and refresh share the per-IP budget
    assert client.post(f"{AUTH}/register", json=register_payload()).status_code == 429
    assert client.post(f"{AUTH}/refresh", headers=CSRF).status_code == 429


def test_five_consecutive_failures_block_only_that_email(
    client: TestClient, limiter: FakeRateLimiter
) -> None:
    bad = {"email": "nile-owner@example.com", "password": WRONG_PASSWORD}
    for _ in range(5):
        assert client.post(f"{AUTH}/login", json=bad).status_code == 401
    blocked = client.post(f"{AUTH}/login", json=bad)
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "300"
    # Even the right password is blocked for that email (case-insensitive key) …
    good = {"email": "Nile-Owner@example.com", "password": TEST_PASSWORD}
    assert client.post(f"{AUTH}/login", json=good).status_code == 429
    # … but another account is unaffected.
    other = {"email": "agency@example.com", "password": TEST_PASSWORD}
    assert client.post(f"{AUTH}/login", json=other).status_code == 200
    assert limiter.blocked == {("auth:login-failures", "nile-owner@example.com"): 300}


def test_successful_login_resets_failure_counter(
    client: TestClient, limiter: FakeRateLimiter
) -> None:
    bad = {"email": "agency@example.com", "password": WRONG_PASSWORD}
    for _ in range(4):
        client.post(f"{AUTH}/login", json=bad)
    assert limiter.failures[("auth:login-failures", "agency@example.com")] == 4
    assert client.post(f"{AUTH}/login", json={**bad, "password": TEST_PASSWORD}).status_code == 200
    assert ("auth:login-failures", "agency@example.com") not in limiter.failures


# ---------------------------------------------------------------- A-U10 --
def test_me_returns_user_and_tenant(
    client: TestClient, auth_headers: Callable[[UUID], dict[str, str]]
) -> None:
    response = client.get(f"{AUTH}/me", headers=auth_headers(AGENCY_ID))
    assert response.status_code == 200
    assert response.json() == {
        "id": str(USER_BY_TENANT[AGENCY_ID]),
        "email": "agency@example.com",
        "tenant_id": str(AGENCY_ID),
        "tenant": {"id": str(AGENCY_ID), "slug": "agency", "name": "Agency Ltd"},
    }


def test_me_requires_bearer_and_rejects_garbage(client: TestClient) -> None:
    missing = client.get(f"{AUTH}/me")
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert missing.json()["detail"]["error"] == "unauthenticated"
    bad = client.get(f"{AUTH}/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert bad.status_code == 401
    assert bad.json()["detail"]["error"] == "token_invalid"
    assert client.get(f"{AUTH}/me", headers={"Authorization": "Basic abc"}).status_code == 401


def test_me_inactive_or_deleted_user_403(
    client: TestClient, auth_headers: Callable[[UUID], dict[str, str]], store: FakeStore
) -> None:
    headers = auth_headers(OWNER_ID)
    store.users[OWNER_USER_ID] = store.users[OWNER_USER_ID].model_copy(update={"is_active": False})
    assert client.get(f"{AUTH}/me", headers=headers).status_code == 403
    del store.users[OWNER_USER_ID]
    assert client.get(f"{AUTH}/me", headers=headers).status_code == 403


def test_me_tenant_mismatch_404(
    client: TestClient, auth_headers: Callable[[UUID], dict[str, str]], store: FakeStore
) -> None:
    """A token whose ``tid`` no longer matches the user's tenant is refused (defence in depth)."""
    headers = auth_headers(OWNER_ID)
    store.users[OWNER_USER_ID] = store.users[OWNER_USER_ID].model_copy(
        update={"tenant_id": AGENCY_ID}
    )
    assert client.get(f"{AUTH}/me", headers=headers).status_code == 404


def test_auth_responses_never_contain_secret_like_keys(client: TestClient) -> None:
    """A-U10 — runtime complement to the OpenAPI guard: scan real auth responses."""
    register = client.post(f"{AUTH}/register", json=register_payload()).json()
    login = client.post(f"{AUTH}/login", json=OWNER_LOGIN).json()

    def keys(node: object) -> set[str]:
        out: set[str] = set()
        if isinstance(node, dict):
            for k, v in node.items():
                out.add(k)
                out |= keys(v)
        elif isinstance(node, list):
            for item in node:
                out |= keys(item)
        return out

    for body in (register, login):
        bad = {
            k
            for k in keys(body)
            if any(f in k.lower() for f in ("password", "hash", "secret", "token"))
        } - {"access_token", "token_type"}
        assert bad == set(), bad


def test_openapi_marks_auth_protected_routes_with_bearer_auth(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert spec["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "description": "Access JWT from /auth/login, /auth/register or /auth/refresh.",
    }
    assert spec["paths"][f"{AUTH}/me"]["get"]["security"] == [{"bearerAuth": []}]
    assert spec["paths"][f"{AUTH}/logout-all"]["post"]["security"] == [{"bearerAuth": []}]
    for public in ("register", "login", "refresh"):
        assert "security" not in spec["paths"][f"{AUTH}/{public}"]["post"]


# ---------------------------------------------------------------- A-U11 --
def test_login_rehashes_legacy_hash_and_touches_last_login(
    client: TestClient, store: FakeStore, hasher: FakePasswordHasher
) -> None:
    store.users[OWNER_USER_ID] = store.users[OWNER_USER_ID].model_copy(
        update={"password_hash": f"legacy${TEST_PASSWORD}", "last_login_at": None}
    )
    assert client.post(f"{AUTH}/login", json=OWNER_LOGIN).status_code == 200
    user: User = store.users[OWNER_USER_ID]
    assert user.password_hash == f"fake${TEST_PASSWORD}"
    assert user.last_login_at is not None
    assert hasher.hash_calls == 1


def test_login_does_not_rehash_modern_hash(
    client: TestClient, store: FakeStore, hasher: FakePasswordHasher
) -> None:
    assert client.post(f"{AUTH}/login", json=OWNER_LOGIN).status_code == 200
    assert hasher.hash_calls == 0
    assert store.users[OWNER_USER_ID].password_hash == f"fake${TEST_PASSWORD}"


# ------------------------------------------------------------ cookie flags --
def test_secure_cookie_uses_host_prefix(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, app: FastAPI
) -> None:
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    _override_settings(app)
    response = client.post(f"{AUTH}/login", json=OWNER_LOGIN)
    raw = response.headers["set-cookie"]
    assert raw.startswith("__Host-refresh=")
    assert "Secure" in raw
    assert "HttpOnly" in raw
    assert "Path=/api/v1/auth" in raw


def test_session_stores_only_sha256_of_cookie_secret(client: TestClient, store: FakeStore) -> None:
    _, cookie = _login(client)
    sid, _, secret = cookie.partition(".")
    session: RefreshSession = store.sessions[UUID(sid)]
    assert session.token_hash == hash_refresh_secret(secret)
    assert secret not in session.model_dump_json()
    assert session.user_agent == "testclient"
    assert session.ip is None  # "testclient" is not an IP literal ⇒ INET column gets NULL


def test_session_records_real_client_ip(app: FastAPI, store: FakeStore) -> None:
    """A genuine IPv4/IPv6 peer address is persisted; non-IP hosts are dropped (INET column)."""
    with TestClient(app, client=("203.0.113.9", 51234)) as c:
        res = c.post(f"{AUTH}/login", json=OWNER_LOGIN)
        assert res.status_code == 200, res.text
        sid = UUID(set_cookie_header(res)[COOKIE].value.partition(".")[0])
    assert store.sessions[sid].ip == "203.0.113.9"
