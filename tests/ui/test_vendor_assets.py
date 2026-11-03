"""SPRINT-12.1 B — vendored assets match LICENSES.md; templates carry no CDN / Arabic."""

from __future__ import annotations

import hashlib
import json
import re

from src.ui.router import I18N_PATH, STATIC_DIR, TEMPLATES_DIR, UI_DIR, t

VENDOR = STATIC_DIR / "vendor"
ARABIC = re.compile(r"[\u0600-\u06FF]")
CDN = re.compile(r"https?://|//cdn\.|unpkg|jsdelivr|cdnjs", re.IGNORECASE)
KEY = re.compile(r"""t\(["']([a-z0-9_.]+)["']\)""")


def _table_rows() -> dict[str, tuple[str, str, str]]:
    rows = {}
    for line in (VENDOR / "LICENSES.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        if len(cells) == 5 and cells[0].endswith((".js", ".css")):
            rows[cells[0]] = (cells[2], cells[3], cells[4])
    return rows


def test_vendor_sha256_matches_licenses_md() -> None:
    rows = _table_rows()
    files = sorted(p.name for p in VENDOR.iterdir() if p.suffix in {".js", ".css"})
    assert files == sorted(rows) == ["htmx.min.js", "pico.min.css"]
    for name, (version, spdx, digest) in rows.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", version), name
        assert spdx in {"0BSD", "MIT"}, name
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, name


def test_templates_have_no_cdn_and_no_arabic_literals() -> None:
    for path in TEMPLATES_DIR.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert not CDN.search(text), path.name
        assert not ARABIC.search(text), path.name
    for path in UI_DIR.glob("*.py"):
        assert not ARABIC.search(path.read_text(encoding="utf-8")), path.name


def test_every_template_key_exists_in_ar_json() -> None:
    messages = json.loads(I18N_PATH.read_text(encoding="utf-8"))
    used: set[str] = set()
    for path in TEMPLATES_DIR.rglob("*.html"):
        used |= set(KEY.findall(path.read_text(encoding="utf-8")))
    assert used and used <= set(messages), sorted(used - set(messages))
    assert "error.generic" in messages
    assert all(ARABIC.search(v) for v in messages.values())
    assert t("error.generic") == messages["error.generic"]
    assert t("no.such.key") == "no.such.key"
