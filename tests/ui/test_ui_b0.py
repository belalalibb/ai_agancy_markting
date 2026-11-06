"""SPRINT-14-B B-0 — closures of the 14-A review findings S14A-1..3 (browser surface only).

Every journey drives ``TestClient`` with cookies and **no auto-redirects**; the API behind it
is the real app wired to fakes, Meta/Graph is ``respx``-stubbed (never a live call).
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from src.modules.auth.schemas import RegisterRequest
from src.ui.router import _only_weak_password
from src.ui.session import login_target

from tests.conftest import BRAND_ID
from tests.fakes import FakeWhatsAppRepository
from tests.modules.social_connect.test_router import _stub_happy_path
from tests.modules.whatsapp.conftest import PHONE_NUMBER_ID
from tests.ui.conftest import LOGIN, OWNER_LOGIN, SETTINGS, VIEWER_LOGIN, csrf_of, login
from tests.ui.test_ui_onboarding import (
    MESSAGES,
    NEW_EMAIL,
    NEW_PASSWORD,
    SEC,
    SYS,
    VERIFY,
    WA_FORM,
    _register,
    _steps,
    _stub_wa,
)

BRIDGE = "/app/social/meta/callback"
HOME = "/app/"


def _hidden_next(page: str) -> str:
    raw = re.search(r'<input type="hidden" name="next" value="([^"]*)"', page).group(1)
    return html.unescape(raw)


# ----------------------------------------------------------------------- B-0.1 ---
def test_login_target_quotes_the_inner_query_once() -> None:
    target = login_target(f"{BRIDGE}?code=a&state=b")
    assert target == f"{LOGIN}?next={BRIDGE}%3Fcode%3Da%26state%3Db"
    assert parse_qs(urlsplit(target).query)["next"] == [f"{BRIDGE}?code=a&state=b"]
    # open-redirect guards are unchanged
    for bad in (None, "", "https://evil.example/app/x", "//evil.example", "/other", LOGIN):
        assert login_target(bad) == LOGIN


def test_meta_callback_survives_login_with_special_characters(ui_client: TestClient) -> None:
    """``&``, ``+``, ``%`` and ``=`` inside code/state roundtrip exactly (no double encoding)."""
    code, state = "a&b+c%d=e", "s.t&u+v%w"
    res = ui_client.get(BRIDGE, params={"code": code, "state": state})
    assert res.status_code == 303
    login_page = ui_client.get(res.headers["location"])  # FastAPI decodes ``next`` once
    assert login_page.status_code == 200
    nxt = _hidden_next(login_page.text)
    assert urlsplit(nxt).path == BRIDGE
    assert parse_qs(urlsplit(nxt).query) == {"code": [code], "state": [state]}
    posted = ui_client.post(LOGIN, data={**OWNER_LOGIN, "next": nxt})
    assert posted.status_code == 303
    location = posted.headers["location"]
    assert urlsplit(location).path == BRIDGE
    assert parse_qs(urlsplit(location).query) == {"code": [code], "state": [state]}


def test_full_oauth_chain_through_login_reaches_page_selection(
    ui_client: TestClient, graph_mock: respx.MockRouter
) -> None:
    """Start OAuth ⇒ drop the cookies ⇒ bridge ⇒ **follow GET login** ⇒ hidden next ⇒ POST
    login ⇒ follow 303 to the bridge ⇒ page selection. No ``missing_parameters``."""
    login(ui_client)
    token = csrf_of(ui_client.get(SETTINGS).text)
    started = ui_client.post(f"{SETTINGS}/connections/start", data={"csrf": token})
    assert started.status_code == 303
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    _stub_happy_path(graph_mock, pages=2)

    ui_client.cookies.clear()
    bridge = ui_client.get(BRIDGE, params={"code": "CODE", "state": state})
    assert bridge.status_code == 303 and bridge.headers["location"].startswith(f"{LOGIN}?next=")
    login_page = ui_client.get(bridge.headers["location"])
    assert login_page.status_code == 200
    nxt = _hidden_next(login_page.text)
    assert parse_qs(urlsplit(nxt).query) == {"code": ["CODE"], "state": [state]}

    posted = ui_client.post(LOGIN, data={**OWNER_LOGIN, "next": nxt})
    assert posted.status_code == 303 and urlsplit(posted.headers["location"]).path == BRIDGE
    done = ui_client.get(posted.headers["location"])
    assert done.status_code == 303, done.text
    assert done.headers["location"].startswith(f"/app/b/{BRAND_ID}/settings/connections/")
    assert done.headers["location"].endswith("/pages") and "flash" not in done.headers["location"]
    pages = ui_client.get(done.headers["location"])
    assert pages.status_code == 200 and 'data-screen="pages"' in pages.text


@pytest.mark.parametrize(
    ("params", "flash"),
    [
        ({"code": "CODE"}, "missing_parameters"),  # state missing
        ({"state": "only-state"}, "missing_parameters"),
        ({"code": "CODE", "state": "garbage.value"}, "invalid_oauth_state"),  # corrupted
    ],
)
def test_bridge_rejects_missing_or_bad_state_without_calling_graph(
    ui_client: TestClient, graph_mock: respx.MockRouter, params: dict[str, str], flash: str
) -> None:
    login(ui_client)
    res = ui_client.get(BRIDGE, params=params)
    assert res.status_code == 303 and res.headers["location"] == f"/app/?flash={flash}"
    assert graph_mock.calls.call_count == 0


@pytest.mark.parametrize("evil", ["https://evil.example/app/x", "//evil.example/app/", "/other"])
def test_login_ignores_external_or_non_app_next(ui_client: TestClient, evil: str) -> None:
    res = ui_client.post(LOGIN, data={**OWNER_LOGIN, "next": evil})
    assert res.status_code == 303 and res.headers["location"] == HOME


# ----------------------------------------------------------------------- B-0.2 ---
def _wa_forms(page: str) -> set[str]:
    return set(re.findall(r'data-form="(wa-[a-z]+)"', page))


def _only_account(repo: FakeWhatsAppRepository):  # noqa: ANN202
    assert len(repo.accounts) == 1
    return next(iter(repo.accounts.values()))


def test_whatsapp_lifecycle_connect_rotate_disable_reconnect_keeps_account_id(
    ui_client: TestClient, graph_mock: respx.MockRouter, wa_repo: FakeWhatsAppRepository
) -> None:
    login(ui_client)
    _stub_wa(graph_mock)
    page = ui_client.get(SETTINGS)
    assert _wa_forms(page.text) == {"wa-connect"}  # absent ⇒ connect only
    assert MESSAGES["wa.connect"] in page.text

    res = ui_client.post(f"{SETTINGS}/whatsapp", data={"csrf": csrf_of(page.text), **WA_FORM})
    assert res.status_code == 303
    account_id = _only_account(wa_repo).id
    page = ui_client.get(SETTINGS)
    assert _wa_forms(page.text) == {"wa-rotate", "wa-disable"}  # active ⇒ rotate/disable only
    assert _steps(ui_client.get(HOME).text)["channel"] == "true"

    rot = ui_client.post(
        f"{SETTINGS}/whatsapp/rotate",
        data={"csrf": csrf_of(page.text), "wa_sys": SYS + "2", "wa_sec": SEC},
    )
    assert rot.status_code == 303 and _only_account(wa_repo).id == account_id

    token = csrf_of(ui_client.get(SETTINGS).text)
    unconfirmed = ui_client.post(f"{SETTINGS}/whatsapp/disable", data={"csrf": token})
    assert unconfirmed.status_code == 400
    assert MESSAGES["wa.disable_confirm_needed"] in unconfirmed.text
    assert _only_account(wa_repo).status.value == "active"  # no effect without confirmation

    gone = ui_client.post(
        f"{SETTINGS}/whatsapp/disable", data={"csrf": token, "wa_confirm": "yes"}
    )
    assert gone.status_code == 303 and gone.headers["location"].endswith("flash=wa_disabled")
    page = ui_client.get(gone.headers["location"])
    assert MESSAGES["wa.disabled_ok"] in page.text
    assert _wa_forms(page.text) == {"wa-connect"} and MESSAGES["wa.reconnect"] in page.text
    assert 'data-form="wa-rotate"' not in page.text
    assert _only_account(wa_repo).status.value == "disabled"
    assert _steps(ui_client.get(HOME).text)["channel"] == "false"  # disabled ≠ channel

    again = ui_client.post(f"{SETTINGS}/whatsapp", data={"csrf": csrf_of(page.text), **WA_FORM})
    assert again.status_code == 303 and again.headers["location"].endswith("flash=wa_connected")
    assert _only_account(wa_repo).id == account_id  # same id, reconnected in place
    assert _only_account(wa_repo).status.value == "active"
    assert _steps(ui_client.get(HOME).text)["channel"] == "true"
    final = ui_client.get(SETTINGS).text
    for secret in (SYS, SYS + "2", SEC, VERIFY):
        assert secret not in final


def test_whatsapp_disable_form_shape_and_explanation(
    ui_client: TestClient, graph_mock: respx.MockRouter
) -> None:
    login(ui_client)
    _stub_wa(graph_mock)
    token = csrf_of(ui_client.get(SETTINGS).text)
    ui_client.post(f"{SETTINGS}/whatsapp", data={"csrf": token, **WA_FORM})
    page = ui_client.get(SETTINGS).text
    form = re.search(r'<form[^>]*data-form="wa-disable".*?</form>', page, re.S).group(0)
    assert f'action="{SETTINGS}/whatsapp/disable"' in form and 'name="csrf"' in form
    assert 'type="checkbox" name="wa_confirm"' in form and "required" in form
    assert MESSAGES["wa.disable_explain"] in form  # "disable, not delete the conversations"
    assert "value=" not in re.search(r'<input[^>]*name="wa_sys"[^>]*>', page).group(0)


def test_whatsapp_disable_without_csrf_or_for_viewer_has_no_effect(
    ui_client: TestClient, graph_mock: respx.MockRouter, wa_repo: FakeWhatsAppRepository
) -> None:
    login(ui_client)
    _stub_wa(graph_mock)
    token = csrf_of(ui_client.get(SETTINGS).text)
    ui_client.post(f"{SETTINGS}/whatsapp", data={"csrf": token, **WA_FORM})
    for bad in ({}, {"csrf": "nope"}):
        res = ui_client.post(f"{SETTINGS}/whatsapp/disable", data={**bad, "wa_confirm": "yes"})
        assert res.status_code == 403
    assert _only_account(wa_repo).status.value == "active"

    ui_client.cookies.clear()
    login(ui_client, VIEWER_LOGIN)
    viewer_token = csrf_of(ui_client.get(SETTINGS).text)
    res = ui_client.post(
        f"{SETTINGS}/whatsapp/disable", data={"csrf": viewer_token, "wa_confirm": "yes"}
    )
    assert res.status_code == 403
    assert _only_account(wa_repo).status.value == "active"


def test_whatsapp_active_account_race_still_shows_the_409_message(
    ui_client: TestClient, graph_mock: respx.MockRouter
) -> None:
    """A stale connect form posted while the account is active ⇒ the existing 409 text."""
    login(ui_client)
    _stub_wa(graph_mock)
    token = csrf_of(ui_client.get(SETTINGS).text)
    first = ui_client.post(f"{SETTINGS}/whatsapp", data={"csrf": token, **WA_FORM})
    assert first.status_code == 303
    res = ui_client.post(f"{SETTINGS}/whatsapp", data={"csrf": token, **WA_FORM})
    assert res.status_code == 409 and MESSAGES["error.whatsapp_account_exists"] in res.text
    for secret in (SYS, SEC, VERIFY):
        assert secret not in res.text


def test_onboarding_channel_ignores_inactive_connection(ui_client: TestClient) -> None:
    from src.ui.router import _onboarding  # noqa: PLC0415

    class _Api:
        async def get(self, path: str, **_: object):  # noqa: ANN202
            if path.endswith("/connections"):
                return [{"status": "reauth_required", "pages": [{"is_selected": True}]}]
            if path.endswith("/content/items"):
                return {"items": []}
            raise AssertionError(path)

        async def json(self, *_: object, **__: object):  # noqa: ANN202
            raise AssertionError("unexpected")

    import asyncio  # noqa: PLC0415

    async def _go() -> object:
        from src.ui.api import ApiError  # noqa: PLC0415

        class Api(_Api):
            async def get(self, path: str, **kw: object):  # noqa: ANN202
                if "whatsapp" in path:
                    raise ApiError(404, {"detail": {"error": "whatsapp_account_not_found"}})
                return await super().get(path, **kw)

        return await _onboarding(Api(), [{"id": str(UUID(int=1))}])  # type: ignore[arg-type]

    steps = asyncio.run(_go())
    assert steps is not None
    assert {s["step"]: s["done"] for s in steps}["channel"] is False


# ----------------------------------------------------------------------- B-0.3 ---
def test_register_rejects_weak_password_with_the_arabic_weak_message(
    ui_client: TestClient,
) -> None:
    # the *real* ``RegisterRequest`` produces these 422 shapes (asserted below)
    with pytest.raises(Exception, match="string_too_short|at least"):
        RegisterRequest(email=NEW_EMAIL, password="x1y2", tenant_slug="ok-slug", tenant_name="n")
    short = _register(ui_client, password="x1y2", password_confirm="x1y2")
    same = _register(ui_client, password=NEW_EMAIL, password_confirm=NEW_EMAIL)
    for res in (short, same):
        assert res.status_code == 422
        assert MESSAGES["error.password_too_weak"] in res.text
        assert MESSAGES["error.http_422"] not in res.text
    assert "x1y2" not in short.text and "must not equal" not in same.text


@pytest.mark.parametrize(
    "over",
    [{"email": "not-an-email"}, {"tenant_slug": "Bad Slug!"}],
)
def test_register_other_422_is_not_reported_as_a_weak_password(
    ui_client: TestClient, over: dict[str, str]
) -> None:
    res = _register(ui_client, **over)
    assert res.status_code == 422
    assert MESSAGES["error.http_422"] in res.text
    assert MESSAGES["error.password_too_weak"] not in res.text


def test_register_overlong_password_falls_back_to_the_safe_arabic_message(
    ui_client: TestClient,
) -> None:
    long_pw = "p" * 129
    res = _register(ui_client, password=long_pw, password_confirm=long_pw)
    assert res.status_code == 422 and MESSAGES["error.http_422"] in res.text
    assert long_pw not in res.text


def test_only_weak_password_classifier_is_narrow() -> None:
    short = {"loc": ["body", "password"], "type": "string_too_short", "msg": "x"}
    same = {
        "loc": ["body"],
        "type": "value_error",
        "msg": "Value error, password must not equal the email address",
    }
    other = {"loc": ["body", "email"], "type": "value_error", "msg": "bad email"}
    assert _only_weak_password([short]) and _only_weak_password([same])
    assert not _only_weak_password([short, other]) and not _only_weak_password([])
    assert not _only_weak_password(["junk"])  # type: ignore[list-item]
    assert not _only_weak_password([{"loc": [], "type": "string_too_short"}])
    assert not _only_weak_password(
        [{"loc": ["body", "password"], "type": "string_too_long", "msg": "x"}]
    )


def test_new_password_constant_unused_guard() -> None:
    assert len(NEW_PASSWORD) >= 10 and PHONE_NUMBER_ID and httpx.Response(200).status_code == 200
