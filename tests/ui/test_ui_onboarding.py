"""SPRINT-14-A — zero-API self-serve onboarding (§2 of the directive).

Every test drives the **browser surface only** (``TestClient`` with cookies, no redirects
followed, zero direct ``/api`` calls); the fakes behind the API are the ones every other UI
test uses (``tests/ui/conftest.py``). Meta and WhatsApp Cloud API are ``respx``-stubbed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from src.core.oauth_state import OAuthStateSigner
from src.modules.tenancy.domain.models import Brand
from src.ui.router import WA_FIELDS
from src.ui.router import router as ui_router
from src.ui.slug import derive_slug, slug_retry

from tests.conftest import BRAND_ID, FAKE_STATE_SECRET, OWNER_ID
from tests.fakes import FakeBrandBrainRepository, FakeRateLimiter, FakeStore
from tests.modules.social_connect.test_router import _stub_happy_path
from tests.modules.whatsapp.conftest import PHONE_NUMBER_ID
from tests.ui.conftest import LOGIN, OWNER_LOGIN, SETTINGS, csrf_of, login

REGISTER = "/app/register"
HOME = "/app/"
NEW_EMAIL = "new-owner@example.com"
NEW_PASSWORD = "a-brand-new-passphrase-14a"  # noqa: S105 - test fixture
SYS = "EAAsystem-user-token-1234567890"  # noqa: S105 - fake secret for assertions
SEC = "app-secret-abcdef"  # noqa: S105
VERIFY = "verify-token-xyz"  # noqa: S105
WA_FORM = {
    "wa_pnid": PHONE_NUMBER_ID,
    "wa_waba": "WABA_1",
    "wa_phone": "+20100000000",
    "wa_sys": SYS,
    "wa_sec": SEC,
    "wa_verify": VERIFY,
}
TEMPLATES = Path("src/ui/templates")
MESSAGES = json.loads(Path("src/ui/i18n/ar.json").read_text(encoding="utf-8"))
SAMPLE_CID = "00000000-0000-4000-8000-0000000000ff"


def _register(client: TestClient, **over: str) -> httpx.Response:
    form = {
        "email": NEW_EMAIL,
        "password": NEW_PASSWORD,
        "password_confirm": NEW_PASSWORD,
        "tenant_name": "شركة النيل للتسويق",
        **over,
    }
    return client.post(REGISTER, data=form)


def _create_brand(client: TestClient, name: str, sector: str = "restaurants", **o: str):
    token = csrf_of(client.get(HOME).text)
    return client.post("/app/brands", data={"csrf": token, "name": name, "sector": sector, **o})


def _steps(html: str) -> dict[str, str]:
    return dict(re.findall(r'data-step="(\w+)" data-done="(\w+)"', html))


def _brand_of(location: str) -> UUID:
    return UUID(location.split("/b/")[1].split("/")[0])


def _tenant_of(store: FakeStore, email: str) -> UUID:
    return next(u.tenant_id for u in store.users.values() if u.email == email)


def _stub_wa(graph_mock: respx.MockRouter) -> respx.Route:
    return graph_mock.get(f"/{PHONE_NUMBER_ID}").mock(
        return_value=httpx.Response(
            200, json={"display_phone_number": "+20 100 000 0000", "verified_name": "Nile"}
        )
    )


# ------------------------------------------------------------ 1. full browser path ---
def test_full_browser_path_register_brand_whatsapp(
    ui_client: TestClient, graph_mock: respx.MockRouter, brain_repo: FakeBrandBrainRepository
) -> None:
    assert ui_client.get(REGISTER).status_code == 200
    res = _register(ui_client)
    assert res.status_code == 303 and res.headers["location"] == HOME, res.text
    assert ui_client.cookies.get("app_session") and ui_client.cookies.get("app_access")

    home = ui_client.get(HOME)
    assert home.status_code == 200 and "data-onboarding" in home.text
    assert _steps(home.text) == {
        "account": "true",
        "brand": "false",
        "channel": "false",
        "draft": "false",
    }
    assert 'data-form="first-brand"' in home.text

    res = _create_brand(ui_client, "مطعم النيل", "clinics")
    assert res.status_code == 303, res.text
    brand_id = _brand_of(res.headers["location"])
    assert res.headers["location"] == f"/app/b/{brand_id}/settings#connect"
    assert brain_repo.profiles[brand_id].sector == "clinics"  # A-2: sector persisted

    settings_page = ui_client.get(f"/app/b/{brand_id}/settings")
    assert settings_page.status_code == 200 and 'data-form="wa-connect"' in settings_page.text
    home = ui_client.get(HOME)
    assert _steps(home.text)["brand"] == "true" and _steps(home.text)["channel"] == "false"
    assert f"/app/b/{brand_id}/settings#connect" in home.text

    _stub_wa(graph_mock)
    token = csrf_of(settings_page.text)
    res = ui_client.post(f"/app/b/{brand_id}/settings/whatsapp", data={"csrf": token, **WA_FORM})
    assert res.status_code == 303, res.text
    assert res.headers["location"] == f"/app/b/{brand_id}/settings?flash=wa_connected"
    home = ui_client.get(HOME)
    assert _steps(home.text)["channel"] == "true"


# ----------------------------------------------------------------- 2. A-1 errors ---
def test_register_duplicate_email_is_409_arabic(ui_client: TestClient) -> None:
    res = _register(ui_client, email=OWNER_LOGIN["email"])
    assert res.status_code == 409 and MESSAGES["error.email_taken"] in res.text
    assert "app_session" not in res.headers.get("set-cookie", "")


def test_register_password_mismatch_is_local_400(ui_client: TestClient, store: FakeStore) -> None:
    users_before = len(store.users)
    res = _register(ui_client, password_confirm="something-else-entirely")
    assert res.status_code == 400 and MESSAGES["register.password_mismatch"] in res.text
    assert len(store.users) == users_before  # FakeStore untouched ⇒ no API call


def test_register_rate_limited_is_429_arabic(
    ui_client: TestClient, limiter: FakeRateLimiter
) -> None:
    limiter.reject_after = 0
    res = _register(ui_client)
    assert res.status_code == 429 and MESSAGES["error.rate_limited"] in res.text


def test_register_weak_password_shows_422_arabic(ui_client: TestClient) -> None:
    res = _register(ui_client, password="short", password_confirm="short")
    assert res.status_code == 422 and MESSAGES["error.password_too_weak"] in res.text


def test_register_tenant_slug_conflict_retries_automatically(
    ui_client: TestClient, store: FakeStore
) -> None:
    # fixed world: tenant slug ``nile-owner`` exists ⇒ derived ``nile-owner`` collides once
    res = _register(ui_client, tenant_name="Nile Owner")
    assert res.status_code == 303, res.text
    assert store.tenants[_tenant_of(store, NEW_EMAIL)].slug == "nile-owner-2"


def test_register_manual_tenant_slug_conflict_is_409(ui_client: TestClient) -> None:
    res = _register(ui_client, tenant_slug="nile-owner")
    assert res.status_code == 409 and MESSAGES["error.tenant_slug_taken"] in res.text


def test_login_and_register_link_each_other(ui_client: TestClient) -> None:
    assert 'href="/app/register"' in ui_client.get(LOGIN).text
    assert 'href="/app/login"' in ui_client.get(REGISTER).text


# ------------------------------------------------------------------- 3. A-2 slug ---
def test_derive_slug_rules() -> None:
    arabic = derive_slug("مطعم النيل", "brand")
    assert re.fullmatch(r"brand-[0-9a-f]{8}", arabic)
    assert derive_slug("Nile Coffee 2", "brand") == "nile-coffee-2"
    assert derive_slug("  Café -- Nür!! ", "org") == "cafe-nur"
    assert derive_slug("ab", "org").startswith("org-")
    assert len(derive_slug("x" * 200, "org")) == 64
    assert slug_retry("nile-coffee", 1) == "nile-coffee-2"
    assert re.fullmatch(r"nile-coffee-[0-9a-f]{4}", slug_retry("nile-coffee", 2))


def test_first_brand_slug_conflict_retries_then_succeeds(
    ui_client: TestClient, store: FakeStore, brain_repo: FakeBrandBrainRepository
) -> None:
    _register(ui_client)
    tenant_id = _tenant_of(store, NEW_EMAIL)
    seeded = Brand(owner_tenant_id=tenant_id, slug="nile-coffee", name="Seed")
    store.brands[seeded.id] = seeded
    res = _create_brand(ui_client, "Nile Coffee")
    assert res.status_code == 303, res.text
    created = [b for b in store.brands.values() if b.owner_tenant_id == tenant_id]
    assert sorted(b.slug for b in created) == ["nile-coffee", "nile-coffee-2"]
    brand_id = next(b.id for b in created if b.slug == "nile-coffee-2")
    assert brain_repo.profiles[brand_id].sector == "restaurants"


def test_first_brand_manual_slug_conflict_fails_immediately(
    ui_client: TestClient, store: FakeStore
) -> None:
    _register(ui_client)
    tenant_id = _tenant_of(store, NEW_EMAIL)
    seeded = Brand(owner_tenant_id=tenant_id, slug="taken-slug", name="S")
    store.brands[seeded.id] = seeded
    res = _create_brand(ui_client, "Any", slug="taken-slug")
    assert res.status_code == 409 and MESSAGES["error.brand_slug_taken"] in res.text
    assert sum(1 for b in store.brands.values() if b.owner_tenant_id == tenant_id) == 1


def test_first_brand_bad_manual_slug_is_arabic_422(ui_client: TestClient) -> None:
    _register(ui_client)
    res = _create_brand(ui_client, "Any", slug="BAD SLUG!")
    assert res.status_code == 422 and MESSAGES["error.slug_invalid"] in res.text


def test_first_brand_sector_failure_does_not_fail_creation(
    ui_client: TestClient, store: FakeStore, brain_repo: FakeBrandBrainRepository
) -> None:
    """``PUT …/brain/profile`` answering an error (here: a version conflict the fake raises
    as ``IntegrityError`` ⇒ 412) must not undo the creation — a flash only (A-2)."""
    from sqlalchemy.exc import IntegrityError

    _register(ui_client)
    brain_repo.insert_profile_error = IntegrityError("x", {}, Exception("dup"))
    res = _create_brand(ui_client, "مطعم")
    assert res.status_code == 303 and "flash=sector_failed" in res.headers["location"]
    brand_id = _brand_of(res.headers["location"])
    assert brand_id in store.brands and brand_id not in brain_repo.profiles
    page = ui_client.get(res.headers["location"].split("#")[0])
    assert page.status_code == 200 and MESSAGES["brands.sector_failed"] in page.text


# ------------------------------------------------------------------ 4. A-3 bridge ---
@pytest.fixture
def signer() -> OAuthStateSigner:
    return OAuthStateSigner(FAKE_STATE_SECRET)


def test_meta_bridge_without_session_keeps_code_and_state_in_next(
    ui_client: TestClient,
) -> None:
    res = ui_client.get("/app/social/meta/callback", params={"code": "x", "state": "y"})
    assert res.status_code == 303
    # 14-B B-0.1: the inner query is quoted once, so ``&state=`` stays inside ``next``
    assert (
        res.headers["location"] == f"{LOGIN}?next=/app/social/meta/callback%3Fcode%3Dx%26state%3Dy"
    )


def test_meta_bridge_completes_and_pages_screen_selects(
    ui_client: TestClient, graph_mock: respx.MockRouter, signer: OAuthStateSigner
) -> None:
    login(ui_client)
    _stub_happy_path(graph_mock, pages=2)
    state = signer.issue(OWNER_ID, BRAND_ID)
    res = ui_client.get("/app/social/meta/callback", params={"code": "CODE", "state": state})
    assert res.status_code == 303, res.text
    location = res.headers["location"]
    assert location.startswith(f"/app/b/{BRAND_ID}/settings/connections/")
    assert location.endswith("/pages")
    cid = location.split("/connections/")[1].split("/")[0]

    page = ui_client.get(location)
    assert page.status_code == 200 and 'data-screen="pages"' in page.text
    page_ids = re.findall(r'name="page_ids" value="([^"]+)"', page.text)
    assert len(page_ids) == 2 and "checked" not in page.text.split("data-pages")[1]
    token = csrf_of(page.text)

    empty = ui_client.post(location, data={"csrf": token})
    assert empty.status_code == 400 and MESSAGES["pages.none"] in empty.text

    res = ui_client.post(location, data={"csrf": token, "page_ids": page_ids[:1]})
    assert res.status_code == 303, res.text
    assert res.headers["location"] == f"{SETTINGS}?flash=pages_selected"
    settings_page = ui_client.get(res.headers["location"])
    assert MESSAGES["pages.saved"] in settings_page.text
    assert f'data-connection="{cid}"' in settings_page.text
    again = ui_client.get(location).text
    assert f'value="{page_ids[0]}" checked' in again  # pre-selected on revisit
    # A-5: a selected page counts as a channel
    assert _steps(ui_client.get(HOME).text)["channel"] == "true"


def test_meta_bridge_user_denied_lands_on_home_with_arabic_flash(ui_client: TestClient) -> None:
    login(ui_client)
    res = ui_client.get(
        "/app/social/meta/callback",
        params={"error": "access_denied", "error_description": "The user denied"},
    )
    assert res.status_code == 303 and res.headers["location"] == "/app/?flash=user_denied"
    home = ui_client.get(res.headers["location"])
    assert home.status_code == 200 and MESSAGES["error.user_denied"] in home.text


def test_meta_bridge_invalid_state_is_translated(ui_client: TestClient) -> None:
    login(ui_client)
    res = ui_client.get("/app/social/meta/callback", params={"code": "c", "state": "garbage"})
    assert res.headers["location"] == "/app/?flash=invalid_oauth_state"
    assert MESSAGES["error.invalid_oauth_state"] in ui_client.get(res.headers["location"]).text


def test_settings_connect_hint_present(ui_client: TestClient) -> None:
    login(ui_client)
    html = ui_client.get(SETTINGS).text
    assert 'id="connect"' in html and MESSAGES["settings.connect_hint"] in html
    assert 'target="_blank"' in html


# ---------------------------------------------------------------- 5. A-4 secrecy ---
def test_whatsapp_form_field_names_and_no_secret_echo(
    ui_client: TestClient, graph_mock: respx.MockRouter
) -> None:
    login(ui_client)
    html = ui_client.get(SETTINGS).text
    for field in WA_FIELDS:
        assert f'name="{field}"' in html
    for secret_field in ("wa_sys", "wa_sec", "wa_verify"):
        tag = re.search(rf'<input[^>]*name="{secret_field}"[^>]*>', html).group(0)
        assert 'type="password"' in tag and 'autocomplete="new-password"' in tag
        assert "value=" not in tag
    route = _stub_wa(graph_mock)
    token = csrf_of(html)
    res = ui_client.post(f"{SETTINGS}/whatsapp", data={"csrf": token, **WA_FORM})
    assert res.status_code == 303 and route.called
    after = ui_client.get(res.headers["location"])
    assert after.status_code == 200 and MESSAGES["wa.connected_ok"] in after.text
    for secret_value in (SYS, SEC, VERIFY):
        assert secret_value not in after.text
    assert PHONE_NUMBER_ID in after.text and "data-wa-phone" in after.text
    assert 'data-form="wa-rotate"' in after.text
    token = csrf_of(after.text)
    res = ui_client.post(
        f"{SETTINGS}/whatsapp/rotate", data={"csrf": token, "wa_sys": SYS + "x", "wa_sec": SEC}
    )
    assert res.status_code == 303 and res.headers["location"].endswith("flash=wa_rotated")
    assert SYS not in ui_client.get(res.headers["location"]).text


def test_whatsapp_422_names_the_short_field_in_arabic(ui_client: TestClient) -> None:
    login(ui_client)
    token = csrf_of(ui_client.get(SETTINGS).text)
    res = ui_client.post(
        f"{SETTINGS}/whatsapp", data={"csrf": token, **WA_FORM, "wa_sys": "tooshort"}
    )
    assert res.status_code == 422
    assert MESSAGES["wa.field_short"].format(field=MESSAGES["wa.sys"]) in res.text
    assert "tooshort" not in res.text


def test_whatsapp_rejected_credentials_is_arabic_422(
    ui_client: TestClient, graph_mock: respx.MockRouter
) -> None:
    login(ui_client)
    graph_mock.get(f"/{PHONE_NUMBER_ID}").mock(
        return_value=httpx.Response(401, json={"error": {"message": "x", "code": 190}})
    )
    token = csrf_of(ui_client.get(SETTINGS).text)
    res = ui_client.post(f"{SETTINGS}/whatsapp", data={"csrf": token, **WA_FORM})
    assert res.status_code == 422
    assert MESSAGES["error.whatsapp_credentials_invalid"] in res.text


# ---------------------------------------------------------------- 6. A-5 states ---
def test_onboarding_bar_states(ui_client: TestClient) -> None:
    _register(ui_client)
    html = ui_client.get(HOME).text
    assert _steps(html)["brand"] == "false" and 'href="/app/#create"' in html
    res = _create_brand(ui_client, "عيادة")
    brand_id = _brand_of(res.headers["location"])
    html = ui_client.get(HOME).text
    assert _steps(html)["channel"] == "false"
    assert f'href="/app/b/{brand_id}/settings#connect"' in html
    assert "data-brands" in html and 'data-form="first-brand"' not in html


def test_onboarding_bar_shown_for_fixed_owner_without_channel(ui_client: TestClient) -> None:
    login(ui_client)
    html = ui_client.get(HOME).text
    assert "data-brands" in html and "data-onboarding" in html
    assert _steps(html)["channel"] == "false" and _steps(html)["draft"] == "false"


async def test_onboarding_bar_hidden_once_all_four_done(
    ui_client: TestClient, wa_account, content_repo
) -> None:
    """Channel (WA account) + first draft present ⇒ the bar disappears entirely."""
    from src.modules.content.domain.models import (
        Channel,
        ContentItem,
        ContentRevision,
        ContentStatus,
    )

    login(ui_client)
    html = ui_client.get(HOME).text
    assert _steps(html)["channel"] == "true" and _steps(html)["draft"] == "false"
    item = ContentItem(
        brand_id=BRAND_ID,
        created_by_tenant_id=OWNER_ID,
        channel=Channel.FACEBOOK_PAGE,
        status=ContentStatus.DRAFT,
        current_revision_id=None,
    )
    revision = ContentRevision(
        item_id=item.id,
        revision_no=1,
        body="أول مسودة",
        provenance={"kind": "human"},
        created_by_tenant_id=OWNER_ID,
    )
    await content_repo.create_item(item, revision)
    html = ui_client.get(HOME).text
    assert "data-onboarding" not in html and "data-brands" in html


# --------------------------------------------------------------------- 7. CSRF ---
NEW_ROUTES = sorted(
    (m, r.path)
    for r in ui_router.routes
    for m in sorted(getattr(r, "methods", None) or ())
    if m != "HEAD"
    and (
        "register" in r.path
        or r.path == "/app/brands"
        or "callback" in r.path
        or r.path.endswith("/pages")
        or "whatsapp" in r.path
    )
)
NEW_POSTS = [p for m, p in NEW_ROUTES if m == "POST" and p != "/app/register"]


def test_eight_new_app_routes_exist() -> None:
    """The directive's §1 table lists 8 new ``/app`` routes (2 + 1 + 3 + 2 + 0) ⇒ 29 + 8 = 37
    (its "9 ⇒ 38" line is an arithmetic slip — documented in §6-هـ). 14-B B-0.2 adds the WA
    ``disable`` POST ⇒ 9 / 38."""
    assert len(NEW_ROUTES) == 9, NEW_ROUTES
    assert (
        len(ui_router.routes) == 44
    )  # 14-B B-1/B-2: +6 (brain GET/POST, competitors ×3, evidence)


@pytest.mark.parametrize("path", NEW_POSTS)
def test_new_posts_require_csrf(ui_client: TestClient, path: str) -> None:
    login(ui_client)
    concrete = path.replace("{brand_id}", str(BRAND_ID)).replace("{connection_id}", SAMPLE_CID)
    for bad in ({}, {"csrf": "nope"}):
        res = ui_client.post(concrete, data={**bad, "name": "x", "page_ids": "1", **WA_FORM})
        assert res.status_code == 403, (path, bad, res.status_code)


def test_new_routes_redirect_without_session_except_register(ui_client: TestClient) -> None:
    assert ui_client.get("/app/register").status_code == 200
    assert ui_client.post("/app/brands", data={"name": "x"}).status_code == 303
    for method, path in NEW_ROUTES:
        if "register" in path:
            continue
        concrete = path.replace("{brand_id}", str(BRAND_ID)).replace("{connection_id}", SAMPLE_CID)
        res = ui_client.request(method, concrete, data={"csrf": "x"})
        assert res.status_code == 303 and res.headers["location"].startswith(LOGIN), path


# --------------------------------------------------------------------- 8. i18n ---
def test_every_template_key_exists_in_ar_json() -> None:
    keys: set[str] = set()
    for path in TEMPLATES.rglob("*.html"):
        # static keys only — ``t("prefix." ~ var)`` is dynamic and checked by the screen tests
        keys.update(
            k
            for k in re.findall(
                r"""(?<![\w.])t\(\s*["']([a-z_.0-9]+)["']\s*[,)]""", path.read_text("utf-8")
            )
            if not k.endswith(".")
        )
    missing = sorted(k for k in keys if k not in MESSAGES)
    assert not missing, missing
    for name in ("pnid", "waba", "phone", "sys", "sec", "verify", "connect", "update", "rotate"):
        assert f"wa.{name}" in MESSAGES
    assert "wa.connected_as" in MESSAGES


def test_templates_have_no_literal_arabic_and_no_secret_names() -> None:
    arabic = re.compile(r"[\u0600-\u06FF]")
    banned = re.compile(r"gAAAA|system_token|api_key|app_secret|verify_token")
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text("utf-8")
        assert not arabic.search(text), path
        assert not banned.search(text), path
