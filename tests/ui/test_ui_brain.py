"""SPRINT-14-B B-1/B-2 — the brand profile page, competitors and evidence refresh."""

from __future__ import annotations

import json
import re
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from src.modules.auth.domain.models import User
from src.modules.tenancy.domain.models import Tenant
from src.ui.router import I18N_PATH

from tests.conftest import BRAND_ID, TEST_PASSWORD
from tests.fakes import FakeBrandBrainRepository, FakeRateLimiter, FakeStore
from tests.ui.conftest import VIEWER_LOGIN, csrf_of, login

BRAIN = f"/app/b/{BRAND_ID}/brain"
MESSAGES = json.loads(I18N_PATH.read_text(encoding="utf-8"))
OUTSIDER = {"email": "outsider@example.com", "password": TEST_PASSWORD}
TENANT_X = UUID("00000000-0000-4000-8000-0000000000f1")
USER_X = UUID("00000000-0000-4000-8000-0000000000f2")


def _form(csrf: str, version: str = "1", **over: object) -> dict[str, object]:
    data: dict[str, object] = {
        "csrf": csrf,
        "version": version,
        "sector": "restaurants",
        "p_name": ["قهوة مختصة", ""],
        "p_desc": ["حبوب محمصة طازجة", ""],
        "p_price": ["120 جنيه", ""],
        "p_url": ["", ""],
        "a_segments": "طلاب\nموظفون",
        "a_regions": "القاهرة",
        "a_languages": "ar",
        "a_age_bands": "18-24",
        "t_voice": "ودود وقريب",
        "t_formality": "casual",
        "t_emoji": "light",
        "t_banned": "رخيص جداً",
        "g_kind": ["leads", "sales"],
        "g_target": ["٣٠ عميلاً", ""],
        "g_days": ["30", ""],
    }
    data.update(over)
    return data


def _page(client: TestClient) -> tuple[str, str]:
    res = client.get(BRAIN)
    assert res.status_code == 200, res.text
    return res.text, csrf_of(res.text)


def _outsider(store: FakeStore) -> None:
    store.tenants[TENANT_X] = Tenant(id=TENANT_X, slug="outsider", name="Outsider")
    store.users[USER_X] = User(
        id=USER_X,
        tenant_id=TENANT_X,
        email=OUTSIDER["email"],
        password_hash=f"fake${TEST_PASSWORD}",
    )


def test_brain_get_renders_arabic_fields_without_raw_json(ui_client: TestClient) -> None:
    login(ui_client)
    html, _ = _page(ui_client)
    assert MESSAGES["brain.title"] in html and 'data-form="profile"' in html
    assert 'name="version" value="1"' in html and 'name="t_banned"' in html
    assert html.count("data-product>") == 3 and html.count("data-goal>") == 2
    assert "reply_templates" not in html and "{" not in re.sub(r"<[^>]+>", "", html)
    assert f'href="{BRAIN}"' in html  # nav.brain on every brand page


def test_brain_save_roundtrip_sends_if_match_from_the_form(
    ui_client: TestClient, brain_repo: FakeBrandBrainRepository
) -> None:
    login(ui_client)
    _, csrf = _page(ui_client)
    res = ui_client.post(BRAIN, data=_form(csrf))
    assert res.status_code == 303 and res.headers["location"].endswith("/brain?flash=brain_saved")
    stored = brain_repo.profiles[BRAND_ID]
    assert stored.sector == "restaurants" and stored.tone.formality == "casual"
    assert [p.name for p in stored.products] == ["قهوة مختصة"]  # blank rows dropped
    assert stored.products[0].price_hint == "120 جنيه" and stored.products[0].url is None
    assert stored.audience.segments == ["طلاب", "موظفون"]
    assert [(g.kind, g.horizon_days) for g in stored.goals] == [("leads", 30)]
    assert stored.tone.banned_phrases == ["رخيص جداً"] and stored.tone.reply_templates == []
    after = ui_client.get(res.headers["location"])
    assert MESSAGES["brain.saved"] in after.text and "قهوة مختصة" in after.text
    assert 'name="version" value="2"' not in after.text  # first create keeps version 1
    assert '<option value="restaurants" selected>' in after.text


def test_brain_save_never_overwrites_reply_templates(
    ui_client: TestClient, brain_repo: FakeBrandBrainRepository
) -> None:
    login(ui_client)
    _, csrf = _page(ui_client)
    ui_client.post(BRAIN, data=_form(csrf))
    stored = brain_repo.profiles[BRAND_ID]
    brain_repo.profiles[BRAND_ID] = stored.model_copy(
        update={"tone": stored.tone.model_copy(update={"reply_templates": ["أهلاً بك"]})}
    )
    res = ui_client.post(BRAIN, data=_form(csrf, version="1", reply_templates="hacked"))
    assert res.status_code == 303
    assert brain_repo.profiles[BRAND_ID].tone.reply_templates == ["أهلاً بك"]


def test_brain_stale_version_gives_412_keeps_inputs_and_does_not_retry(
    ui_client: TestClient, brain_repo: FakeBrandBrainRepository
) -> None:
    login(ui_client)
    _, csrf = _page(ui_client)
    assert ui_client.post(BRAIN, data=_form(csrf)).status_code == 303  # v1 stored
    assert ui_client.post(BRAIN, data=_form(csrf, t_voice="نسخة ثانية")).status_code == 303  # v2
    stale = ui_client.post(BRAIN, data=_form(csrf, t_voice="نصي الذي كتبته", version="1"))
    assert stale.status_code == 412
    assert MESSAGES["error.profile_version_conflict"] in stale.text
    assert "نصي الذي كتبته" in stale.text and 'name="version" value="1"' in stale.text
    assert f'<a href="{BRAIN}">' in stale.text  # reload link, no blind retry
    assert brain_repo.profiles[BRAND_ID].version == 2
    assert brain_repo.profiles[BRAND_ID].tone.voice == "نسخة ثانية"


def test_brain_missing_version_is_a_safe_428(ui_client: TestClient) -> None:
    login(ui_client)
    _, csrf = _page(ui_client)
    data = _form(csrf)
    del data["version"]
    res = ui_client.post(BRAIN, data=data)
    assert res.status_code == 428 and MESSAGES["error.if_match_invalid"] in res.text


def test_brain_invalid_input_keeps_text_and_is_translated(ui_client: TestClient) -> None:
    login(ui_client)
    _, csrf = _page(ui_client)
    res = ui_client.post(BRAIN, data=_form(csrf, t_formality="shouting", t_voice="باقٍ هنا"))
    assert res.status_code == 422 and MESSAGES["error.http_422"] in res.text
    assert "باقٍ هنا" in res.text and "shouting" not in res.text


def test_brain_csrf_is_403_before_any_effect(
    ui_client: TestClient, brain_repo: FakeBrandBrainRepository
) -> None:
    login(ui_client)
    for path in (
        BRAIN,
        f"{BRAIN}/competitors",
        f"{BRAIN}/evidence/refresh",
        f"{BRAIN}/competitors/{BRAND_ID}/delete",
        f"{BRAIN}/competitors/{BRAND_ID}/refresh",
    ):
        res = ui_client.post(path, data=_form("bad"))
        assert res.status_code == 403, path
    assert not brain_repo.profiles and not brain_repo.competitors


def test_brain_viewer_is_read_only(
    ui_client: TestClient, brain_repo: FakeBrandBrainRepository
) -> None:
    login(ui_client, VIEWER_LOGIN)
    html, csrf = _page(ui_client)
    assert "data-readonly" in html and 'name="t_banned"' not in html
    assert 'type="submit">' + MESSAGES["brain.save"] not in html
    assert 'data-form="competitor"' not in html and "/evidence/refresh" not in html
    # no write form of any kind: the only <form> left is the shell's logout; no profile
    # form, version carrier or brain submit button
    assert html.count("<form") == 1 and 'action="/app/logout"' in html
    assert 'data-form="profile"' not in html and 'name="version"' not in html
    assert html.count('type="submit"') == 1
    assert html.count("<fieldset disabled") == html.count("<fieldset")
    res = ui_client.post(BRAIN, data=_form(csrf))
    assert res.status_code == 403
    assert not brain_repo.profiles
    for path, data in (
        (f"{BRAIN}/competitors", {"csrf": csrf, "c_name": "x"}),
        (f"{BRAIN}/evidence/refresh", {"csrf": csrf}),
    ):
        assert ui_client.post(path, data=data).status_code == 403
    assert not brain_repo.competitors


def test_brain_non_member_gets_404_and_no_write(
    ui_client: TestClient, store: FakeStore, brain_repo: FakeBrandBrainRepository
) -> None:
    _outsider(store)
    login(ui_client, OUTSIDER)
    assert ui_client.get(BRAIN).status_code == 404
    res = ui_client.post(BRAIN, data=_form(csrf_of(ui_client.get("/app/").text)))
    assert res.status_code == 404 and not brain_repo.profiles
    res = ui_client.post(
        f"{BRAIN}/competitors", data={"csrf": csrf_of(ui_client.get("/app/").text), "c_name": "x"}
    )
    assert res.status_code == 404 and not brain_repo.competitors


def test_brain_anonymous_is_redirected(ui_client: TestClient) -> None:
    assert ui_client.get(BRAIN).status_code == 303
    assert ui_client.post(BRAIN, data={}).status_code == 303


# ------------------------------------------------------------------- B-2 ----
def _add(client: TestClient, csrf: str, name: str, **over: str) -> int:
    data = {"csrf": csrf, "c_name": name, "c_kind": "direct", "c_host": f"{name}.example.com"}
    data.update(over)
    return client.post(f"{BRAIN}/competitors", data=data).status_code


def test_competitor_limit_is_three_and_fourth_is_rejected(
    ui_client: TestClient, brain_repo: FakeBrandBrainRepository
) -> None:
    login(ui_client)
    _, csrf = _page(ui_client)
    assert [_add(ui_client, csrf, n) for n in ("a", "b", "c")] == [303, 303, 303]
    html, csrf = _page(ui_client)
    assert 'data-form="competitor"' not in html and "3 / 3" in html  # form hidden at the cap
    res = ui_client.post(
        f"{BRAIN}/competitors", data={"csrf": csrf, "c_name": "d", "c_host": "d.example.com"}
    )
    assert res.status_code == 409 and MESSAGES["error.competitor_limit_reached"] in res.text
    assert len(brain_repo.competitors) == 3


def test_competitor_add_hosts_and_translated_errors(
    ui_client: TestClient, brain_repo: FakeBrandBrainRepository
) -> None:
    login(ui_client)
    _, csrf = _page(ui_client)
    res = ui_client.post(
        f"{BRAIN}/competitors",
        data={
            "csrf": csrf,
            "c_name": "منافس",
            "c_host": "rival.example.com",
            "c_hosts": "cdn.example.com",
        },
    )
    assert res.status_code == 303
    (comp,) = brain_repo.competitors.values()
    assert comp.allowed_hosts == ["rival.example.com", "cdn.example.com"]
    html = ui_client.get(res.headers["location"]).text
    assert "منافس" in html and MESSAGES["brain.competitor_added"] in html
    dup = ui_client.post(f"{BRAIN}/competitors", data={"csrf": csrf, "c_name": "منافس"})
    assert dup.status_code == 409 and MESSAGES["error.competitor_exists"] in dup.text
    bad = ui_client.post(
        f"{BRAIN}/competitors", data={"csrf": csrf, "c_name": "x", "c_host": "not a host"}
    )
    assert bad.status_code == 422 and MESSAGES["error.host_invalid"] in bad.text
    social = ui_client.post(
        f"{BRAIN}/competitors", data={"csrf": csrf, "c_name": "y", "c_host": "facebook.com"}
    )
    assert social.status_code == 422 and MESSAGES["error.host_blocked_platform"] in social.text
    too_many = ui_client.post(
        f"{BRAIN}/competitors",
        data={"csrf": csrf, "c_name": "z", "c_hosts": "a.com\nb.com\nc.com\nd.com"},
    )
    assert too_many.status_code == 422


def test_competitor_delete_and_refresh_single_and_global(
    ui_client: TestClient, brain_repo: FakeBrandBrainRepository
) -> None:
    login(ui_client)
    _, csrf = _page(ui_client)
    _add(ui_client, csrf, "a")
    _add(ui_client, csrf, "b")
    first, second = list(brain_repo.competitors)
    # single competitor refresh: legal sources are not wired in the test app (no direct HTTP)
    one = ui_client.post(f"{BRAIN}/competitors/{first}/refresh", data={"csrf": csrf})
    assert one.status_code == 303 and "flash=competitor_" in one.headers["location"]
    rest = ui_client.post(f"{BRAIN}/evidence/refresh", data={"csrf": csrf})
    assert rest.status_code == 303 and rest.headers["location"].endswith("evidence_refreshed")
    html = ui_client.get(rest.headers["location"]).text
    assert 'data-grade="measured"' in html and "data-confidence" in html
    assert MESSAGES["grade.measured"] in html
    gone = ui_client.post(f"{BRAIN}/competitors/{first}/delete", data={"csrf": csrf})
    assert gone.status_code == 303 and first not in brain_repo.competitors
    assert second in brain_repo.competitors
    miss = ui_client.post(f"{BRAIN}/competitors/{first}/refresh", data={"csrf": csrf})
    assert miss.status_code == 404 and MESSAGES["error.competitor_not_found"] in miss.text


def test_refresh_rate_limit_keeps_retry_after(
    ui_client: TestClient, limiter: FakeRateLimiter
) -> None:
    login(ui_client)
    _, csrf = _page(ui_client)
    limiter.reject_after = len(limiter.hits)  # the next brain refresh is throttled upstream
    last = ui_client.post(f"{BRAIN}/evidence/refresh", data={"csrf": csrf})
    assert last.status_code == 429 and MESSAGES["error.refresh_rate_limited"] in last.text
    assert int(last.headers["retry-after"]) >= 1


def test_competitor_isolation_between_brands(
    ui_client: TestClient, brain_repo: FakeBrandBrainRepository, store: FakeStore
) -> None:
    from tests.conftest import BRAND2_ID

    login(ui_client)
    _, csrf = _page(ui_client)
    _add(ui_client, csrf, "a")
    (cid,) = brain_repo.competitors
    other = f"/app/b/{BRAND2_ID}/brain/competitors/{cid}"
    res = ui_client.post(f"{other}/delete", data={"csrf": csrf})
    assert res.status_code in (404, 303) and cid in brain_repo.competitors
    page = ui_client.get(f"/app/b/{BRAND2_ID}/brain").text
    assert "data-competitor=" not in page


@pytest.mark.parametrize("flash", ["bogus", "<script>"])
def test_unknown_flash_keys_render_nothing(ui_client: TestClient, flash: str) -> None:
    login(ui_client)
    res = ui_client.get(f"{BRAIN}?flash={flash}")
    assert res.status_code == 200 and "data-flash" not in res.text
