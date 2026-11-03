"""SPRINT-12.1 B (D-076) — Safe Removability of ``src/ui``: only ``src/main.py`` imports it,
and the UI itself never reaches repositories, SQLAlchemy or any ``infrastructure``."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
UI = SRC / "ui"
ALLOWED_PREFIXES = (
    "fastapi",
    "starlette",
    "jinja2",
    "jwt",
    "src.core.config",
    "src.core.http",  # build_in_process_client — the only httpx holder (D-068)
    "src.modules.auth.",
    "src.ui",
)
STDLIB = {
    "__future__",
    "collections",
    "dataclasses",
    "datetime",
    "hashlib",
    "hmac",
    "json",
    "pathlib",
    "typing",
    "uuid",
}
FORBIDDEN = ("sqlalchemy", ".infrastructure", "import httpx")


def _modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mods.append(node.module or "")
        elif isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
    return mods


def test_only_main_imports_ui() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("src/ui/") or rel == "src/main.py":
            continue
        if any(m == "src.ui" or m.startswith("src.ui.") for m in _modules(path)):
            offenders.append(rel)
    assert offenders == [], offenders
    assert any(m.startswith("src.ui") for m in _modules(SRC / "main.py"))


def test_ui_imports_only_allowed_modules() -> None:
    for path in UI.rglob("*.py"):
        for module in _modules(path):
            top = module.split(".")[0]
            assert top in STDLIB or module.startswith(ALLOWED_PREFIXES), (path.name, module)
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN:
            assert needle not in text, (path.name, needle)


def test_removability_doc_is_complete() -> None:
    doc = (UI / "REMOVABILITY.md").read_text(encoding="utf-8")
    needles = (
        "src/main.py",
        "app_session",
        "tests/ui",
        "jinja2",
        "python-multipart",
        "No migration",
    )
    for needle in needles:
        assert needle in doc, needle
