"""SPRINT-12.1-B D — admin AI catalog (4 tabs + probe), admin entitlements, Umami tag,
playbooks card. Non-admins get a 404 page before any API call (D-0)."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.config import Settings, get_settings
from src.modules.ai_gateway.catalog.router import get_catalog_admin_service
from src.modules.ai_gateway.contract import AIError, ErrorCategory
from src.modules.ai_gateway.dependencies import get_host_resolver
from src.modules.brand_brain.playbooks import SECTORS
from src.ui.router import I18N_PATH

from tests.conftest import BRAND_ID, OWNER_USER_ID
from tests.fakes import FakeCatalogRepository, FakeWhatsAppRepository
from tests.ui.conftest import TODAY, csrf_of, login

HX = {"HX-Request": "true"}
MESSAGES = json.loads(I18N_PATH.read_text(encoding="utf-8"))
ADMIN_AI = "/app/admin/ai"
ADMIN_ENT = "/app/admin/entitlements"
# Assembled at import time so no secret-shaped literal sits in the repository (gitleaks).
RAW_KEY = "".join(("sk-", "UI-ADMIN-TEST-", "0123456789abcdef"))
PUBLIC_URL = "https://api.example-llm.com/v1"
SCRIPT_URL = "https://cloud.umami.is/script.js"


async def _public_resolver(host: str, port: int) -> list[tuple]:
    return [(2, 1, 6, "", ("104.18.6.192", port))]


@pytest.fixture
def catalog_repo() -> FakeCatalogRepository:
    return FakeCatalogRepository()


def _reconfigure(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    """The UI router reads ``get_settings()`` directly (lru-cached, not a ``Depends``), so a
    dependency override alone would not reach it: set the env and clear the cache (the
    autouse ``_test_env`` fixture clears it again on teardown)."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def admin_ui(
    ui_app: FastAPI,
    catalog_repo: FakeCatalogRepository,
    encryption: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """OWNER is a platform admin; the catalog admin service runs over the in-memory repo."""
    from src.modules.ai_gateway.catalog.admin_service import CatalogAdminService

    admin_settings = _reconfigure(monkeypatch, PLATFORM_ADMIN_USER_IDS=str(OWNER_USER_ID))
    assert OWNER_USER_ID in admin_settings.platform_admin_user_ids
    ui_app.dependency_overrides[get_settings] = lambda: admin_settings
    ui_app.dependency_overrides[get_catalog_admin_service] = lambda: CatalogAdminService(
        catalog_repo,
        encryption=encryption,
        allow_local_hosts=admin_settings.ai_allow_local_provider_hosts,
        invalidate=lambda: None,
        resolver=_public_resolver,
    )
    ui_app.dependency_overrides[get_host_resolver] = lambda: _public_resolver
    return ui_app


def _attr(html: str, name: str) -> str:
    start = html.index(f'{name}="') + len(name) + 2
    return html[start : html.index('"', start)]


def _create_provider(client: TestClient, csrf: str, **extra: str) -> str:
    data = {
        "csrf": csrf,
        "slug": "openai-main",
        "kind": "openai_compatible",
        "base_url": PUBLIC_URL,
    }
    data.update(extra)
    res = client.post(f"{ADMIN_AI}/providers/create", data=data, headers=HX)
    assert res.status_code == 200 and MESSAGES["admin.saved"] in res.text, res.text
    return _attr(res.text, "data-provider")


def _create_model(client: TestClient, csrf: str, provider_id: str, **extra: str) -> str:
    data = {
        "csrf": csrf,
        "provider_id": provider_id,
        "model_name": "gpt-4o-mini",
        "capability": "text",
        "prompt_per_1k": "0.00015",
        "completion_per_1k": "0.0006",
    }
    data.update(extra)
    res = client.post(f"{ADMIN_AI}/models/create", data=data, headers=HX)
    assert res.status_code == 200 and MESSAGES["admin.saved"] in res.text, res.text
    return _attr(res.text, "data-model")


# ------------------------------------------------------------------- D-0 ---
def test_non_admin_gets_404_page_not_403(ui_client: TestClient) -> None:
    login(ui_client)  # OWNER, but PLATFORM_ADMIN_USER_IDS is empty in the plain ``ui_app``
    for path in (ADMIN_AI, f"{ADMIN_AI}?tab=probe", ADMIN_ENT):
        res = ui_client.get(path)
        assert res.status_code == 404, (path, res.status_code)
        assert MESSAGES["error.not_found"] in res.text and "platform_admin" not in res.text
    html = ui_client.get(TODAY).text
    assert 'data-nav="admin"' not in html
    csrf = csrf_of(html)
    res = ui_client.post(f"{ADMIN_AI}/providers/create", data={"csrf": csrf, "slug": "x"})
    assert res.status_code == 404
    # no session ⇒ the usual redirect, never a 404 (nothing about the panel is disclosed)
    ui_client.cookies.clear()
    res = ui_client.get(ADMIN_AI, headers=HX)
    assert res.status_code == 200 and res.headers["HX-Redirect"].startswith("/app/login")
    assert ui_client.get(ADMIN_ENT).status_code == 303


# ------------------------------------------------------------- D-1 tabs ---
def test_admin_ai_renders_four_tabs_and_nav_link(admin_ui: FastAPI, ui_client: TestClient) -> None:
    login(ui_client)
    res = ui_client.get(ADMIN_AI)
    assert res.status_code == 200, res.text
    for tab in ("providers", "models", "routing", "probe"):
        assert f"?tab={tab}" in res.text and MESSAGES[f"admin.tab_{tab}"] in res.text
    assert 'data-table="providers"' in res.text and 'data-nav="admin"' in res.text
    markers = (
        ("models", 'data-table="models"'),
        ("routing", "data-purpose="),
        ("probe", 'data-form="probe"'),
    )
    for tab, marker in markers:
        part = ui_client.get(f"{ADMIN_AI}?tab={tab}", headers=HX)
        assert part.status_code == 200 and "<html" not in part.text and marker in part.text


def test_admin_provider_create_never_echoes_the_key(
    admin_ui: FastAPI, ui_client: TestClient, catalog_repo: FakeCatalogRepository
) -> None:
    login(ui_client)
    csrf = csrf_of(ui_client.get(ADMIN_AI).text)
    res = ui_client.post(
        f"{ADMIN_AI}/providers/create",
        data={
            "csrf": csrf,
            "slug": "openai-main",
            "kind": "openai_compatible",
            "base_url": PUBLIC_URL,
            "credential": RAW_KEY,
        },
        headers=HX,
    )
    assert res.status_code == 200, res.text
    assert RAW_KEY not in res.text and 'data-has-credential="true"' in res.text
    page = ui_client.get(ADMIN_AI).text
    assert RAW_KEY not in page and 'data-has-credential="true"' in page and "hx-confirm=" in page
    assert len(catalog_repo.providers) == 1
    provider_id = str(next(iter(catalog_repo.providers)))
    res = ui_client.post(
        f"{ADMIN_AI}/providers/update",
        data={"csrf": csrf, "id": provider_id, "enabled": "false"},
        headers=HX,
    )
    assert res.status_code == 200 and MESSAGES["admin.saved"] in res.text
    assert next(iter(catalog_repo.providers.values())).enabled is False
    # host-not-allowed + conflict ⇒ translated flashes, codes never leak
    res = ui_client.post(
        f"{ADMIN_AI}/providers/create",
        data={
            "csrf": csrf,
            "slug": "local",
            "kind": "openai_compatible",
            "base_url": "http://127.0.0.1:11434/v1",
        },
        headers=HX,
    )
    assert res.status_code == 200 and MESSAGES["error.provider_host_not_allowed"] in res.text
    assert "provider_host_not_allowed" not in res.text
    res = ui_client.post(
        f"{ADMIN_AI}/providers/create",
        data={
            "csrf": csrf,
            "slug": "openai-main",
            "kind": "openai_compatible",
            "base_url": PUBLIC_URL,
        },
        headers=HX,
    )
    assert res.status_code == 200 and MESSAGES["error.catalog_conflict"] in res.text
    res = ui_client.post(
        f"{ADMIN_AI}/providers/delete", data={"csrf": csrf, "id": provider_id}, headers=HX
    )
    assert res.status_code == 200 and len(catalog_repo.providers) == 0
    assert ui_client.post(f"{ADMIN_AI}/providers/bogus", data={"csrf": csrf}).status_code == 404


def test_admin_models_and_routing_round_trip(
    admin_ui: FastAPI, ui_client: TestClient, catalog_repo: FakeCatalogRepository
) -> None:
    login(ui_client)
    csrf = csrf_of(ui_client.get(ADMIN_AI).text)
    provider = _create_provider(ui_client, csrf, kind="fake", base_url="https://fake.example/v1")
    model = _create_model(ui_client, csrf, provider, prompt_per_1k="0", completion_per_1k="0")
    assert len(catalog_repo.models) == 1  # ``0`` is a valid price
    res = ui_client.post(
        f"{ADMIN_AI}/routing/put",
        data={"csrf": csrf, "purpose": "content_generation", "models": [model]},
        headers=HX,
    )
    assert res.status_code == 200 and MESSAGES["admin.saved"] in res.text, res.text
    chain = res.text.split('data-purpose="content_generation"', 1)[1].split("</article>", 1)[0]
    assert (
        "gpt-4o-mini" in chain
        and f"{MESSAGES['admin.live_now']}: openai-main / gpt-4o-mini" in chain
    )
    assert (
        ui_client.post(
            f"{ADMIN_AI}/routing/put", data={"csrf": csrf, "purpose": "nope"}
        ).status_code
        == 404
    )
    res = ui_client.post(
        f"{ADMIN_AI}/models/create",
        data={
            "csrf": csrf,
            "provider_id": provider,
            "model_name": "bad",
            "capability": "text",
            "cost_per_minute_usd": "1",
        },
        headers=HX,
    )
    assert (
        res.status_code == 200
        and "data-flash" in res.text
        and "catalog_invalid_pricing" not in res.text
    )
    res = ui_client.post(f"{ADMIN_AI}/models/delete", data={"csrf": csrf, "id": model}, headers=HX)
    assert res.status_code == 200 and len(catalog_repo.models) == 0


# ------------------------------------------------------------- D-1 probe ---
def test_admin_probe_success_failure_translated_and_autoescaped(
    admin_ui: FastAPI, ui_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.modules.ai_gateway.catalog import admin_service
    from src.modules.ai_gateway.catalog.service import _ResolvedFacade
    from src.modules.ai_gateway.gateway import CompletionResult, Usage

    holder: dict[str, Any] = {"error": None, "text": "pong"}

    class _Scripted:
        def __init__(self, model: str) -> None:
            self.model = model

        async def complete(self, req: Any) -> CompletionResult:
            if holder["error"] is not None:
                raise holder["error"]
            return CompletionResult(
                text=holder["text"],
                model=self.model,
                usage=Usage(prompt_tokens=3, completion_tokens=1),
                provider="scripted",
                latency_ms=7,
            )

    def _build(resolved: Any, **_: Any) -> Any:
        return _ResolvedFacade(_Scripted(resolved.model.model_name), resolved=resolved)

    monkeypatch.setattr(admin_service, "build_provider_client", _build)
    login(ui_client)
    csrf = csrf_of(ui_client.get(ADMIN_AI).text)
    provider = _create_provider(ui_client, csrf, credential=RAW_KEY)
    model = _create_model(ui_client, csrf, provider)
    run = f"{ADMIN_AI}/probe/run"
    res = ui_client.post(
        run,
        data={"csrf": csrf, "model_id": model, "prompt": "ping", "max_tokens": "8"},
        headers=HX,
    )
    assert res.status_code == 200, res.text
    assert (
        'data-probe-result data-ok="true"' in res.text and MESSAGES["admin.probe_ok"] in res.text
    )
    assert "<pre data-preview>pong</pre>" in res.text and "data-error-category" not in res.text
    # provider failure ⇒ 200, ok=false, category translated (never raw, never 500)
    holder["error"] = AIError(ErrorCategory.PROVIDER_UNAVAILABLE, provider_code="upstream 503")
    res = ui_client.post(run, data={"csrf": csrf, "model_id": model, "prompt": "ping"}, headers=HX)
    assert res.status_code == 200
    assert 'data-ok="false"' in res.text and MESSAGES["ai_error.provider_unavailable"] in res.text
    assert ">provider_unavailable<" not in res.text and "upstream 503" not in res.text
    # autoescape: a hostile preview never becomes markup
    holder["error"] = None
    holder["text"] = "<script>alert(1)</script>"
    res = ui_client.post(run, data={"csrf": csrf, "model_id": model, "prompt": "ping"}, headers=HX)
    assert res.status_code == 200 and "<script>alert(1)</script>" not in res.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in res.text
    # unknown model ⇒ translated 404 flash; prompt too long ⇒ translated 422; no CSRF ⇒ 403
    res = ui_client.post(
        run, data={"csrf": csrf, "model_id": str(uuid4()), "prompt": "ping"}, headers=HX
    )
    assert res.status_code == 200 and MESSAGES["error.catalog_not_found"] in res.text
    res = ui_client.post(
        run, data={"csrf": csrf, "model_id": model, "prompt": "x" * 501}, headers=HX
    )
    assert res.status_code == 200 and MESSAGES["error.http_422"] in res.text
    assert ui_client.post(run, data={"model_id": model, "prompt": "ping"}).status_code == 403


def test_i18n_covers_error_categories_and_catalog_codes() -> None:
    from src.modules.ai_gateway.catalog import service as catalog_service

    have = {k.split(".", 1)[1] for k in MESSAGES if k.startswith("ai_error.")}
    assert {c.value for c in ErrorCategory} <= have and len(ErrorCategory) == 12
    codes = {
        obj.error
        for obj in vars(catalog_service).values()
        if isinstance(obj, type) and issubclass(obj, catalog_service.CatalogError)
    }
    missing = {c for c in codes if f"error.{c}" not in MESSAGES}
    assert codes and not missing, missing


# ---------------------------------------------------------------- D-2 ---
async def test_admin_entitlements_lookup_activate_and_wa_caps(
    admin_ui: FastAPI,
    ui_client: TestClient,
    wa_repo: FakeWhatsAppRepository,
    wa_account,  # noqa: ANN001
) -> None:
    login(ui_client)
    res = ui_client.get(ADMIN_ENT)
    assert (
        res.status_code == 200
        and 'data-form="lookup"' in res.text
        and "data-card=" not in res.text
    )
    res = ui_client.get(f"{ADMIN_ENT}?brand_id={BRAND_ID}")
    assert res.status_code == 200, res.text
    assert (
        'data-card="entitlement" data-plan="trial"' in res.text
        and 'data-form="wa-caps"' in res.text
    )
    csrf = csrf_of(res.text)
    res = ui_client.post(
        f"{ADMIN_ENT}/activate",
        data={
            "csrf": csrf,
            "brand_id": str(BRAND_ID),
            "plan": "growth",
            "months": "3",
            "note": "InstaPay #123",
        },
    )
    assert res.status_code == 200, res.text
    assert MESSAGES["admin.saved"] in res.text and 'data-plan="growth"' in res.text
    res = ui_client.post(
        f"{ADMIN_ENT}/wa-caps",
        data={
            "csrf": csrf,
            "brand_id": str(BRAND_ID),
            "account_id": str(wa_account.id),
            "daily_reply_cap": "50",
            "kill_switch": "true",
        },
    )
    assert res.status_code == 200 and MESSAGES["admin.saved"] in res.text
    cap = await wa_repo.get_number_cap(wa_account.id)
    assert cap is not None and cap.daily_reply_cap == 50 and cap.kill_switch is True
    # errors ⇒ translated flashes, never 500
    res = ui_client.post(
        f"{ADMIN_ENT}/activate",
        data={"csrf": csrf, "brand_id": str(BRAND_ID), "plan": "gold", "months": "1"},
    )
    assert res.status_code == 200 and MESSAGES["error.http_422"] in res.text
    res = ui_client.post(
        f"{ADMIN_ENT}/wa-caps",
        data={"csrf": csrf, "brand_id": str(BRAND_ID), "account_id": str(wa_account.id)},
    )
    assert res.status_code == 200 and MESSAGES["error.wa_caps_nothing_to_change"] in res.text
    assert "wa_caps_nothing_to_change" not in res.text
    res = ui_client.get(f"{ADMIN_ENT}?brand_id={uuid4()}")
    assert res.status_code == 200 and MESSAGES["error.brand_not_found"] in res.text
    assert ui_client.post(f"{ADMIN_ENT}/bogus", data={"csrf": csrf}).status_code == 404


# ---------------------------------------------------------------- D-3 ---
def test_umami_tag_absent_by_default_and_present_once_when_configured(
    ui_app: FastAPI, ui_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    login(ui_client)
    for path in ("/app/", TODAY, f"/app/b/{BRAND_ID}/settings"):
        html = ui_client.get(path).text
        assert "umami" not in html and SCRIPT_URL not in html, path
    # The CDN grep in ``test_vendor_assets`` is about *templates*: the URL below comes from
    # Settings (``umami_script_url``), never from a template literal.
    configured = _reconfigure(monkeypatch, UMAMI_WEBSITE_ID="site-abc-123")
    assert configured.umami_website_id == "site-abc-123"
    html = ui_client.get(TODAY).text
    tag = f'<script defer src="{SCRIPT_URL}" data-website-id="site-abc-123"></script>'
    assert html.count(tag) == 1 and html.count("data-website-id") == 1


# ---------------------------------------------------------------- D-4 ---
def test_playbooks_route_and_today_card(ui_client: TestClient) -> None:
    assert ui_client.get("/api/v1/playbooks").status_code == 401
    login(ui_client)
    token = ui_client.cookies.get("app_access")
    res = ui_client.get("/api/v1/playbooks", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    books = res.json()
    assert {b["sector"] for b in books} == set(SECTORS) == {"restaurants", "clinics"}
    assert all(len(b["today_tips"]) == 3 for b in books)
    # no ``brand_profiles.sector`` yet ⇒ a select; ``?sector=`` ⇒ three tips, translated label
    html = ui_client.get(TODAY).text
    assert 'data-form="sector"' in html and "data-tips" not in html
    html = ui_client.get(f"{TODAY}?sector=clinics").text
    assert 'data-sector="clinics"' in html and "data-tips" in html
    assert MESSAGES["sector.clinics"] in html and "sector.clinics" not in html
    clinics = next(b for b in books if b["sector"] == "clinics")
    assert all(tip in html for tip in clinics["today_tips"])


def test_playbook_loader_fails_on_malformed_file(tmp_path) -> None:  # noqa: ANN001
    from src.modules.brand_brain import playbooks as pb

    for sector in SECTORS:
        (tmp_path / f"{sector}.json").write_text(
            (pb.PLAYBOOKS_DIR / f"{sector}.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
    assert set(pb.load_playbooks(tmp_path)) == set(SECTORS)
    (tmp_path / "clinics.json").write_text('{"sector": "clinics", "tips": []}', encoding="utf-8")
    with pytest.raises(pb.PlaybookLoadError, match="clinics.json"):
        pb.load_playbooks(tmp_path)
    (tmp_path / "clinics.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(pb.PlaybookLoadError, match="malformed"):
        pb.load_playbooks(tmp_path)
    (tmp_path / "clinics.json").unlink()
    with pytest.raises(pb.PlaybookLoadError, match="missing"):
        pb.load_playbooks(tmp_path)
