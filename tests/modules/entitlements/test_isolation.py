"""SPRINT-11 C-8 (D-076) — Safe Removability: nothing outside ``entitlements/`` imports it,
except the explicit whitelist (``src/main.py`` mounts the routers;
``src/modules/ai_gateway/dependencies.py`` injects the limits reader into ``BudgetGuard``)."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
MODULE = "src.modules.entitlements"
WHITELIST = frozenset({"src/main.py", "src/modules/ai_gateway/dependencies.py"})


def _imports_entitlements(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(MODULE):
            return True
        if isinstance(node, ast.Import) and any(a.name.startswith(MODULE) for a in node.names):
            return True
    return False


def test_only_whitelisted_files_import_entitlements() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("src/modules/entitlements/") or rel in WHITELIST:
            continue
        if _imports_entitlements(path):
            offenders.append(rel)
    assert offenders == [], offenders


def test_whitelist_files_really_import_it() -> None:
    """Guards the whitelist itself: if a touch point moves, this test tells the reviewer."""
    for rel in WHITELIST:
        assert _imports_entitlements(ROOT / rel), rel


def test_entitlements_does_not_import_other_modules_orm() -> None:
    """D-016: cross-module reads go through SQL text / consumer protocols, never ORM."""
    for path in (SRC / "modules" / "entitlements").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for forbidden in (
            "tenancy.infrastructure.orm",
            "content.infrastructure.orm",
            "whatsapp.infrastructure.orm",
            "brand_brain.infrastructure",
        ):
            assert forbidden not in text, (path.name, forbidden)


def test_removability_doc_is_complete() -> None:
    doc = (SRC / "modules" / "entitlements" / "REMOVABILITY.md").read_text(encoding="utf-8")
    for needle in (
        "brand_entitlements",
        "entitlement_events",
        "/api/v1/admin/entitlements/activate",
        "/api/v1/admin/entitlements/{brand_id}",
        "/api/v1/brands/{brand_id}/entitlement",
        "/api/v1/brands/{brand_id}/learning-consent",
        "FEATURE_ENTITLEMENTS_ENFORCED",
        "PLATFORM_ADMIN_USER_IDS",
        "get_entitlement_limits_reader",
        "alembic downgrade 0013",
        "'full'",
    ):
        assert needle in doc, needle
