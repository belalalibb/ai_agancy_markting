"""SPRINT-12 B-3 (D-076) — Safe Removability of ``pulse``: nothing outside
``src/modules/pulse/`` imports it except ``src/main.py`` (step + router mount), and pulse
itself touches other modules only through SQL text / its consumer-owned port (D-016)."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
MODULE = "src.modules.pulse"
WHITELIST = frozenset({"src/main.py"})


def _imports_pulse(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(MODULE):
            return True
        if isinstance(node, ast.Import) and any(a.name.startswith(MODULE) for a in node.names):
            return True
    return False


def test_only_main_imports_pulse() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("src/modules/pulse/") or rel in WHITELIST:
            continue
        if _imports_pulse(path):
            offenders.append(rel)
    assert offenders == [], offenders


def test_main_really_imports_pulse() -> None:
    assert _imports_pulse(ROOT / "src/main.py")


def test_pulse_does_not_import_other_modules_orm_or_services() -> None:
    """Checks are SQL text over other modules' tables; only tenancy access/auth helpers,
    ``core`` and the pulse port are imported."""
    allowed_prefixes = (
        "src.core.",
        "src.modules.pulse",
        "src.modules.auth.dependencies",
        "src.modules.tenancy.dependencies",
        "src.modules.tenancy.domain.access",
        "src.modules.tenancy.exceptions",
        "src.modules.tenancy.router",
    )
    for path in (SRC / "modules" / "pulse").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src."):
                assert node.module.startswith(allowed_prefixes), (path.name, node.module)
        text = path.read_text(encoding="utf-8")
        for forbidden in (
            "whatsapp.infrastructure.orm",
            "publishing.infrastructure",
            "social_connect.infrastructure",
            "entitlements.infrastructure",
            "entitlements.service",
        ):
            assert forbidden not in text, (path.name, forbidden)


def test_entitlements_does_not_import_pulse() -> None:
    """The check-(6) adapter is duck-typed: entitlements must stay ignorant of pulse."""
    for path in (SRC / "modules" / "entitlements").rglob("*.py"):
        assert not _imports_pulse(path), path.name


def test_removability_doc_is_complete() -> None:
    doc = (SRC / "modules" / "pulse" / "REMOVABILITY.md").read_text(encoding="utf-8")
    for needle in (
        "pulse_alerts",
        "FEATURE_PULSE",
        "PULSE_INTERVAL_SECONDS",
        "/api/v1/brands/{brand_id}/pulse/alerts",
        "/api/v1/brands/{brand_id}/pulse/alerts/{alert_id}/ack",
        "PlanCounterUsageReader",
        "pulse_scheduler_step",
        "wa_window_closing",
        "wa_daily_cap_80pct",
        "alembic downgrade 0015",
    ):
        assert needle in doc, needle
