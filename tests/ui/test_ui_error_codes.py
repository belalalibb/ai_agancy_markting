"""SPRINT-14-B B-5 — every literal error code declared in ``src/modules/**/exceptions.py``
has an Arabic ``error.<code>`` message (AST only: nothing is imported or executed)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from src.ui.router import I18N_PATH

MODULES = Path(__file__).resolve().parents[2] / "src" / "modules"


def _declared_codes() -> dict[str, str]:
    codes: dict[str, str] = {}
    for path in sorted(MODULES.glob("**/exceptions.py")):  # no exclusions
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign):
                target, value = node.target, node.value
            else:
                continue
            if (
                isinstance(target, ast.Name)
                and target.id in ("error", "code")
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                codes.setdefault(value.value, str(path.relative_to(MODULES)))
    return codes


def test_every_declared_error_code_has_an_arabic_message() -> None:
    messages = json.loads(I18N_PATH.read_text(encoding="utf-8"))
    codes = _declared_codes()
    assert len(codes) >= 90  # review baseline: 90 literal codes
    missing = sorted(c for c in codes if f"error.{c}" not in messages)
    assert missing == [], {c: codes[c] for c in missing}
    assert not [k for k in messages if k.startswith("errors.")]  # singular namespace only
    for code in codes:
        assert messages[f"error.{code}"].strip() and messages[f"error.{code}"] != code
