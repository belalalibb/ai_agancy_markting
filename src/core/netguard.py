"""Shared public-host guard (SPRINT-11.1 B-4, D-077; SPRINT-12 A-4 / H4).

A tiny copy of the address test used by ``brand_brain/public_fetch.py`` so the provider
catalog can refuse private / loopback / link-local / multicast / reserved endpoints without
touching the SSRF module (``public_fetch.py`` keeps its own ``_is_global_address`` — §5-2:
0 diff). SPRINT-12 A-4 adds what the catalog needs beyond the pure test:

* :func:`normalize_ip_literal` — non-canonical IPv4 spellings (``0177.0.0.1``,
  ``2130706433``, ``0x7f000001``, ``127.1``) become a canonical literal **before** the test
  (``ipaddress.ip_address`` rejects them and they would otherwise pass as "DNS names");
* :func:`is_forbidden_hostname` — ``*.internal``, ``*.local``, ``*.localhost``,
  ``*.nip.io``, ``*.sslip.io`` and bare (dot-less) names are refused without a lookup;
* :func:`resolve_public_addresses` — ``getaddrinfo`` (injectable) and **every** address must
  be global (the ``public_fetch.py:235`` contract);
* :class:`PinnedAddressTransport` — an ``httpx`` transport that re-resolves the host
  immediately before **every** request and refuses a non-global answer (DNS rebinding after
  the create-time check); the request itself is sent unchanged (SNI / Host intact).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

__all__ = [
    "PinnedAddressTransport",
    "PrivateAddressError",
    "Resolver",
    "default_resolver",
    "host_port_key",
    "is_forbidden_hostname",
    "is_global_address",
    "is_global_ip_literal",
    "normalize_ip_literal",
    "resolve_public_addresses",
]

#: ``getaddrinfo``-shaped resolver: ``(host, port) -> [(family, type, proto, canon, sockaddr)]``
Resolver = Callable[[str, int], Awaitable[list[tuple[Any, ...]]]]

_FORBIDDEN_SUFFIXES = (".internal", ".local", ".localhost", ".nip.io", ".sslip.io")
_FORBIDDEN_NAMES = frozenset({"localhost", "localhost.localdomain", "nip.io", "sslip.io"})


class PrivateAddressError(httpx.ConnectError):
    """The host resolved to a non-global address right before the request (rebinding guard)."""


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


def _int_octet(part: str) -> int | None:
    """One ``inet_aton``-style component: decimal, ``0``-octal or ``0x``-hex."""
    try:
        if part.lower().startswith("0x"):
            return int(part, 16)
        if len(part) > 1 and part.startswith("0"):
            return int(part, 8)
        return int(part, 10)
    except ValueError:
        return None


def normalize_ip_literal(host: str) -> str | None:
    """Canonical IP literal of ``host`` or ``None`` when it is not an IP spelling at all.

    Covers the ``inet_aton`` forms browsers and libc accept but ``ipaddress`` rejects:
    ``0177.0.0.1`` (octal), ``2130706433`` (one 32-bit integer), ``0x7f000001`` (hex),
    ``127.1`` / ``127.0.1`` (short forms) — all ``127.0.0.1`` — plus bracketed IPv6."""
    candidate = host.strip().strip("[]")
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        pass
    parts = candidate.split(".")
    if not candidate or not 1 <= len(parts) <= 4 or not all(parts):
        return None
    values = [_int_octet(p) for p in parts]
    if any(v is None or v < 0 for v in values):
        return None
    ints = [v for v in values if v is not None]
    *head, last = ints
    if any(v > 255 for v in head) or last >= 256 ** (4 - len(head)):
        return None
    packed = 0
    for v in head:
        packed = (packed << 8) | v
    packed = (packed << (8 * (4 - len(head)))) | last
    return str(ipaddress.IPv4Address(packed))


def is_forbidden_hostname(host: str) -> bool:
    """Names that must never reach a resolver: loopback aliases, mDNS / internal zones and
    the wildcard-DNS services that map any IP into a public-looking name."""
    name = host.lower().rstrip(".")
    if name in _FORBIDDEN_NAMES or name.endswith(_FORBIDDEN_SUFFIXES):
        return True
    return "." not in name  # a bare word is a local / search-domain name, never public


async def default_resolver(host: str, port: int) -> list[tuple[Any, ...]]:
    loop = asyncio.get_running_loop()
    return await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)


async def resolve_public_addresses(
    host: str, port: int, *, resolver: Resolver | None = None
) -> list[str]:
    """Resolve ``host`` and return its addresses **only if every one is global**; an empty
    list means unresolvable **or** at least one private / reserved answer (fail closed)."""
    try:
        infos = await (resolver or default_resolver)(host, port)
    except (OSError, ValueError):
        return []
    addresses = [str(info[4][0]).split("%", 1)[0] for info in infos if info and info[4]]
    if not addresses or not all(is_global_ip_literal(a) for a in addresses):
        return []
    return addresses


class PinnedAddressTransport(httpx.AsyncBaseTransport):
    """Wrap an ``httpx`` transport so that **every** request to a DNS-named host re-resolves
    the name immediately before it is sent and refuses a non-global answer — the
    ``public_fetch.py`` contract applied at connect time (a record that flips to a private
    address after the admin's create-time check is caught here). IP literals are
    re-normalised and tested; allow-listed ``host:port`` entries
    (``AI_ALLOW_LOCAL_PROVIDER_HOSTS``) pass through unchanged."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        *,
        allow_hosts: frozenset[str] = frozenset(),
        resolver: Resolver | None = None,
    ) -> None:
        self._inner = inner
        self._allow = allow_hosts
        self._resolver = resolver

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        if host_port_key(host, port, default_port=port) not in self._allow:
            await self.assert_public(host, port)
        return await self._inner.handle_async_request(request)

    async def assert_public(self, host: str, port: int) -> None:
        literal = normalize_ip_literal(host)
        if literal is not None:
            if not is_global_ip_literal(literal):
                raise PrivateAddressError(f"private address {literal!r} refused")
            return
        if is_forbidden_hostname(host):
            raise PrivateAddressError(f"host {host!r} refused")
        if not await resolve_public_addresses(host, port, resolver=self._resolver):
            raise PrivateAddressError(f"host {host!r} resolved to a non-public address")

    async def aclose(self) -> None:
        await self._inner.aclose()
