"""SPRINT-11.1 C-1/C-2 — the eleven ``/admin/ai/*`` routes over the fakes (no PG).

* every route is ``PlatformAdmin``: 401 without a bearer, 403 ``platform_admin_required`` for
  a regular user and for everyone when ``PLATFORM_ADMIN_USER_IDS`` is empty (Criterion #16);
* Zero-Secret (Criterion #10): the raw key never appears in any response, in ``openapi.json``
  or in ``caplog``; the stored cipher is Fernet (``gAAAA``);
* host guard (Criterion #13): ``127.0.0.1`` ⇒ 422 ``provider_host_not_allowed`` unless listed;
* pricing shapes (Criterion #11): ``max_audio_seconds=121`` ⇒ 422;
* routing replace is atomic/ordered; conflicts ⇒ 409; unknown ids ⇒ 404;
* probe (Criterion #14): ``max_tokens=65`` ⇒ 422; sixth within a minute ⇒ 429 + Retry-After;
  success ⇒ one ``ai_cost_log`` row ``purpose='admin_probe'`` ``brand_id NULL`` ``ok``;
  provider failure ⇒ **200** ``ok=false`` + an ``ErrorCategory`` name + an ``error`` row.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.config import Settings, get_settings
from src.modules.ai_gateway.catalog.router import PROBE_BUCKET, get_catalog_admin_service
from src.modules.ai_gateway.catalog.service import CatalogPriceTable
from src.modules.ai_gateway.contract import AIError, ErrorCategory
from src.modules.ai_gateway.cost_log import CostOutcome
from src.modules.ai_gateway.gateway import CompletionRequest, CompletionResult, Usage

from tests.conftest import AGENCY_ID, OWNER_ID, OWNER_USER_ID
from tests.fakes import FakeCatalogRepository, FakeRateLimiter

API = "/api/v1/admin/ai"
Headers = Callable[[UUID], dict[str, str]]
# Assembled at import time so no secret-shaped literal sits in the repository (gitleaks).
RAW_KEY = "".join(("sk-", "PLANNER-PROBE-", "abcdef0123456789"))
PUBLIC = "https://api.example-llm.com/v1"


# ------------------------------------------------------------------ fixtures --
@pytest.fixture
def catalog_repo() -> FakeCatalogRepository:
    return FakeCatalogRepository()


@pytest.fixture
def admin_app(
    app: FastAPI,
    settings: Settings,
    catalog_repo: FakeCatalogRepository,
    encryption: Any,
    limiter: FakeRateLimiter,
) -> FastAPI:
    """OWNER is a platform admin; the admin service runs over the in-memory catalog repo."""
    from src.modules.ai_gateway.catalog.admin_service import CatalogAdminService
    from src.modules.ai_gateway.catalog.infrastructure.pg_repository import CATALOG_CACHE

    admin_settings = settings.model_copy(update={"platform_admin_user_ids": [OWNER_USER_ID]})
    app.dependency_overrides[get_settings] = lambda: admin_settings
    app.state.catalog_invalidations = 0

    def _invalidate() -> None:
        app.state.catalog_invalidations += 1
        CATALOG_CACHE.invalidate()

    app.dependency_overrides[get_catalog_admin_service] = lambda: CatalogAdminService(
        catalog_repo,
        encryption=encryption,
        allow_local_hosts=admin_settings.ai_allow_local_provider_hosts,
        invalidate=_invalidate,
    )
    app.state.admin_settings = admin_settings
    return app


@pytest.fixture
def admin(admin_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(admin_app) as c:
        yield c


def _provider_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "slug": "openai-main",
        "kind": "openai_compatible",
        "base_url": PUBLIC,
        "api_key": RAW_KEY,
    }
    body.update(overrides)
    return body


def _create_provider(admin: TestClient, owner: dict[str, str], **overrides: Any) -> dict[str, Any]:
    res = admin.post(f"{API}/providers", json=_provider_body(**overrides), headers=owner)
    assert res.status_code == 201, res.text
    return res.json()


def _create_model(
    admin: TestClient,
    owner: dict[str, str],
    provider_id: str,
    name: str = "gpt-4o-mini",
    **kw: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "provider_id": provider_id,
        "model_name": name,
        "pricing": {"prompt_per_1k": "0.00015", "completion_per_1k": "0.0006"},
    }
    body.update(kw)
    res = admin.post(f"{API}/models", json=body, headers=owner)
    assert res.status_code == 201, res.text
    return res.json()


# ------------------------------------------------------------------- guard --
ALL_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/providers", None),
    ("POST", "/providers", _provider_body()),
    ("PATCH", f"/providers/{uuid4()}", {"enabled": False}),
    ("DELETE", f"/providers/{uuid4()}", None),
    ("GET", "/models", None),
    ("POST", "/models", {"provider_id": str(uuid4()), "model_name": "m"}),
    ("PATCH", f"/models/{uuid4()}", {"enabled": False}),
    ("DELETE", f"/models/{uuid4()}", None),
    ("GET", "/routing", None),
    ("PUT", "/routing/content_generation", {"models": []}),
    ("POST", "/probe", {"model_id": str(uuid4()), "prompt": "hi"}),
]


@pytest.mark.parametrize(
    ("method", "path", "body"), ALL_ROUTES, ids=[f"{m} {p}" for m, p, _ in ALL_ROUTES]
)
def test_c16_every_admin_ai_route_is_platform_admin_only(
    client: TestClient, auth_headers: Headers, method: str, path: str, body: dict | None
) -> None:
    """Empty admin list ⇒ 403 for the owner; no bearer ⇒ 401 (Criterion #16)."""
    res = client.request(method, f"{API}{path}", json=body, headers=auth_headers(OWNER_ID))
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["error"] == "platform_admin_required"
    anon = client.request(method, f"{API}{path}", json=body)
    assert anon.status_code == 401, anon.text


def test_c16_regular_user_is_refused_when_admins_exist(admin: TestClient, auth_headers: Headers):
    res = admin.get(f"{API}/providers", headers=auth_headers(AGENCY_ID))
    assert res.status_code == 403 and res.json()["detail"]["error"] == "platform_admin_required"


# --------------------------------------------------------------- providers --
def test_c1_provider_crud_zero_secret_and_cascade(
    admin: TestClient,
    auth_headers: Headers,
    catalog_repo: FakeCatalogRepository,
    admin_app: FastAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = auth_headers(OWNER_ID)
    caplog.set_level(logging.INFO)
    created = _create_provider(admin, owner)
    assert created["has_credential"] is True and created["credential_ref"] is None
    assert "api_key" not in created and "credential_cipher" not in created
    stored = catalog_repo.providers[UUID(created["id"])]
    assert stored.credential_cipher is not None and stored.credential_cipher.startswith("gAAAA")
    assert RAW_KEY not in stored.credential_cipher

    listed = admin.get(f"{API}/providers", headers=owner)
    assert listed.status_code == 200 and listed.json()[0]["has_credential"] is True

    # PATCH: new key replaces; explicit null clears; credential_ref switches the mode.
    patched = admin.patch(
        f"{API}/providers/{created['id']}",
        json={"enabled": False, "timeout_seconds": "12.5", "api_key": RAW_KEY + "x"},
        headers=owner,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["enabled"] is False and Decimal(
        patched.json()["timeout_seconds"]
    ) == Decimal("12.5")
    cleared = admin.patch(
        f"{API}/providers/{created['id']}", json={"api_key": None}, headers=owner
    )
    assert cleared.status_code == 200 and cleared.json()["has_credential"] is False
    ref = admin.patch(
        f"{API}/providers/{created['id']}",
        json={"credential_ref": "OPENAI_KEY_ENV"},
        headers=owner,
    )
    assert ref.status_code == 200
    assert (
        ref.json()["has_credential"] is True and ref.json()["credential_ref"] == "OPENAI_KEY_ENV"
    )
    back = admin.patch(
        f"{API}/providers/{created['id']}", json={"api_key": RAW_KEY}, headers=owner
    )
    assert back.json()["credential_ref"] is None and back.json()["has_credential"] is True

    # Zero-Secret over every body seen so far and over the audit log.
    for text in (created, listed.json(), patched.json(), cleared.json(), ref.json(), back.json()):
        assert RAW_KEY not in str(text) and "sk-" not in str(text)
    assert RAW_KEY not in caplog.text and "sk-" not in caplog.text
    assert any("admin_ai action=provider.create" in r.getMessage() for r in caplog.records)
    assert admin_app.state.catalog_invalidations >= 5

    # Duplicate slug ⇒ 409; unknown id ⇒ 404; CASCADE on delete.
    dup = admin.post(f"{API}/providers", json=_provider_body(), headers=owner)
    assert dup.status_code == 409 and dup.json()["detail"]["error"] == "catalog_conflict"
    missing = admin.patch(f"{API}/providers/{uuid4()}", json={"enabled": True}, headers=owner)
    assert missing.status_code == 404 and missing.json()["detail"]["error"] == "catalog_not_found"
    model = _create_model(admin, owner, created["id"])
    put = admin.put(
        f"{API}/routing/content_generation", json={"models": [model["id"]]}, headers=owner
    )
    assert put.status_code == 200 and len(put.json()) == 1
    gone = admin.delete(f"{API}/providers/{created['id']}", headers=owner)
    assert gone.status_code == 204
    assert admin.get(f"{API}/models", headers=owner).json() == []
    assert admin.get(f"{API}/routing", headers=owner).json() == []
    assert admin.delete(f"{API}/providers/{created['id']}", headers=owner).status_code == 404


def test_c13_provider_host_guard_422_unless_allow_listed(
    admin: TestClient, auth_headers: Headers, admin_app: FastAPI
) -> None:
    owner = auth_headers(OWNER_ID)
    local = admin.post(
        f"{API}/providers",
        json=_provider_body(slug="ollama", base_url="http://127.0.0.1:11434/v1"),
        headers=owner,
    )
    assert local.status_code == 422, local.text
    assert local.json()["detail"]["error"] == "provider_host_not_allowed"
    # PATCH to a loopback host is refused the same way.
    created = _create_provider(admin, owner)
    patched = admin.patch(
        f"{API}/providers/{created['id']}",
        json={"base_url": "http://localhost:11434"},
        headers=owner,
    )
    assert patched.status_code == 422
    # Allow-listed host:port ⇒ 201.
    allowed = admin_app.state.admin_settings.model_copy(
        update={"ai_allow_local_provider_hosts": ["127.0.0.1:11434"]}
    )
    admin_app.dependency_overrides[get_settings] = lambda: allowed
    from src.modules.ai_gateway.catalog.admin_service import CatalogAdminService

    repo = FakeCatalogRepository()
    admin_app.dependency_overrides[get_catalog_admin_service] = lambda: CatalogAdminService(
        repo,
        encryption=None,
        allow_local_hosts=allowed.ai_allow_local_provider_hosts,
        invalidate=lambda: None,
    )
    ok = admin.post(
        f"{API}/providers",
        json=_provider_body(slug="ollama", base_url="http://127.0.0.1:11434/v1", api_key=None),
        headers=owner,
    )
    assert ok.status_code == 201, ok.text


def test_c1_provider_payload_validation(admin: TestClient, auth_headers: Headers) -> None:
    owner = auth_headers(OWNER_ID)
    both = admin.post(
        f"{API}/providers", json=_provider_body(credential_ref="ENV_KEY"), headers=owner
    )
    assert both.status_code == 422  # api_key XOR credential_ref
    bad_slug = admin.post(f"{API}/providers", json=_provider_body(slug="Bad Slug!"), headers=owner)
    assert bad_slug.status_code == 422
    extra = admin.post(f"{API}/providers", json=_provider_body(secret="x"), headers=owner)
    assert extra.status_code == 422


# ------------------------------------------------------------------ models --
def test_c11_model_crud_and_pricing_shapes(admin: TestClient, auth_headers: Headers) -> None:
    owner = auth_headers(OWNER_ID)
    provider = _create_provider(admin, owner)
    model = _create_model(admin, owner, provider["id"])
    assert model["pricing"]["prompt_per_1k"] == "0.00015" and model["capability"] == "text"

    by_provider = admin.get(f"{API}/models", params={"provider_id": provider["id"]}, headers=owner)
    assert [m["id"] for m in by_provider.json()] == [model["id"]]
    assert (
        admin.get(f"{API}/models", params={"provider_id": str(uuid4())}, headers=owner).json()
        == []
    )

    # audio over 120 s ⇒ 422 at creation (Criterion #11); text with audio price ⇒ 422.
    too_long = admin.post(
        f"{API}/models",
        json={
            "provider_id": provider["id"],
            "model_name": "whisper-1",
            "capability": "audio",
            "pricing": {"cost_per_minute_usd": "0.006", "max_audio_seconds": 121},
        },
        headers=owner,
    )
    assert too_long.status_code == 422
    wrong_shape = admin.post(
        f"{API}/models",
        json={
            "provider_id": provider["id"],
            "model_name": "weird",
            "capability": "text",
            "pricing": {"cost_per_minute_usd": "0.006", "max_audio_seconds": 60},
        },
        headers=owner,
    )
    assert wrong_shape.status_code == 422
    audio = admin.post(
        f"{API}/models",
        json={
            "provider_id": provider["id"],
            "model_name": "whisper-1",
            "capability": "audio",
            "pricing": {"cost_per_minute_usd": "0.006", "max_audio_seconds": 120},
        },
        headers=owner,
    )
    assert audio.status_code == 201, audio.text

    # Zero price is valid; unpriced is allowed (strict mode refuses it at call time).
    zero = _create_model(
        admin,
        owner,
        provider["id"],
        "free-1",
        pricing={"prompt_per_1k": "0", "completion_per_1k": "0"},
    )
    assert zero["pricing"]["prompt_per_1k"] == "0"
    unpriced = _create_model(admin, owner, provider["id"], "mystery-1", pricing={})
    assert unpriced["pricing"]["prompt_per_1k"] is None

    dup = admin.post(
        f"{API}/models",
        json={"provider_id": provider["id"], "model_name": "gpt-4o-mini"},
        headers=owner,
    )
    assert dup.status_code == 409
    orphan = admin.post(
        f"{API}/models", json={"provider_id": str(uuid4()), "model_name": "x"}, headers=owner
    )
    assert orphan.status_code == 404

    patched = admin.patch(
        f"{API}/models/{model['id']}",
        json={
            "enabled": False,
            "max_tokens_cap": 2048,
            "pricing": {"prompt_per_1k": "0.001", "completion_per_1k": "0.002"},
        },
        headers=owner,
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False and patched.json()["max_tokens_cap"] == 2048
    assert patched.json()["pricing"]["completion_per_1k"] == "0.002"
    bad_patch = admin.patch(
        f"{API}/models/{model['id']}",
        json={"pricing": {"cost_per_minute_usd": "0.1", "max_audio_seconds": 30}},
        headers=owner,
    )
    assert bad_patch.status_code == 422
    assert bad_patch.json()["detail"]["error"] == "catalog_invalid_pricing"
    assert (
        admin.patch(f"{API}/models/{uuid4()}", json={"enabled": True}, headers=owner).status_code
        == 404
    )
    assert admin.delete(f"{API}/models/{model['id']}", headers=owner).status_code == 204
    assert admin.delete(f"{API}/models/{model['id']}", headers=owner).status_code == 404


# ----------------------------------------------------------------- routing --
def test_c1_routing_put_replaces_ordered_chain(admin: TestClient, auth_headers: Headers) -> None:
    owner = auth_headers(OWNER_ID)
    provider = _create_provider(admin, owner)
    a = _create_model(admin, owner, provider["id"], "a-1")
    b = _create_model(admin, owner, provider["id"], "b-1")

    put = admin.put(
        f"{API}/routing/content_generation", json={"models": [b["id"], a["id"]]}, headers=owner
    )
    assert put.status_code == 200, put.text
    assert [(r["model_id"], r["priority"]) for r in put.json()] == [(b["id"], 0), (a["id"], 1)]

    listed = admin.get(f"{API}/routing", params={"purpose": "content_generation"}, headers=owner)
    assert [r["model_id"] for r in listed.json()] == [b["id"], a["id"]]
    other = admin.put(f"{API}/routing/content_plan", json={"models": [a["id"]]}, headers=owner)
    assert other.status_code == 200
    everything = admin.get(f"{API}/routing", headers=owner).json()
    assert len(everything) == 3

    # Replace with a shorter chain ⇒ the old rows are gone; empty ⇒ no route.
    shorter = admin.put(
        f"{API}/routing/content_generation", json={"models": [a["id"]]}, headers=owner
    )
    assert [r["model_id"] for r in shorter.json()] == [a["id"]]
    empty = admin.put(f"{API}/routing/content_generation", json={"models": []}, headers=owner)
    assert empty.status_code == 200 and empty.json() == []
    assert (
        admin.get(f"{API}/routing", params={"purpose": "content_generation"}, headers=owner).json()
        == []
    )

    unknown_model = admin.put(
        f"{API}/routing/content_plan", json={"models": [str(uuid4())]}, headers=owner
    )
    assert (
        unknown_model.status_code == 404
        and unknown_model.json()["detail"]["error"] == "catalog_not_found"
    )
    duplicate = admin.put(
        f"{API}/routing/content_plan", json={"models": [a["id"], a["id"]]}, headers=owner
    )
    assert duplicate.status_code == 422
    bad_purpose = admin.put(f"{API}/routing/not_a_purpose", json={"models": []}, headers=owner)
    assert (
        bad_purpose.status_code == 422
        and bad_purpose.json()["detail"]["error"] == "catalog_unknown_purpose"
    )
    assert (
        admin.get(f"{API}/routing", params={"purpose": "nope"}, headers=owner).status_code == 422
    )


# ------------------------------------------------------------------- probe --
class _ScriptedGateway:
    """Stands in for ``build_provider_client``: a fixed reply or a scripted ``AIError``."""

    name = "scripted"

    def __init__(self, model: str, error: AIError | None = None) -> None:
        self.model = model
        self.error = error
        self.requests: list[CompletionRequest] = []

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        self.requests.append(req)
        if self.error is not None:
            raise self.error
        return CompletionResult(
            text="pong " * 60,
            model=self.model,
            provider=self.name,
            usage=Usage(prompt_tokens=11, completion_tokens=5),
            latency_ms=7,
        )


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the facade factory used by the probe so no network client is ever built."""
    from src.modules.ai_gateway.catalog import admin_service

    holder: dict[str, Any] = {"error": None, "built": []}

    def _build(resolved: Any, **_: Any) -> _ScriptedGateway:
        gw = _ScriptedGateway(resolved.model.model_name, holder["error"])
        holder["built"].append(gw)
        return gw

    monkeypatch.setattr(admin_service, "build_provider_client", _build)
    return holder


def _cost_rows(app: FastAPI) -> list[Any]:
    return list(app.state.cost_writer.entries)


def test_c14_probe_success_writes_admin_probe_cost_row(
    admin: TestClient, auth_headers: Headers, admin_app: FastAPI, scripted: dict[str, Any]
) -> None:
    owner = auth_headers(OWNER_ID)
    provider = _create_provider(admin, owner)
    model = _create_model(admin, owner, provider["id"])
    res = admin.post(
        f"{API}/probe",
        json={"model_id": model["id"], "prompt": "ping", "max_tokens": 8},
        headers=owner,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["error_category"] is None
    assert body["prompt_tokens"] == 11 and body["completion_tokens"] == 5
    assert len(body["preview"]) <= 200 and body["preview"].startswith("pong")
    # 11 * 0.00015 / 1000 + 5 * 0.0006 / 1000 = 0.00000165 + 0.000003 = 0.00000465 ⇒ 0.000005
    assert Decimal(body["estimated_cost_usd"]) == Decimal("0.000005")
    req = scripted["built"][0].requests[0]
    assert req.purpose == "admin_probe" and req.brand_id is None and req.max_tokens == 8
    rows = _cost_rows(admin_app)
    assert len(rows) == 1
    row = rows[0]
    assert row.purpose == "admin_probe" and row.brand_id is None
    assert row.outcome == CostOutcome.OK and row.model == "gpt-4o-mini"
    assert RAW_KEY not in res.text


def test_c14_probe_provider_failure_is_200_ok_false_with_category(
    admin: TestClient, auth_headers: Headers, admin_app: FastAPI, scripted: dict[str, Any]
) -> None:
    owner = auth_headers(OWNER_ID)
    provider = _create_provider(admin, owner)
    model = _create_model(admin, owner, provider["id"])
    scripted["error"] = AIError(ErrorCategory.PROVIDER_UNAVAILABLE, provider_code="upstream 503")
    res = admin.post(
        f"{API}/probe", json={"model_id": model["id"], "prompt": "ping"}, headers=owner
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is False and body["error_category"] == "provider_unavailable"
    assert body["prompt_tokens"] is None and body["estimated_cost_usd"] is None
    assert ErrorCategory(body["error_category"]) in ErrorCategory  # one of the 12
    rows = _cost_rows(admin_app)
    assert len(rows) == 1 and rows[0].outcome == CostOutcome.ERROR
    assert rows[0].error_category == "provider_unavailable" and rows[0].brand_id is None


def test_c14_probe_limits_422_429_404(
    admin: TestClient,
    auth_headers: Headers,
    limiter: FakeRateLimiter,
    scripted: dict[str, Any],
) -> None:
    owner = auth_headers(OWNER_ID)
    provider = _create_provider(admin, owner)
    model = _create_model(admin, owner, provider["id"])
    too_many_tokens = admin.post(
        f"{API}/probe",
        json={"model_id": model["id"], "prompt": "ping", "max_tokens": 65},
        headers=owner,
    )
    assert too_many_tokens.status_code == 422
    long_prompt = admin.post(
        f"{API}/probe", json={"model_id": model["id"], "prompt": "p" * 501}, headers=owner
    )
    assert long_prompt.status_code == 422
    missing = admin.post(
        f"{API}/probe", json={"model_id": str(uuid4()), "prompt": "ping"}, headers=owner
    )
    assert missing.status_code == 404 and missing.json()["detail"]["error"] == "catalog_not_found"

    # 5 per minute per admin: the sixth is 429 with Retry-After (the limiter is keyed by user).
    limiter.hits.clear()
    limiter.reject_after = 5
    for _ in range(5):
        assert (
            admin.post(
                f"{API}/probe", json={"model_id": model["id"], "prompt": "ping"}, headers=owner
            ).status_code
            == 200
        )
    sixth = admin.post(
        f"{API}/probe", json={"model_id": model["id"], "prompt": "ping"}, headers=owner
    )
    assert sixth.status_code == 429 and sixth.json()["detail"]["error"] == "rate_limited"
    assert sixth.headers["Retry-After"] == "60"
    assert all(
        bucket == PROBE_BUCKET and key == str(OWNER_USER_ID) for bucket, key in limiter.hits
    )


def test_c11_probe_unpriced_model_is_503_before_any_call(
    admin: TestClient, auth_headers: Headers, admin_app: FastAPI, scripted: dict[str, Any]
) -> None:
    """Strict pricing (default): a ``NULL``-priced model ⇒ 503 ``ai_model_unpriced`` and the
    provider is never called (Criterion #11)."""
    owner = auth_headers(OWNER_ID)
    provider = _create_provider(admin, owner)
    model = _create_model(admin, owner, provider["id"], "mystery-1", pricing={})
    res = admin.post(
        f"{API}/probe", json={"model_id": model["id"], "prompt": "ping"}, headers=owner
    )
    assert res.status_code == 503, res.text
    assert res.json()["detail"]["error"] == "ai_model_unpriced"
    assert scripted["built"] and scripted["built"][0].requests == []
    assert _cost_rows(admin_app) == []


# -------------------------------------------------------------- zero secret --
def test_c10_openapi_admin_ai_schemas_carry_no_secret_fields(client: TestClient) -> None:
    """Response schemas of ``/admin/ai/*`` never name ``api_key`` / ``credential_cipher``;
    ``api_key`` exists on the two request bodies only (``SecretStr``, write-only)."""
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    for name in ("ProviderOut", "ModelOut", "RouteOut", "ProbeOut"):
        props = set(schemas[name]["properties"])
        assert not props & {"api_key", "credential_cipher"}, name
    assert "api_key" in schemas["ProviderCreate"]["properties"]
    api_key = schemas["ProviderCreate"]["properties"]["api_key"]
    # nullable ``SecretStr`` ⇒ ``anyOf[{string, writeOnly}, {null}]``: write-only, password format.
    secret_arm = next(a for a in api_key["anyOf"] if a.get("type") == "string")
    assert secret_arm.get("writeOnly") is True and secret_arm.get("format") == "password"
    admin_paths = [p for p in spec["paths"] if "/admin/ai/" in p]
    assert len(admin_paths) == 7 and sum(len(spec["paths"][p]) for p in admin_paths) == 11


def test_c3_price_table_from_snapshot_matches_model_prices() -> None:
    """The probe prices through ``CatalogPriceTable(snapshot)``; sanity of the arithmetic."""
    from src.modules.ai_gateway.catalog.domain import (
        CatalogSnapshot,
        Model,
        ModelPricing,
        Provider,
    )

    p = Provider(slug="p", kind="fake", base_url="http://fake")
    m = Model(
        provider_id=p.id,
        model_name="fake-1",
        pricing=ModelPricing.token(Decimal("1"), Decimal("2")),
    )
    table = CatalogPriceTable(CatalogSnapshot(providers=(p,), models=(m,)))
    assert table.estimate("fake-1", 1000, 1000) == Decimal("3.000000")
    assert table.estimate_unit("other") is None
