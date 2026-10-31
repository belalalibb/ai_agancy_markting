"""SPRINT-04 B-U1 — every ``/api/v1`` route except the public allowlist demands a Bearer JWT.

Parametrised over the live OpenAPI document (nested routers are invisible in
``app.routes``), so a new route added without ``CurrentUser`` fails here automatically.
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.config import Settings
from src.main import create_app

pytestmark = pytest.mark.contract

PUBLIC = {
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("GET", "/api/v1/social/meta/callback"),
    # SPRINT-09 B-1: Meta calls these without our JWT — GET is the hub.challenge handshake
    # (verify_token hash), POST is protected by the mandatory X-Hub-Signature-256 HMAC.
    ("GET", "/api/v1/webhooks/whatsapp"),
    ("POST", "/api/v1/webhooks/whatsapp"),
    ("GET", "/health"),
    ("GET", "/health/ready"),
}
SAMPLE_UUID = "00000000-0000-4000-8000-0000000000ff"


def _protected_operations() -> list[tuple[str, str]]:
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("AUTH_JWT_SECRET", "guard-collection-secret-at-least-32-bytes!!")
    spec = create_app(Settings(_env_file=None)).openapi()  # type: ignore[call-arg]
    ops = [(m.upper(), path) for path, methods in spec["paths"].items() for m in methods]
    return sorted(op for op in ops if op not in PUBLIC)


PROTECTED = _protected_operations()
PROTECTED_ROUTE_COUNT = len(PROTECTED)
IDS = [f"{m} {p}" for m, p in PROTECTED]


def _concrete(template: str) -> str:
    for name in (
        "brand_id",
        "connection_id",
        "grant_id",
        "tenant_id",
        "item_id",
        "conversation_id",  # SPRINT-09
        "draft_id",
    ):
        template = template.replace("{" + name + "}", SAMPLE_UUID)
    return template


def test_protected_route_count_is_reported() -> None:
    """Printed into the executor report (§7); fails loudly if the surface changes."""
    print(f"\nB-U1 protected routes: {PROTECTED_ROUTE_COUNT}")
    # auth: logout, logout-all, me (3) + tenancy: 7 + social: 8 + content: 12 (SPRINT-06)
    # + publishing: GET …/publish, POST …/publish/retry (2, SPRINT-07 B7-U3) = 32
    # SPRINT-09 B-2: +9 whatsapp (account ×3, conversations, messages, read, send, template,
    # archive) ⇒ 43. (The directive's "42" counted eight; the nine listed routes are
    # authoritative — documented in §6.)
    # SPRINT-09 B-3: +3 (suggest-reply, reply-drafts approve, reply-drafts reject) ⇒ 46
    # SPRINT-10 A-2: +1 (POST /brands/{brand_id}/whatsapp/account/rotate) ⇒ 47
    # (directive says 45 for the same reason).
    assert PROTECTED_ROUTE_COUNT == 56, PROTECTED  # SPRINT-10 B-1: 47 + 9 brain routes


@pytest.mark.parametrize(("method", "template"), PROTECTED, ids=IDS)
def test_protected_routes_require_bearer(client: TestClient, method: str, template: str) -> None:
    # Any ``?tenant_id=`` is irrelevant now — identity is the JWT only (B-U5).
    res = client.request(method, _concrete(template), params={"tenant_id": SAMPLE_UUID}, json={})
    assert res.status_code == 401, (method, template, res.text)
    assert res.headers["www-authenticate"] == "Bearer"
    assert res.json()["detail"]["error"] in {"unauthenticated", "refresh_missing"}


@pytest.mark.parametrize(("method", "template"), PROTECTED, ids=IDS)
def test_protected_routes_reject_garbage_bearer(
    client: TestClient, method: str, template: str
) -> None:
    res = client.request(
        method, _concrete(template), headers={"Authorization": "Bearer not.a.jwt"}, json={}
    )
    assert res.status_code == 401, (method, template, res.text)
    assert res.json()["detail"]["error"] == "token_invalid"


def test_openapi_marks_every_protected_route_with_bearer_auth(app: FastAPI) -> None:
    spec = app.openapi()
    assert "bearerAuth" in spec["components"]["securitySchemes"]
    for method, path in PROTECTED:
        assert spec["paths"][path][method.lower()].get("security") == [{"bearerAuth": []}], (
            method,
            path,
        )
    for method, path in PUBLIC:
        if path.startswith("/api/v1"):
            assert "security" not in spec["paths"][path][method.lower()], (method, path)
