"""Host-only normalisation for ``brand_competitors.website_host`` / ``allowed_hosts``.

A *host* is ``example.com`` or ``shop.example.com`` — never a URL: no scheme, no path, no
port, no credentials, no whitespace, at least one dot, DNS label characters only. The
closed-platform block list lives in ``brand_brain/public_fetch.py`` (B-2) next to the fetcher
that enforces it.
"""

from __future__ import annotations

import re
from typing import Final

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
MAX_HOST_LENGTH = 253

# D-074 (ج): closed platforms are never a legal public source — fixed, not configurable.
# ``facebook.com`` is blocked for page fetches; the Graph API path is separate (B-2).
BLOCKED_PLATFORM_HOSTS: Final[frozenset[str]] = frozenset(
    {
        "instagram.com",
        "facebook.com",
        "tiktok.com",
        "whatsapp.com",
        "linkedin.com",
        "x.com",
        "twitter.com",
        "snapchat.com",
        "youtube.com",
    }
)


def normalize_host(raw: str) -> str | None:
    """Lower-cased host or ``None`` when ``raw`` is not a bare, well-formed host name."""
    value = raw.strip().lower().rstrip(".")
    if not value or len(value) > MAX_HOST_LENGTH:
        return None
    if any(ch in value for ch in "/:@?#\\ \t\n"):
        return None
    if value.startswith("www."):
        value = value[4:]
    labels = value.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(label) for label in labels):
        return None
    if labels[-1].isdigit():  # bare IPv4 — not a brand's public site
        return None
    return value


def host_matches(host: str, pattern: str) -> bool:
    """``shop.example.com`` matches ``example.com`` (suffix on a label boundary)."""
    return host == pattern or host.endswith("." + pattern)


def is_blocked_platform(host: str) -> bool:
    return any(host_matches(host, blocked) for blocked in BLOCKED_PLATFORM_HOSTS)
