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
    # SPRINT-12 C-1: the watermarked public diagnosis card — token in the path, 60/min/IP.
    ("GET", "/api/v1/d/{token}"),
}
SAMPLE_UUID = "00000000-0000-4000-8000-0000000000ff"


def _protected_operations() -> list[tuple[str, str]]:
    os.environ.setdefault("APP_ENV", "test")
    os.environ.setdefault("AUTH_JWT_SECRET", "guard-collection-secret-at-least-32-bytes!!")
    spec = create_app(Settings(_env_file=None)).openapi()  # type: ignore[call-arg]
    ops = [(m.upper(), path) for path, methods in spec["paths"].items() for m in methods]
    # SPRINT-12.1 B: the ``/app/*`` HTML UI is session-cookie guarded (303 ⇒ /app/login), not
    # Bearer guarded; it is ``include_in_schema=False`` *and* excluded here explicitly.
    return sorted(op for op in ops if op not in PUBLIC and not op[1].startswith("/app"))


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
        "competitor_id",  # SPRINT-10 B-1
        "provider_id",  # SPRINT-11.1 C-1
        "model_id",
        "account_id",  # SPRINT-12 B (D-082)
        "alert_id",  # SPRINT-12 B-3 (Pulse)
        "diag_id",  # SPRINT-12 C-1 (public diagnosis link)
        "rec_id",  # SPRINT-12.1-B C (UI)
    ):
        template = template.replace("{" + name + "}", SAMPLE_UUID)
    template = template.replace("{action}", "submit").replace("{tab}", "providers")  # 12.1-B
    return template.replace("{purpose}", "content_generation")  # SPRINT-11.1 C-1


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
    # SPRINT-10 B-2: +1 (POST …/brain/competitors/{competitor_id}/refresh) ⇒ 57
    # SPRINT-10 B-3: +5 (diagnose, content-plan, GET recommendations, accept, dismiss) ⇒ 62
    # SPRINT-11 C-5: +4 entitlements (admin activate, admin read, brand entitlement,
    # learning-consent) ⇒ 66
    # SPRINT-11.1 C-1: +11 /admin/ai/* (providers ×4, models ×4, routing ×2, probe) ⇒ 77
    # SPRINT-12 B: +1 lead-stage (D-079) +2 pulse alerts (list, ack) +1 admin wa-caps (D-082) ⇒ 81
    # SPRINT-12 C-1: +2 (POST/DELETE …/brain/diagnoses/{diag_id}/share) ⇒ 83; public 6 ⇒ 7.
    # (The directive's 82 assumed batch approval — deferred to 12.1 by the size valve — and
    # a PATCH-based share; the real routes are POST/DELETE share — documented in §6.)
    assert PROTECTED_ROUTE_COUNT == 85, (
        PROTECTED
    )  # 12 B: 77+4; C-1: +2; 12.1 A-2: +1; 12.1-B D-4: +1


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


def test_app_routes_redirect_without_session(client: TestClient) -> None:
    """SPRINT-12.1 B: every ``/app/*`` route except login/logout answers 303 ⇒ ``/app/login``
    without a session cookie (and with a garbage/expired one) — never 200, never 401 JSON."""
    from src.ui.router import router as ui_router

    guarded = [
        (m, r.path)
        for r in ui_router.routes
        for m in sorted(getattr(r, "methods", None) or ())
        if m != "HEAD" and r.path not in {"/app/login", "/app/logout"}
    ]
    assert len(guarded) == 24, guarded
    for method, template in guarded:
        path = _concrete(template)
        for cookies in ({}, {"app_session": "expired.or.garbage"}):
            client.cookies.clear()
            for key, value in cookies.items():
                client.cookies.set(key, value, path="/app")
            res = client.request(method, path, data={"csrf": "x"}, follow_redirects=False)
            assert res.status_code == 303, (method, path, res.status_code)
            assert res.headers["location"].startswith("/app/login"), (method, path)
