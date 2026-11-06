"""Latin slug derivation for Arabic names (SPRINT-14-A directive 4-B).

Pure text transformation — no business rule lives here. ``RegisterRequest.tenant_slug`` and
``BrandCreate.slug`` both require ``Slug`` (``[a-z0-9-]{3,64}``, no edge dash); an Arabic
name yields nothing usable, so the fallback is ``<prefix>-<8 hex>``. The API still validates.
"""

from __future__ import annotations

import re
import secrets
import unicodedata

MAX_SLUG = 64
MIN_SLUG = 3
_NON_SLUG = re.compile(r"[^a-z0-9]+")


def derive_slug(name: str, prefix: str) -> str:
    """ASCII-fold + lowercase, keep ``[a-z0-9]``, collapse the rest to ``-``; shorter than
    3 chars (a fully Arabic name) ⇒ ``f"{prefix}-{token_hex(4)}"``."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    candidate = _NON_SLUG.sub("-", folded.lower()).strip("-")[:MAX_SLUG].strip("-")
    if len(candidate) >= MIN_SLUG:
        return candidate
    return f"{prefix}-{secrets.token_hex(4)}"


def slug_retry(base: str, attempt: int) -> str:
    """Suffix for a taken slug: attempt 1 ⇒ ``-2``, attempt 2 ⇒ ``-<4 hex>`` (directive 4-B-4)."""
    suffix = "-2" if attempt == 1 else f"-{secrets.token_hex(2)}"
    return f"{base[: MAX_SLUG - len(suffix)].rstrip('-')}{suffix}"
