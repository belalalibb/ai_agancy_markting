"""SPRINT-11.1 B-5 / Criterion #15 — the provider-catalog kill switch at the HTTP layer.

``FEATURE_PROVIDER_CATALOG_ENABLED=false`` (default) with a **full** catalog ⇒ ``/generate``
uses ``AI_PROVIDER_BASE_URL`` / ``AI_PRICE_TABLE_JSON`` literally (respx sees the legacy host,
the catalog host receives nothing). ``true`` ⇒ the catalog route is called instead; an empty
catalog ⇒ 503 ``ai_route_unavailable`` before any network call.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from src.core.config import get_settings
from src.modules.ai_gateway.catalog.domain import (
    CatalogSnapshot,
    Model,
    ModelPricing,
    Provider,
    Route,
)
from src.modules.ai_gateway.dependencies import get_catalog_reader

from tests.conftest import BRAND_ID, OWNER_ID
from tests.modules.content.test_router import Headers, _create, _generate

LEGACY = "https://legacy.example/v1"
CATALOG = "https://catalog.example/v1"
PLATFORM_KEY = "".join(("sk-", "legacyplatformkey"))  # assembled: no secret-shaped literal
MODEL = "gpt-4o-mini"


def _ok_body(model: str) -> dict:
    return {
        "model": model,
        "choices": [{"message": {"content": "catalog or legacy reply text"}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
    }


class _StaticReader:
    def __init__(self, snapshot: CatalogSnapshot) -> None:
        self._snapshot = snapshot
        self.calls = 0

    async def snapshot(self) -> CatalogSnapshot:
        self.calls += 1
        return self._snapshot


def _full_catalog() -> CatalogSnapshot:
    provider = Provider(
        slug="catalog-llm",
        kind="openai_compatible",
        base_url=CATALOG,
        credential_ref="CATALOG_TEST_PROVIDER_KEY",
    )
    model = Model(
        provider_id=provider.id,
        model_name=MODEL,
        pricing=ModelPricing.token(Decimal("0.001"), Decimal("0.002")),
    )
    return CatalogSnapshot(
        providers=(provider,),
        models=(model,),
        routes=(Route(purpose="content_generation", model_id=model.id, priority=0),),
    )


@pytest.fixture
def as_(auth_headers: Headers) -> Headers:
    return auth_headers


@pytest.fixture
def switchable(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    """Legacy HTTP provider configured + a full catalog behind ``get_catalog_reader``; the
    kill switch is toggled per test through ``get_settings``."""
    base = app.dependency_overrides.get(get_settings)
    current = base() if base else get_settings()
    legacy = current.model_copy(
        update={
            "ai_provider": "openai_compatible",
            "ai_provider_base_url": LEGACY,
            "ai_provider_api_key": SecretStr(PLATFORM_KEY),
            "ai_default_model": MODEL,
            "ai_price_table_json": (
                '{"gpt-4o-mini": {"prompt_per_1k": "0.00015", "completion_per_1k": "0.0006"}}'
            ),
            "feature_provider_catalog_enabled": False,
        }
    )
    app.state.legacy_settings = legacy
    app.dependency_overrides[get_settings] = lambda: app.state.legacy_settings
    reader = _StaticReader(_full_catalog())
    app.state.catalog_reader = reader
    app.dependency_overrides[get_catalog_reader] = lambda: reader
    monkeypatch.setenv("CATALOG_TEST_PROVIDER_KEY", "".join(("sk-", "catalogkey")))
    try:
        yield app
    finally:
        app.dependency_overrides.pop(get_catalog_reader, None)
        if base:
            app.dependency_overrides[get_settings] = base
        else:
            app.dependency_overrides.pop(get_settings, None)


def test_switch_off_uses_legacy_env_provider_literally(
    switchable: FastAPI, client: TestClient, as_: Headers
) -> None:
    owner = as_(OWNER_ID)
    item_id = _create(client, owner)["id"]
    with respx.mock(assert_all_called=False) as router:
        legacy = router.post(f"{LEGACY}/chat/completions").mock(
            return_value=httpx.Response(200, json=_ok_body(MODEL))
        )
        catalog = router.post(f"{CATALOG}/chat/completions").mock(
            return_value=httpx.Response(200, json=_ok_body(MODEL))
        )
        res = _generate(client, owner, item_id)
    assert res.status_code == 200, res.text
    assert legacy.call_count == 1 and catalog.call_count == 0
    bearer = legacy.calls[0].request.headers["Authorization"]
    assert bearer == f"Bearer {PLATFORM_KEY}"
    # the catalog was never even read on the switched-off path
    assert switchable.state.catalog_reader.calls == 0
    rows = switchable.state.cost_writer.entries
    assert rows and rows[-1].model == MODEL
    # priced from AI_PRICE_TABLE_JSON: 12 * 0.00015/1k + 7 * 0.0006/1k
    assert rows[-1].estimated_cost_usd == Decimal("0.000006")


def test_switch_on_routes_through_catalog_provider(
    switchable: FastAPI, client: TestClient, as_: Headers
) -> None:
    switchable.state.legacy_settings = switchable.state.legacy_settings.model_copy(
        update={"feature_provider_catalog_enabled": True}
    )
    owner = as_(OWNER_ID)
    item_id = _create(client, owner)["id"]
    with respx.mock(assert_all_called=False) as router:
        legacy = router.post(f"{LEGACY}/chat/completions").mock(
            return_value=httpx.Response(200, json=_ok_body(MODEL))
        )
        catalog = router.post(f"{CATALOG}/chat/completions").mock(
            return_value=httpx.Response(200, json=_ok_body(MODEL))
        )
        res = _generate(client, owner, item_id)
    assert res.status_code == 200, res.text
    assert catalog.call_count == 1 and legacy.call_count == 0
    bearer = catalog.calls[0].request.headers["Authorization"]
    assert bearer == "Bearer " + "".join(("sk-", "catalogkey"))
    assert PLATFORM_KEY not in res.text and "catalogkey" not in res.text
    assert switchable.state.catalog_reader.calls >= 1
    rows = switchable.state.cost_writer.entries
    # priced from ai_models: 12 * 0.001/1k + 7 * 0.002/1k
    assert rows[-1].estimated_cost_usd == Decimal("0.000026")
    assert rows[-1].brand_id == BRAND_ID


def test_switch_on_with_empty_catalog_is_503_before_any_network(
    switchable: FastAPI, client: TestClient, as_: Headers
) -> None:
    switchable.state.legacy_settings = switchable.state.legacy_settings.model_copy(
        update={"feature_provider_catalog_enabled": True}
    )
    switchable.dependency_overrides[get_catalog_reader] = lambda: _StaticReader(CatalogSnapshot())
    owner = as_(OWNER_ID)
    item_id = _create(client, owner)["id"]
    before = len(switchable.state.cost_writer.entries)
    with respx.mock(assert_all_called=False) as router:
        any_llm = router.post(url__regex=r".*/chat/completions").mock(
            return_value=httpx.Response(200, json=_ok_body(MODEL))
        )
        res = _generate(client, owner, item_id)
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["error"] == "ai_route_unavailable"
    assert any_llm.call_count == 0
    assert len(switchable.state.cost_writer.entries) == before  # no reservation written
