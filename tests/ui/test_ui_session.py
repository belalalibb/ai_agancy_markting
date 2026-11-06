"""SPRINT-12.1 B-1 — ``/app`` session cookie, redirects, CSRF, logout."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.config import Settings, get_settings
from src.ui import session as ui_session
from src.ui.api import ApiError, error_code
from src.ui.router import router as ui_router

from tests.conftest import BRAND2_ID, BRAND_ID
from tests.ui.conftest import INBOX, LOGIN, OWNER_LOGIN, TODAY, csrf_of, login

SAMPLE = "00000000-0000-4000-8000-0000000000ff"
EXEMPT = {"/app/login", "/app/logout", "/app/register"}


def _app_operations() -> list[tuple[str, str]]:
    ops = []
    for route in ui_router.routes:
        path = getattr(route, "path", "")
        for method in sorted(getattr(route, "methods", None) or ()):
            if method == "HEAD" or path in EXEMPT:
                continue
            ops.append((method, path))
    return sorted(ops)


APP_OPERATIONS = _app_operations()
IDS = [f"{m} {p}" for m, p in APP_OPERATIONS]


def _concrete(template: str) -> str:
    for name in (
        "brand_id",
        "alert_id",
        "item_id",
        "conversation_id",
        "rec_id",
        "grant_id",
        "connection_id",
        "competitor_id",
    ):
        template = template.replace("{" + name + "}", SAMPLE)
    return template.replace("{action}", "submit").replace("{tab}", "providers")


def test_ui_routes_are_hidden_from_openapi_and_counted(ui_app: FastAPI) -> None:
    """The ``/app`` surface is HTML, not API: it must not appear in the OpenAPI contract
    (``tests/test_auth_guard.py`` stays at 84 protected ops) — 29 routes, 26 session-guarded
    (GET/POST login + POST logout are exempt). SPRINT-12.1-B C added 12: content (GET + 2
    POST), diagnosis (GET + 2 POST), settings (GET + 5 POST)."""
    spec_paths = ui_app.openapi()["paths"]
    assert not any(path.startswith("/app") for path in spec_paths)
    # SPRINT-14-A: +8 (register ×2, brands, bridge, pages ×2, WA ×2) · 14-B B-0.2: +1 (WA disable)
    assert (
        len(ui_router.routes) == 44
    )  # 14-B B-1/B-2: +6 (brain GET/POST, competitors ×3, evidence)
    assert len(APP_OPERATIONS) == 40  # +7 session-guarded (GET/POST register are public)


@pytest.mark.parametrize(("method", "template"), APP_OPERATIONS, ids=IDS)
def test_app_routes_redirect_without_session(
    ui_client: TestClient, method: str, template: str
) -> None:
    path = _concrete(template)
    res = ui_client.request(method, path, data={"csrf": "x", "body": "hi", "lead_stage": "won"})
    assert res.status_code == 303, (method, path, res.text)
    assert res.headers["location"].startswith(LOGIN)
    if method == "GET":
        assert res.headers["location"] == f"{LOGIN}?next={path}"


@pytest.mark.parametrize("cookie", ["garbage", "00000000-0000-4000-8000-000000000001.nope"])
def test_app_routes_redirect_with_invalid_or_expired_cookie(
    ui_client: TestClient, cookie: str
) -> None:
    ui_client.cookies.set("app_session", cookie, path="/app")
    res = ui_client.get(TODAY)
    assert res.status_code == 303 and res.headers["location"] == f"{LOGIN}?next={TODAY}"
    cleared = res.headers["set-cookie"]
    assert cleared.startswith("app_session=") and "Max-Age=0" in cleared


def test_login_form_renders_rtl_without_cdn(ui_client: TestClient) -> None:
    res = ui_client.get(f"{LOGIN}?next={TODAY}")
    assert res.status_code == 200
    assert '<html lang="ar" dir="rtl">' in res.text
    assert f'name="next" value="{TODAY}"' in res.text
    assert "/app/static/vendor/pico.min.css" in res.text and "cdn" not in res.text.lower()


def test_login_sets_httponly_lax_cookie_scoped_to_app(ui_client: TestClient) -> None:
    res = ui_client.post(LOGIN, data=OWNER_LOGIN)
    assert res.status_code == 303 and res.headers["location"] == "/app/"
    cookie = res.headers["set-cookie"]
    assert cookie.startswith("app_session=")
    assert "HttpOnly" in cookie and "Path=/app" in cookie and "SameSite=lax" in cookie
    assert "Secure" not in cookie  # auth_cookie_secure is False in the test Settings
    # the auth refresh cookie itself is NOT forwarded to the browser by the UI
    assert "refresh=" not in cookie


def test_login_honours_in_app_next_and_rejects_open_redirect(ui_client: TestClient) -> None:
    res = ui_client.post(LOGIN, data={**OWNER_LOGIN, "next": TODAY})
    assert res.status_code == 303 and res.headers["location"] == TODAY
    res = ui_client.post(LOGIN, data={**OWNER_LOGIN, "next": "https://evil.example/"})
    assert res.headers["location"] == "/app/"


def test_login_failure_renders_translated_error(ui_client: TestClient) -> None:
    res = ui_client.post(LOGIN, data={"email": OWNER_LOGIN["email"], "password": "wrong-pw-1"})
    assert res.status_code == 401
    assert "data-error" in res.text and "invalid_credentials" not in res.text
    assert "app_session" not in res.headers.get("set-cookie", "")


def test_secure_cookie_name_follows_settings(ui_app: FastAPI) -> None:
    base = ui_app.dependency_overrides.get(get_settings, get_settings)()
    secure = base.model_copy(update={"auth_cookie_secure": True})
    assert ui_session.session_cookie_name(secure) == "__Secure-app_session"
    assert ui_session.session_cookie_name(base) == "app_session"
    assert ui_session.read_session({}, base) is None


def test_session_resolves_and_brand_pick_lists_both_brands(ui_client: TestClient) -> None:
    login(ui_client)
    res = ui_client.get("/app/")
    # the owner has two brands in the fixed world ⇒ the pick page, not a redirect
    assert res.status_code == 200 and "data-brands" in res.text
    assert str(BRAND_ID) in res.text and str(BRAND2_ID) in res.text
    # C-0: a valid ``app_access`` is decoded locally — no refresh, no cookie rotation
    assert "set-cookie" not in res.headers


def test_brand_without_grant_is_404_page(ui_client: TestClient) -> None:
    login(ui_client)
    res = ui_client.get(f"/app/b/{SAMPLE}/today")
    assert res.status_code == 404
    assert "data-error" in res.text and 'dir="rtl"' in res.text


def test_csrf_missing_or_wrong_is_403(ui_client: TestClient) -> None:
    login(ui_client)
    page = ui_client.get(TODAY)
    token = csrf_of(page.text)
    assert len(token) == ui_session.CSRF_HEX_CHARS
    res = ui_client.post(f"{TODAY}/alerts/{SAMPLE}/ack", data={})
    assert res.status_code == 403
    assert "Max-Age=0" not in res.headers.get("set-cookie", "")  # cookies stay (C-0.3)
    res = ui_client.post(f"{TODAY}/alerts/{SAMPLE}/ack", data={"csrf": token[::-1]})
    assert res.status_code == 403
    res = ui_client.post(f"{INBOX}/{SAMPLE}/stage", data={"csrf": "", "lead_stage": "won"})
    assert res.status_code == 403


def test_csrf_helpers_are_deterministic_per_principal(settings: Settings) -> None:
    user, other = uuid4(), uuid4()
    nonce, other_nonce = ui_session.new_nonce(), ui_session.new_nonce()
    token = ui_session.csrf_token(settings, user, nonce)
    assert ui_session.verify_csrf(settings, user, nonce, token)
    assert not ui_session.verify_csrf(settings, other, nonce, token)
    assert not ui_session.verify_csrf(settings, user, other_nonce, token)
    assert not ui_session.verify_csrf(settings, user, nonce, None)
    assert not ui_session.verify_csrf(settings, user, "", token)


def test_csrf_stamp_is_stable_within_a_login_cycle(ui_client: TestClient) -> None:
    """The stamp is bound to (user_id, per-login nonce): identical across requests of one
    login — including across a refresh rotation — otherwise no form could be submitted."""
    login(ui_client)
    first = csrf_of(ui_client.get(TODAY).text)
    ui_client.cookies.delete("app_access", path="/app")  # force one refresh (rotation)
    second = csrf_of(ui_client.get(TODAY).text)
    assert first == second


def test_logout_revokes_session_and_clears_cookie(ui_client: TestClient) -> None:
    value = login(ui_client)
    token = csrf_of(ui_client.get(TODAY).text)
    nonce = ui_client.cookies.get("app_csrf")
    res = ui_client.post("/app/logout", data={"csrf": token})
    assert res.status_code == 303 and res.headers["location"] == LOGIN
    cleared = res.headers.get_list("set-cookie")
    assert len(cleared) == 3 and all("Max-Age=0" in c for c in cleared)
    # the revoked refresh value is useless now (access cookie gone ⇒ refresh ⇒ 401)
    ui_client.cookies.set("app_session", value, path="/app")
    ui_client.cookies.set("app_csrf", nonce, path="/app")
    res = ui_client.get(TODAY)
    assert res.status_code == 303
    # logout without a cookie is harmless
    ui_client.cookies.clear()
    assert ui_client.post("/app/logout").status_code == 303


def test_error_code_extraction() -> None:
    assert error_code({"detail": {"error": "x"}}, 400) == "x"
    assert error_code({"detail": "plain"}, 400) == "plain"
    assert error_code("not json", 502) == "http_502"
    assert str(ApiError(404, "not_found")) == "404 not_found"
