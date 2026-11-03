"""SPRINT-12.1-B C-0 — closes U1-a, U1-b, U2, U3, U4 (CURRENT_DIRECTIVE §3-1, tests 1–10).

``app_access`` is decoded locally; ``/auth/refresh`` is called only on expiry; CSRF is bound to
``(user_id, per-login nonce)``; htmx callers get ``HX-Redirect`` / ``HX-Retarget`` instead of
swallowed redirects and silent 4xx; every enum the templates translate has an ``ar.json`` key.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import get_args
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.config import Settings, get_settings
from src.modules.auth.dependencies import IP_LIMIT, get_rate_limiter
from src.modules.auth.domain.models import AuthenticatedActor
from src.modules.auth.infrastructure.rate_limit import InMemoryRateLimiter
from src.modules.auth.jwt import AccessTokenCodec
from src.modules.pulse.domain import ALERT_KINDS, Severity
from src.modules.whatsapp.domain.models import DeliveryStatus, LeadStage, ObjectionCode
from src.ui.router import I18N_PATH, VENDOR_CACHE_CONTROL

from tests.conftest import OWNER_USER_ID
from tests.fakes import FakeStore
from tests.ui.conftest import (
    LOGIN,
    OWNER_LOGIN,
    TODAY,
    csrf_of,
    login,
    seed_alert,
    seed_conversation,
)

HX = {"HX-Request": "true"}
MISSING = "00000000-0000-4000-8000-0000000000ff"


def _settings(app: FastAPI) -> Settings:
    return app.dependency_overrides.get(get_settings, get_settings)()


def _expired_access(app: FastAPI, client: TestClient) -> str:
    """Forge an ``app_access`` whose ``exp`` is already in the past (ttl=0) for the live sid."""
    settings = _settings(app)
    live = AccessTokenCodec(
        secret=settings.auth_jwt_secret, issuer=settings.app_name, ttl_seconds=900
    ).decode(client.cookies.get("app_access"))
    dead = AccessTokenCodec(
        secret=settings.auth_jwt_secret, issuer=settings.app_name, ttl_seconds=0
    )
    return dead.issue(live, now=datetime(2020, 1, 1, tzinfo=UTC))


def _live_sessions(store: FakeStore) -> int:
    return sum(1 for s in store.sessions.values() if s.revoked_at is None)


class _CountingLimiter(InMemoryRateLimiter):
    """The *real* sliding-window limiter + a counter of ``auth:ip`` hits (login + refresh)."""

    def __init__(self) -> None:
        super().__init__()
        self.ip_hits = 0

    def hit(self, bucket: str, key: str, limit: int, window_seconds: int) -> int | None:
        if bucket == "auth:ip":
            self.ip_hits += 1
        return super().hit(bucket, key, limit, window_seconds)


@pytest.fixture
def real_limiter(ui_app: FastAPI) -> _CountingLimiter:
    limiter = _CountingLimiter()
    ui_app.dependency_overrides[get_rate_limiter] = lambda: limiter
    return limiter


# ----------------------------------------------------------- 1. U1-a ------
def test_u1a_thirty_requests_in_a_minute_no_kick(
    ui_client: TestClient, real_limiter: _CountingLimiter
) -> None:
    login(ui_client)  # auth:ip hit #1 (login itself)
    token = csrf_of(ui_client.get(TODAY).text)
    statuses = []
    for i in range(32):
        if i % 4 == 3:
            res = ui_client.post(f"{TODAY}/alerts/{MISSING}/ack", data={"csrf": token})
        else:
            res = ui_client.get(TODAY)
        statuses.append(res.status_code)
    assert statuses == [200] * 32, statuses
    assert IP_LIMIT == 10 and len(statuses) >= 30  # the old design kicked at request 10
    assert real_limiter.ip_hits - 1 <= 1  # ≤ 1 refresh for the whole minute


# ----------------------------------------------------------- 2. U1-b ------
def test_u1b_twin_requests_same_cookies_keep_all_sessions(
    ui_app: FastAPI, ui_client: TestClient, store: FakeStore
) -> None:
    login(ui_client)
    assert _live_sessions(store) == 1
    # (a) valid access cookie — twins are served locally, nothing rotates
    first, second = ui_client.get(TODAY), ui_client.get(TODAY)
    assert (first.status_code, second.status_code) == (200, 200)
    assert _live_sessions(store) == 1
    # (b) expired access cookie on *both* twins (same refresh value) ⇒ exactly one refresh
    expired = _expired_access(ui_app, ui_client)
    session_value, nonce = ui_client.cookies.get("app_session"), ui_client.cookies.get("app_csrf")
    results = []
    for _ in range(2):
        ui_client.cookies.set("app_access", expired, path="/app")
        ui_client.cookies.set("app_session", session_value, path="/app")
        ui_client.cookies.set("app_csrf", nonce, path="/app")
        results.append(ui_client.get(TODAY).status_code)
    assert results == [200, 200]
    assert _live_sessions(store) == 1  # no revoke_all — the twin read the coalesced result


# ----------------------------------------------------------- 3. 429 -------
def test_refresh_429_keeps_cookies_and_shows_rate_limited(
    ui_app: FastAPI, ui_client: TestClient, real_limiter: _CountingLimiter
) -> None:
    login(ui_client)
    for _ in range(IP_LIMIT):  # exhaust the shared auth:ip bucket
        real_limiter.hit("auth:ip", "testclient", IP_LIMIT, 60)
    expired = _expired_access(ui_app, ui_client)
    ui_client.cookies.set("app_access", expired, path="/app")
    res = ui_client.get(TODAY)
    assert res.status_code == 503 and res.headers["retry-after"]
    assert "Max-Age=0" not in res.headers.get("set-cookie", "")
    messages = json.loads(I18N_PATH.read_text(encoding="utf-8"))
    assert messages["error.rate_limited"] in res.text
    # htmx poll: same outcome, retargeted to #flash, cookies untouched
    ui_client.cookies.set("app_access", expired, path="/app")
    res = ui_client.get(TODAY, headers=HX)
    assert res.status_code == 200 and res.headers["hx-retarget"] == "#flash"
    assert "data-flash" in res.text and "Max-Age=0" not in res.headers.get("set-cookie", "")


# ----------------------------------------------------------- 4. API 401 ---
def test_api_401_mid_page_triggers_one_refresh_then_denied(
    ui_client: TestClient, store: FakeStore, real_limiter: _CountingLimiter
) -> None:
    login(ui_client)
    assert ui_client.get(TODAY).status_code == 200
    # revoke from "another device": the access JWT is still valid for up to 15 minutes, so the
    # page must learn it from the API (401) ⇒ one refresh attempt ⇒ denied
    now = datetime.now(tz=UTC)
    for sid, session in list(store.sessions.items()):
        store.sessions[sid] = session.model_copy(update={"revoked_at": now})
    hits_before = real_limiter.ip_hits
    res = ui_client.post(f"{TODAY}/items/{MISSING}/approve", data={"csrf": "x" * 32}, headers={})
    assert res.status_code == 403  # CSRF still checked first with the local token
    saved = {k: ui_client.cookies.get(k) for k in ("app_session", "app_access", "app_csrf")}
    res = ui_client.get(f"/app/b/{MISSING}/today")  # brand read ⇒ CurrentUser (JWT ok) ⇒ 404
    assert res.status_code == 404
    # a CurrentUserFresh write sees the revocation
    token = csrf_of(ui_client.get(TODAY).text)
    res = ui_client.post(f"{TODAY}/items/{MISSING}/approve", data={"csrf": token})
    assert res.status_code == 303 and res.headers["location"].startswith(LOGIN)
    cleared = res.headers.get_list("set-cookie")
    assert len(cleared) == 3 and all("Max-Age=0" in c for c in cleared)
    assert real_limiter.ip_hits - hits_before == 1  # exactly one refresh attempt
    # htmx variant ⇒ 200 + HX-Redirect (no 303 injected into #today)
    for key, value in saved.items():
        ui_client.cookies.set(key, value, path="/app")
    res = ui_client.post(f"{TODAY}/items/{MISSING}/approve", data={"csrf": token}, headers=HX)
    assert res.status_code == 200 and res.headers["hx-redirect"].startswith(LOGIN)
    assert res.text == ""


# ----------------------------------------------------------- 5. logout ----
def test_logout_requires_csrf(ui_client: TestClient, store: FakeStore) -> None:
    login(ui_client)
    token = csrf_of(ui_client.get(TODAY).text)
    for bad in ({}, {"csrf": token[::-1]}, {"csrf": ""}):
        res = ui_client.post("/app/logout", data=bad)
        assert res.status_code == 403, bad
        assert "Max-Age=0" not in res.headers.get("set-cookie", "")
        assert _live_sessions(store) == 1
    res = ui_client.post("/app/logout", data={"csrf": token})
    assert res.status_code == 303 and res.headers["location"] == LOGIN
    assert _live_sessions(store) == 0
    cleared = res.headers.get_list("set-cookie")
    assert sorted(c.split("=")[0] for c in cleared) == ["app_access", "app_csrf", "app_session"]
    assert all("Max-Age=0" in c for c in cleared)


# ----------------------------------------------------------- 6. nonce -----
def test_csrf_nonce_differs_per_login(ui_client: TestClient) -> None:
    login(ui_client)
    first_nonce = ui_client.cookies.get("app_csrf")
    first = csrf_of(ui_client.get(TODAY).text)
    ui_client.post("/app/logout", data={"csrf": first})
    ui_client.cookies.clear()
    login(ui_client)
    second = csrf_of(ui_client.get(TODAY).text)
    assert ui_client.cookies.get("app_csrf") != first_nonce
    assert first != second  # U2: a leaked stamp dies with its login cycle
    assert len(first) == len(second) == 32


def test_csrf_stamp_from_other_user_rejected(
    ui_app: FastAPI, ui_client: TestClient, store: FakeStore
) -> None:
    login(ui_client)
    nonce = ui_client.cookies.get("app_csrf")
    from src.ui.session import csrf_token

    settings = _settings(ui_app)
    other_users_stamp = csrf_token(settings, uuid4(), nonce)  # same nonce, other principal
    own = csrf_token(settings, OWNER_USER_ID, nonce)
    assert own == csrf_of(ui_client.get(TODAY).text)
    res = ui_client.post(f"{TODAY}/alerts/{MISSING}/ack", data={"csrf": other_users_stamp})
    assert res.status_code == 403
    assert _live_sessions(store) == 1


# ----------------------------------------------------------- 7. htmx ------
def test_htmx_action_error_shows_flash_in_partial(ui_client: TestClient, pulse_repo) -> None:
    login(ui_client)
    token = csrf_of(ui_client.get(TODAY).text)
    res = ui_client.post(f"{TODAY}/alerts/{MISSING}/ack", data={"csrf": token}, headers=HX)
    assert res.status_code == 200
    assert "<html" not in res.text and 'data-card="alerts"' in res.text
    assert "data-flash" in res.text  # U3: the error is no longer swallowed by the partial
    # full-page variant renders the flash exactly once (base suppresses the duplicate)
    res = ui_client.post(f"{TODAY}/alerts/{MISSING}/ack", data={"csrf": token})
    assert res.status_code == 200 and res.text.count("data-flash") == 1


async def test_htmx_thread_error_shows_flash_in_partial(
    ui_client: TestClient,
    wa_repo,  # noqa: ANN001
    wa_account,  # noqa: ANN001
) -> None:
    conv = await seed_conversation(wa_repo, wa_account.id)
    login(ui_client)
    token = csrf_of(ui_client.get(TODAY).text)
    inbox = f"/app/b/{TODAY.split('/')[3]}/inbox"
    # an invalid stage value ⇒ 422 from the API ⇒ translated flash *inside* the thread partial
    res = ui_client.post(
        f"{inbox}/{conv.id}/stage", data={"csrf": token, "lead_stage": "bogus"}, headers=HX
    )
    assert res.status_code == 200 and "<html" not in res.text
    assert "data-flash" in res.text and "data-messages" in res.text
    assert "http_422" not in res.text


def test_htmx_csrf_403_retargets_flash(ui_client: TestClient) -> None:
    login(ui_client)
    res = ui_client.post(f"{TODAY}/alerts/{MISSING}/ack", data={"csrf": "nope"}, headers=HX)
    assert res.status_code == 403
    assert res.headers["hx-retarget"] == "#flash" and res.headers["hx-reswap"] == "innerHTML"
    assert res.text.startswith('<p role="alert" data-flash>')
    assert "Max-Age=0" not in res.headers.get("set-cookie", "")
    # the page has the #flash landing zone
    assert 'id="flash"' in ui_client.get(TODAY).text


def test_htmx_without_session_gets_hx_redirect_not_303(ui_client: TestClient) -> None:
    res = ui_client.get(TODAY, headers=HX)
    assert res.status_code == 200 and res.text == ""
    assert res.headers["hx-redirect"] == f"{LOGIN}?next={TODAY}"
    assert "location" not in res.headers


# ----------------------------------------------------------- 8. i18n ------
def test_i18n_covers_all_enums() -> None:
    messages = json.loads(I18N_PATH.read_text(encoding="utf-8"))
    expected = {
        "severity": set(get_args(Severity)),
        "stage": {s.value for s in LeadStage},
        "objection": {o.value for o in ObjectionCode},
        "alert": set(ALERT_KINDS),
        "delivery": {d.value for d in DeliveryStatus},
    }
    for prefix, values in expected.items():
        have = {k.split(".", 1)[1] for k in messages if k.startswith(f"{prefix}.")}
        assert values <= have, (prefix, values - have)
    assert "info" in {k.split(".", 1)[1] for k in messages if k.startswith("severity.")}


def test_plan_bar_shows_conversations_and_info_severity(ui_client: TestClient, pulse_repo) -> None:
    alert = seed_alert(pulse_repo)
    pulse_repo.alerts[alert.id] = alert.model_copy(
        update={"kind": "wa_daily_cap_80pct", "severity": "info"}
    )
    login(ui_client)
    html = ui_client.get(TODAY).text
    messages = json.loads(I18N_PATH.read_text(encoding="utf-8"))
    assert "severity.info" not in html and messages["severity.info"] in html
    bar = html[html.index("plan-bar") : html.index("</p>", html.index("plan-bar"))]
    assert messages["plan.conversations"] in bar
    assert bar.count(" / ") == 2  # posts X / Y · conversations Z / W


# ----------------------------------------------------------- 9. static ----
def test_vendor_cache_control_immutable(ui_client: TestClient) -> None:
    res = ui_client.get("/app/static/vendor/htmx.min.js")
    assert res.status_code == 200
    assert (
        res.headers["cache-control"] == VENDOR_CACHE_CONTROL == "public, max-age=86400, immutable"
    )
    res = ui_client.get("/app/static/vendor/LICENSES.md")
    assert res.headers["cache-control"] == VENDOR_CACHE_CONTROL
    assert ui_client.get("/app/static/vendor/nope.js").status_code == 404


# ----------------------------------------------------------- 10. cookies --
def test_login_sets_three_cookies_with_expected_lifetimes(
    ui_app: FastAPI, ui_client: TestClient
) -> None:
    res = ui_client.post(LOGIN, data=OWNER_LOGIN)
    settings = _settings(ui_app)
    cookies = {c.split("=")[0]: c for c in res.headers.get_list("set-cookie")}
    assert set(cookies) == {"app_session", "app_access", "app_csrf"}
    for c in cookies.values():
        assert "HttpOnly" in c and "Path=/app" in c and "SameSite=lax" in c
    assert f"Max-Age={settings.auth_access_ttl_seconds}" in cookies["app_access"]
    assert f"Max-Age={settings.auth_refresh_ttl_seconds}" in cookies["app_session"]
    assert f"Max-Age={settings.auth_refresh_ttl_seconds}" in cookies["app_csrf"]
    # the access cookie is a verifiable JWT for this user
    codec = AccessTokenCodec(
        secret=settings.auth_jwt_secret, issuer=settings.app_name, ttl_seconds=900
    )
    actor: AuthenticatedActor = codec.decode(ui_client.cookies.get("app_access"))
    assert actor.user_id == OWNER_USER_ID
