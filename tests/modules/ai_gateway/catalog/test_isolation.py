"""SPRINT-11.1 C-3 (D-076) — Safe Removability of ``ai_gateway/catalog``.

* AST boundary: nothing outside ``src/modules/ai_gateway/`` imports the ``catalog`` package
  except ``src/main.py`` (router mount); inside the package only ``dependencies.py`` does, and
  the business modules never do (Criterion #17).
* Kill-switch isolation: with ``FEATURE_PROVIDER_CATALOG_ENABLED=false`` and every ``catalog``
  module evicted from ``sys.modules``, the app still boots and every non-admin route answers
  (the hot path performs no catalog import or I/O) — Criterion #19.
* The ``REMOVABILITY.md`` contract names every touch point that actually exists.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.config import Settings, get_settings

from tests.conftest import BRAND_ID, OWNER_ID

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
PACKAGE = "src.modules.ai_gateway.catalog"
OUTSIDE_WHITELIST = frozenset({"src/main.py"})
INSIDE_WHITELIST = frozenset({"src/modules/ai_gateway/dependencies.py"})
REMOVABILITY = SRC / "modules" / "ai_gateway" / "catalog" / "REMOVABILITY.md"


def _imports_catalog(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(PACKAGE):
            return True
        if isinstance(node, ast.Import) and any(a.name.startswith(PACKAGE) for a in node.names):
            return True
    return False


def test_only_whitelisted_files_import_the_catalog() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("src/modules/ai_gateway/catalog/"):
            continue
        if rel in OUTSIDE_WHITELIST or rel in INSIDE_WHITELIST:
            continue
        if _imports_catalog(path):
            offenders.append(rel)
    assert offenders == [], offenders


def test_whitelist_files_really_import_it() -> None:
    """Guards the whitelist itself: if a touch point moves, this test tells the reviewer."""
    for rel in OUTSIDE_WHITELIST | INSIDE_WHITELIST:
        assert _imports_catalog(ROOT / rel), rel


def test_business_modules_never_import_the_catalog() -> None:
    for module in ("content", "whatsapp", "brand_brain", "tenancy", "entitlements", "auth"):
        for path in (SRC / "modules" / module).rglob("*.py"):
            assert not _imports_catalog(path), path.relative_to(ROOT).as_posix()


def test_removability_contract_lists_every_touch_point() -> None:
    text = REMOVABILITY.read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 70
    for needle in (
        "src/main.py",
        "src/modules/ai_gateway/dependencies.py",
        "FEATURE_PROVIDER_CATALOG_ENABLED",
        "AI_ALLOW_LOCAL_PROVIDER_HOSTS",
        "AI_CATALOG_CACHE_SECONDS",
        "ai_providers",
        "ai_models",
        "ai_routing",
        "admin_probe",
        "0015",
        "test_isolation.py",
    ):
        assert needle in text, needle


# ------------------------------------------------------- kill-switch isolation --
def test_app_serves_non_admin_routes_with_catalog_evicted_and_switch_off(
    app: FastAPI, settings: Settings, auth_headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Evict every ``catalog`` module, keep the switch off ⇒ a brand read and a content list
    work and the guarded gateway path never re-imports the catalog (no catalog I/O)."""
    assert settings.feature_provider_catalog_enabled is False
    off = settings.model_copy(update={"feature_provider_catalog_enabled": False})
    app.dependency_overrides[get_settings] = lambda: off

    evicted = [name for name in list(sys.modules) if name.startswith(PACKAGE)]
    assert evicted, "the catalog package should have been imported by main.py at app creation"
    saved = {name: sys.modules.pop(name) for name in evicted}
    try:
        with TestClient(app) as client:
            owner = auth_headers(OWNER_ID)
            brand = client.get(f"/api/v1/brands/{BRAND_ID}", headers=owner)
            assert brand.status_code == 200, brand.text
            content = f"/api/v1/brands/{BRAND_ID}/content"
            items = client.get(f"{content}/items", headers=owner)
            assert items.status_code == 200, items.text
            created = client.post(
                f"{content}/items",
                json={"channel": "facebook_page", "body": "switch-off isolation probe"},
                headers=owner,
            )
            assert created.status_code == 201, created.text
            # The guarded gateway (fake provider, legacy price table) runs with the switch off.
            generated = client.post(
                f"{content}/items/{created.json()['id']}/generate",
                json={"brief": "a short post about our new coffee blend"},
                headers=owner,
            )
            assert generated.status_code == 200, generated.text
        # The switch-off path imported nothing from the catalog package.
        reimported = [name for name in sys.modules if name.startswith(PACKAGE)]
        assert reimported == [], reimported
    finally:
        sys.modules.update(saved)
