"""Shared public-host guard (SPRINT-11.1 B-4, D-077).

A tiny copy of the address test used by ``brand_brain/public_fetch.py`` so the provider
catalog can refuse private / loopback / link-local / multicast / reserved endpoints without
touching the SSRF module (``public_fetch.py`` keeps its own ``_is_global_address`` — §5-2:
0 diff). The function is pure (no network); the catalog resolves the host itself.
"""

from __future__ import annotations

import ipaddress
from typing import Any

__all__ = ["host_port_key", "is_global_address", "is_global_ip_literal"]


def is_global_address(sockaddr: tuple[Any, ...]) -> bool:
    """``is_global`` **and** not multicast / reserved — identical semantics to
    ``brand_brain.public_fetch._is_global_address`` (a scope id ``%eth0`` is dropped)."""
    raw = str(sockaddr[0]).split("%", 1)[0]
    return is_global_ip_literal(raw)


def is_global_ip_literal(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def host_port_key(host: str, port: int | None, *, default_port: int) -> str:
    """``host:port`` as listed in ``AI_ALLOW_LOCAL_PROVIDER_HOSTS`` (lower-case host, the
    scheme default port when the URL has none)."""
    return f"{host.lower().rstrip('.')}:{port or default_port}"
