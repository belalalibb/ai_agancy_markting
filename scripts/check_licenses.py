#!/usr/bin/env python3
"""D-054 license gate — fail CI when a runtime dependency carries a disallowed license.

Run inside a *clean* virtualenv where only ``pip install .`` (no ``[dev]`` extras) was
executed, so the inspected distribution set equals the runtime tree::

    python -m venv .venv-licenses && .venv-licenses/bin/pip install .
    .venv-licenses/bin/python scripts/check_licenses.py

Rules (directive SPRINT-03 §A2):

* License source precedence: ``License-Expression`` ⇒ ``License`` (if ≤ 60 chars) ⇒
  ``Classifier: License :: …`` entries (joined with `` AND ``).
* **Reject** when the text matches ``(?<![L\\w])GPL``, ``AGPL``, ``Affero``, ``SSPL``,
  ``BUSL``, ``Commons Clause``, ``EUPL``, or is ``UNKNOWN``/empty.
* **Accept** the SPDX whitelist (MIT, MIT-0, BSD-2-Clause, BSD-3-Clause, Apache-2.0,
  PSF-2.0, ISC, MPL-2.0, Unlicense, Python-2.0, Zlib) and ``AND``/``OR`` expressions whose
  every operand is accepted. Common classifier spellings are normalised to SPDX first.
* Anything else ⇒ reject with an explicit reason (no silent grey zone).
* ``scripts/license_allowlist.txt`` may pin exact ``name==version # reason`` exceptions.
* The project itself (``ai-growth-os``) is excluded by name.

Always prints a ``package | version | license | verdict`` table. Exit 0 ⇔ no rejections.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Protocol

SELF_PACKAGE_NAMES = frozenset({"ai-growth-os", "ai_growth_os"})
ALLOWLIST_PATH = Path(__file__).with_name("license_allowlist.txt")
MAX_LICENSE_FIELD_LEN = 60

ACCEPTED_SPDX: frozenset[str] = frozenset(
    {
        "MIT",
        "MIT-0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "Apache-2.0",
        "PSF-2.0",
        "ISC",
        "MPL-2.0",
        "Unlicense",
        "Python-2.0",
        "Zlib",
    }
)

# Frequently seen non-SPDX spellings (metadata ``License`` field / classifiers) → SPDX id.
_ALIASES: dict[str, str] = {
    "mit license": "MIT",
    "mit": "MIT",
    "mit-0": "MIT-0",
    "bsd": "BSD-3-Clause",
    "bsd license": "BSD-3-Clause",
    "bsd-3": "BSD-3-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd 3-clause license": "BSD-3-Clause",
    "new bsd": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "modified bsd": "BSD-3-Clause",
    "modified bsd license": "BSD-3-Clause",
    "3-clause bsd": "BSD-3-Clause",
    "bsd-2": "BSD-2-Clause",
    "bsd 2-clause": "BSD-2-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "simplified bsd": "BSD-2-Clause",
    "2-clause bsd": "BSD-2-Clause",
    "apache": "Apache-2.0",
    "apache 2": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache-2": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "apache license": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "apache software license 2.0": "Apache-2.0",
    "psf": "PSF-2.0",
    "psf-2.0": "PSF-2.0",
    "psf license": "PSF-2.0",
    "psfl": "PSF-2.0",
    "python software foundation license": "PSF-2.0",
    "python-2.0": "Python-2.0",
    "isc": "ISC",
    "isc license": "ISC",
    "isc license (iscl)": "ISC",
    "mpl-2.0": "MPL-2.0",
    "mpl 2.0": "MPL-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "mozilla public license 2.0": "MPL-2.0",
    "unlicense": "Unlicense",
    "the unlicense": "Unlicense",
    "the unlicense (unlicense)": "Unlicense",
    "zlib": "Zlib",
    "zlib/libpng license": "Zlib",
}

REJECT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![L\w])GPL"),
    re.compile(r"AGPL"),
    re.compile(r"Affero"),
    re.compile(r"SSPL"),
    re.compile(r"BUSL"),
    re.compile(r"Commons Clause"),
    re.compile(r"EUPL"),
)

_CLASSIFIER_PREFIX = "License :: "
_CLASSIFIER_STRIP = ("OSI Approved :: ", "Other/Proprietary License")


class DistributionLike(Protocol):
    """Subset of ``importlib.metadata.Distribution`` used here (fakes in tests)."""

    @property
    def metadata(self) -> metadata.PackageMetadata: ...  # pragma: no cover - protocol

    @property
    def version(self) -> str: ...  # pragma: no cover - protocol


@dataclass(frozen=True, slots=True)
class LicenseReport:
    package: str
    version: str
    license: str
    accepted: bool
    reason: str

    @property
    def verdict(self) -> str:
        return "OK" if self.accepted else f"REJECT ({self.reason})"


# --------------------------------------------------------------------------- extraction
def _str_or_none(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def extract_license(meta: metadata.PackageMetadata) -> str:
    """Return the best license string for a distribution or ``""`` if unknown."""
    expression = _str_or_none(meta.get("License-Expression"))
    if expression:
        return expression

    field = _str_or_none(meta.get("License"))
    if field and len(field) <= MAX_LICENSE_FIELD_LEN and field.upper() != "UNKNOWN":
        return field

    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith(_CLASSIFIER_PREFIX)]
    parts: list[str] = []
    for classifier in classifiers:
        tail = classifier[len(_CLASSIFIER_PREFIX) :]
        for prefix in _CLASSIFIER_STRIP:
            tail = tail.replace(prefix, "")
        tail = tail.strip(" :")
        if tail:
            parts.append(tail)
    return " AND ".join(parts)


# --------------------------------------------------------------------------- evaluation
def _normalise(term: str) -> str:
    """Map one operand of a license expression to an SPDX id when recognised."""
    for candidate in (term.strip(), term.strip().strip("()").strip()):
        if candidate in ACCEPTED_SPDX:
            return candidate
        alias = _ALIASES.get(candidate.lower())
        if alias is not None:
            return alias
    return term.strip()


def evaluate_license(text: str) -> tuple[bool, str]:
    """Return ``(accepted, reason)`` for a license string."""
    if not text or text.strip().upper() == "UNKNOWN":
        return False, "license unknown/empty"
    for pattern in REJECT_PATTERNS:
        if pattern.search(text):
            return False, f"matches forbidden pattern {pattern.pattern!r}"

    terms = [t for t in re.split(r"\s+(?:AND|OR|and|or)\s+", text) if t.strip()]
    unknown = [t for t in terms if _normalise(t) not in ACCEPTED_SPDX]
    if unknown:
        return False, f"not in whitelist: {', '.join(unknown)}"
    return True, "whitelisted"


def load_allowlist(path: Path = ALLOWLIST_PATH) -> frozenset[str]:
    """Parse ``name==version # reason`` lines; returns the set of exact ``name==version``."""
    if not path.exists():
        return frozenset()
    pins: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and "==" in line:
            name, version = (p.strip() for p in line.split("==", 1))
            pins.add(f"{_canonical(name)}=={version}")
    return frozenset(pins)


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def audit(
    distributions: Iterable[DistributionLike],
    allowlist: frozenset[str] = frozenset(),
    *,
    exclude: frozenset[str] = SELF_PACKAGE_NAMES,
) -> list[LicenseReport]:
    reports: list[LicenseReport] = []
    for dist in distributions:
        name = _str_or_none(dist.metadata.get("Name")) or "<unnamed>"
        if _canonical(name) in {_canonical(e) for e in exclude}:
            continue
        version = dist.version or ""
        license_text = extract_license(dist.metadata)
        accepted, reason = evaluate_license(license_text)
        if not accepted and f"{_canonical(name)}=={version}" in allowlist:
            accepted, reason = True, "allowlisted (scripts/license_allowlist.txt)"
        reports.append(LicenseReport(name, version, license_text or "UNKNOWN", accepted, reason))
    reports.sort(key=lambda r: r.package.lower())
    return reports


# --------------------------------------------------------------------------- reporting
def format_table(reports: Sequence[LicenseReport]) -> str:
    headers = ("package", "version", "license", "verdict")
    rows = [(r.package, r.version, r.license, r.verdict) for r in reports]
    widths = [
        max(len(h), *(len(row[i]) for row in rows)) if rows else len(h)
        for i, h in enumerate(headers)
    ]
    line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "-+-".join("-" * w for w in widths)
    body = [" | ".join(c.ljust(widths[i]) for i, c in enumerate(row)) for row in rows]
    return "\n".join([line, sep, *body])


def main(argv: Sequence[str] | None = None) -> int:
    del argv  # no CLI options by design — the gate is not configurable from the command line
    reports = audit(metadata.distributions(), load_allowlist())
    print(format_table(reports))
    rejected = [r for r in reports if not r.accepted]
    print()
    if rejected:
        print(f"D-054 license gate FAILED: {len(rejected)} of {len(reports)} package(s) rejected.")
        for r in rejected:
            print(f"  - {r.package}=={r.version}: {r.license!r} -> {r.reason}")
        return 1
    print(f"D-054 license gate passed: {len(reports)} package(s) checked, 0 rejected.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
