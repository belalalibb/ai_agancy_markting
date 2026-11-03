"""SPRINT-12 Part A coverage top-ups — the small branches of the H1–H6 closures that the
route-level tests do not reach (consumer error constructors, the real ``getaddrinfo``
resolver, the catalog snapshot pass-through, audio pricing guards)."""

from __future__ import annotations

import socket
from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio


def test_consumer_plan_inactive_errors_carry_plan_and_active_until() -> None:
    """A-1 (H1): both consumer-owned ``CounterPlanInactiveError`` classes are plain data
    carriers (``plan`` / ``active_until``) so ``src/main.py`` can bind them to the adapter."""
    from src.modules.content import entitlements_port as content_port
    from src.modules.whatsapp import entitlements_port as wa_port

    for cls in (content_port.CounterPlanInactiveError, wa_port.CounterPlanInactiveError):
        bare = cls()
        assert (bare.plan, bare.active_until) == (None, None) and str(bare) == "plan inactive"
        full = cls(plan="growth", active_until="2026-12-01T00:00:00+00:00")
        assert (full.plan, full.active_until) == ("growth", "2026-12-01T00:00:00+00:00")
    assert content_port.CounterPlanInactiveError is not wa_port.CounterPlanInactiveError
    assert "CounterPlanInactiveError" in content_port.__all__
    assert "CounterPlanInactiveError" in wa_port.__all__


async def test_default_resolver_uses_getaddrinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    """A-4 (H4): the production resolver is the event loop's ``getaddrinfo`` (stream sockets)
    — patched here so no network is touched."""
    import asyncio

    from src.core.netguard import default_resolver, resolve_public_addresses

    seen: list[tuple] = []

    async def fake_getaddrinfo(host, port, **kw):  # noqa: ANN001, ANN202
        seen.append((host, port, kw.get("type")))
        return [(2, 1, 6, "", ("104.18.6.192", port))]

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    assert await default_resolver("api.openai.com", 443) == [(2, 1, 6, "", ("104.18.6.192", 443))]
    assert seen == [("api.openai.com", 443, socket.SOCK_STREAM)]
    # the default path of resolve_public_addresses (resolver=None) goes through it too
    assert await resolve_public_addresses("api.openai.com", 443) == ["104.18.6.192"]
    assert await resolve_public_addresses("x.example", 443, resolver=None) == ["104.18.6.192"]


async def test_provider_catalog_snapshot_passthrough_and_audio_guards() -> None:
    """A-5 (H5): ``ProviderCatalog.snapshot`` is the reader's snapshot; ``estimate_audio``
    returns ``None`` for a half-priced audio model and for negative seconds."""
    from src.modules.ai_gateway.catalog.domain import (
        CatalogSnapshot,
        Model,
        ModelPricing,
        Provider,
    )
    from src.modules.ai_gateway.catalog.service import (
        CatalogPriceTable,
        ProviderCatalog,
        price_key,
    )

    p = Provider(slug="p", kind="fake", base_url="http://fake")
    audio = Model(
        provider_id=p.id,
        model_name="whisper",
        capability="audio",
        pricing=ModelPricing.audio(Decimal("0.006"), 60),
    )
    snap = CatalogSnapshot(providers=(p,), models=(audio,))

    class Reader:
        async def snapshot(self) -> CatalogSnapshot:
            return snap

    assert await ProviderCatalog(Reader()).snapshot() is snap
    table = CatalogPriceTable(snap)
    key = price_key(p.id, "whisper")
    assert table.estimate_audio(key, -1) is None
    assert table.estimate_audio(key, 60) == Decimal("0.006000")
    half = audio.model_copy(update={"pricing": ModelPricing(max_audio_seconds=60)})
    half_table = CatalogPriceTable(CatalogSnapshot(providers=(p,), models=(half,)))
    assert half_table.estimate_audio(key, 10) is None
