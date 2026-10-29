"""Unit tests — D-054 license gate (`scripts/check_licenses.py`, SPRINT-03 A3).

Hermetic: fake distributions are injected; nothing depends on the packages installed in
the environment running the tests.
"""

from __future__ import annotations

import sys
from email.message import Message
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import check_licenses as gate  # noqa: E402

pytestmark = pytest.mark.unit


class _FakeDist:
    def __init__(
        self,
        name: str,
        version: str,
        *,
        expression: str | None = None,
        license_field: str | None = None,
        classifiers: tuple[str, ...] = (),
    ) -> None:
        msg = Message()
        msg["Name"] = name
        msg["Version"] = version
        if expression is not None:
            msg["License-Expression"] = expression
        if license_field is not None:
            msg["License"] = license_field
        for c in classifiers:
            msg["Classifier"] = c
        self.metadata = msg
        self.version = version


def _verdicts(dists: list[_FakeDist], allowlist: frozenset[str] = frozenset()) -> dict[str, bool]:
    return {r.package: r.accepted for r in gate.audit(dists, allowlist)}


def test_gpl_is_rejected() -> None:
    reports = gate.audit([_FakeDist("copyleft-lib", "1.0", expression="GPL-3.0-only")])
    assert len(reports) == 1
    assert reports[0].accepted is False
    assert "forbidden pattern" in reports[0].reason
    assert reports[0].verdict.startswith("REJECT")


def test_or_expression_with_accepted_parts_is_accepted() -> None:
    v = _verdicts([_FakeDist("cryptography", "42.0", expression="Apache-2.0 OR BSD-3-Clause")])
    assert v == {"cryptography": True}


def test_unknown_license_is_rejected() -> None:
    v = _verdicts(
        [
            _FakeDist("mystery", "0.1", license_field="UNKNOWN"),
            _FakeDist("empty", "0.1"),
        ]
    )
    assert v == {"mystery": False, "empty": False}


def test_lgpl_is_rejected_as_outside_whitelist() -> None:
    reports = gate.audit([_FakeDist("weak-copyleft", "2.1", expression="LGPL-2.1")])
    assert reports[0].accepted is False
    # Not caught by the `(?<![L\w])GPL` pattern (the L look-behind) → rejected by whitelist.
    assert "not in whitelist" in reports[0].reason


def test_allowlist_applies_only_to_exact_name_version(tmp_path: Path) -> None:
    allow_file = tmp_path / "license_allowlist.txt"
    allow_file.write_text(
        "# comment line\nweak-copyleft==2.1  # reviewed by planner\n", encoding="utf-8"
    )
    allowlist = gate.load_allowlist(allow_file)
    assert allowlist == frozenset({"weak-copyleft==2.1"})

    v = _verdicts(
        [
            _FakeDist("weak-copyleft", "2.1", expression="LGPL-2.1"),
            _FakeDist("weak-copyleft", "2.2", expression="LGPL-2.1"),
            _FakeDist("Weak_Copyleft", "2.1", expression="LGPL-2.1"),  # canonical name match
        ],
        allowlist,
    )
    assert v["weak-copyleft"] is True or v["Weak_Copyleft"] is True
    rejected_versions = [
        r
        for r in gate.audit([_FakeDist("weak-copyleft", "2.2", expression="LGPL-2.1")], allowlist)
    ]
    assert rejected_versions[0].accepted is False


def test_missing_allowlist_file_yields_empty_set(tmp_path: Path) -> None:
    assert gate.load_allowlist(tmp_path / "nope.txt") == frozenset()


def test_classifier_fallback_and_long_license_field() -> None:
    long_text = "x" * 61
    dist = _FakeDist(
        "classified",
        "1.0",
        license_field=long_text,
        classifiers=("License :: OSI Approved :: MIT License", "Topic :: Utilities"),
    )
    assert gate.extract_license(dist.metadata) == "MIT License"
    assert _verdicts([dist]) == {"classified": True}


def test_license_field_wins_over_classifiers_when_short() -> None:
    dist = _FakeDist(
        "fielded",
        "1.0",
        license_field="BSD-2-Clause",
        classifiers=("License :: OSI Approved :: GPL",),
    )
    assert gate.extract_license(dist.metadata) == "BSD-2-Clause"


def test_self_package_is_excluded_and_table_always_printed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dists = [
        _FakeDist("ai-growth-os", "0.1.0", license_field="Proprietary"),
        _FakeDist("fastapi", "0.1", expression="MIT"),
    ]
    monkeypatch.setattr(gate.metadata, "distributions", lambda: dists)
    monkeypatch.setattr(gate, "load_allowlist", lambda: frozenset())
    assert gate.main([]) == 0
    out = capsys.readouterr().out
    assert "package" in out and "verdict" in out
    assert "fastapi" in out
    assert "ai-growth-os" not in out


def test_main_exit_1_when_rejected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dists = [_FakeDist("bad", "9", expression="AGPL-3.0")]
    monkeypatch.setattr(gate.metadata, "distributions", lambda: dists)
    monkeypatch.setattr(gate, "load_allowlist", lambda: frozenset())
    assert gate.main([]) == 1
    assert "FAILED" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("text", "accepted"),
    [
        ("MIT", True),
        ("MIT License", True),
        ("BSD License", True),
        ("Apache Software License", True),
        ("MPL-2.0 AND (Apache-2.0 OR MIT)", True),
        ("Python Software Foundation License", True),
        ("Zlib", True),
        ("Unlicense", True),
        ("Proprietary", False),
        ("SSPL-1.0", False),
        ("BUSL-1.1", False),
        ("Apache-2.0 WITH Commons Clause", False),
        ("EUPL-1.2", False),
        ("GNU Affero General Public License v3", False),
        ("MIT AND GPL-2.0", False),
    ],
)
def test_evaluate_license_matrix(text: str, accepted: bool) -> None:
    assert gate.evaluate_license(text)[0] is accepted
