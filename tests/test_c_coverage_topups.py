"""SPRINT-11.1 A-6 (F1) — DB-less unit coverage for the last uncovered lines named in §2-A-6
(``brand_brain/infrastructure/internal_metrics.py:35–123`` …) and the Part B/C helpers
(``core/netguard.py``, catalog router factory). No ``# pragma: no cover`` anywhere (§5-2).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from src.modules.content.domain.models import Channel  # noqa: F401 - import order (circular)


# ------------------------------------------------ brand_brain internal metrics (no DB) --
class _Row:
    def __init__(self, *values: Any) -> None:
        self._values = values

    def one(self) -> tuple[Any, ...]:
        return self._values


class _ScriptedSession:
    """Answers ``scalar()`` / ``execute().one()`` in call order — the reader issues exactly
    two scalars (conversations, published) and three row queries (wa, content, latency)."""

    def __init__(self, scalars: list[Any], rows: list[tuple[Any, ...]]) -> None:
        self._scalars = list(scalars)
        self._rows = list(rows)
        self.statements: list[Any] = []

    async def scalar(self, stmt: Any) -> Any:
        self.statements.append(stmt)
        return self._scalars.pop(0)

    async def execute(self, stmt: Any) -> _Row:
        self.statements.append(stmt)
        return _Row(*self._rows.pop(0))


@pytest.mark.asyncio
async def test_pg_internal_metrics_reader_maps_rows_to_rates_and_counts() -> None:
    from src.modules.brand_brain.infrastructure.internal_metrics import PgInternalMetricsReader

    session = _ScriptedSession(
        scalars=[7, 3],  # wa_conversations_7d, posting_frequency_7d
        rows=[(6, 8), (9, 12), (Decimal("42.5"), 4)],  # wa (approved, decided), content, latency
    )
    metrics = await PgInternalMetricsReader(session).read(  # type: ignore[arg-type]
        uuid4(), now=datetime(2026, 11, 2, tzinfo=UTC)
    )
    assert metrics.wa_conversations_7d == 7
    assert metrics.wa_approval_rate_30d == pytest.approx(0.75)
    assert metrics.content_approval_rate_30d == pytest.approx(0.75)
    assert metrics.posting_frequency_7d == 3
    assert metrics.avg_reply_latency_s == pytest.approx(42.5)
    assert (metrics.wa_decisions_30d, metrics.content_decisions_30d, metrics.reply_samples) == (
        8,
        12,
        4,
    )
    assert len(session.statements) == 5


@pytest.mark.asyncio
async def test_pg_internal_metrics_reader_empty_tables_yield_none_rates() -> None:
    from src.modules.brand_brain.infrastructure.internal_metrics import PgInternalMetricsReader

    session = _ScriptedSession(scalars=[None, None], rows=[(None, None), (None, 0), (None, 0)])
    metrics = await PgInternalMetricsReader(session).read(  # type: ignore[arg-type]
        uuid4(), now=datetime(2026, 11, 2, tzinfo=UTC)
    )
    assert metrics.wa_conversations_7d == 0 and metrics.posting_frequency_7d == 0
    assert metrics.wa_approval_rate_30d is None and metrics.content_approval_rate_30d is None
    assert metrics.avg_reply_latency_s is None and metrics.reply_samples == 0


# ----------------------------------------------------------------- core/netguard --
def test_netguard_address_helpers() -> None:
    from src.core.netguard import host_port_key, is_global_address, is_global_ip_literal

    assert is_global_address(("93.184.216.34", 443)) is True
    assert is_global_address(("fe80::1%eth0", 443, 0, 0)) is False  # scope id dropped
    assert is_global_address(("127.0.0.1", 80)) is False
    assert is_global_ip_literal("not-an-ip") is False
    assert is_global_ip_literal("224.0.0.1") is False  # multicast
    assert host_port_key("API.Example.com.", None, default_port=443) == "api.example.com:443"
    assert host_port_key("localhost", 11434, default_port=80) == "localhost:11434"


# --------------------------------------------------------- catalog router factory --
def test_catalog_admin_service_factory_builds_encryption_when_key_configured(
    settings: Any, fernet_key: str
) -> None:
    from pydantic import SecretStr
    from src.modules.ai_gateway.catalog.admin_service import CatalogAdminService
    from src.modules.ai_gateway.catalog.router import get_catalog_admin_service

    class _Session:
        pass

    with_key = settings.model_copy(
        update={
            "token_encryption_key": SecretStr(fernet_key),
            "ai_allow_local_provider_hosts": ["localhost:11434"],
        }
    )
    service = get_catalog_admin_service(_Session(), with_key)  # type: ignore[arg-type]
    assert isinstance(service, CatalogAdminService)
    assert service._encryption is not None  # noqa: SLF001 - factory wiring under test
    assert service._allow_local == ["localhost:11434"]  # noqa: SLF001
    without = settings.model_copy(update={"token_encryption_key": None})
    assert get_catalog_admin_service(_Session(), without)._encryption is None  # type: ignore[arg-type]  # noqa: SLF001


# ------------------------------------------------------------ small leftovers --
def test_channel_registry_mapping_protocol() -> None:
    from src.modules.publishing.channels import get_registry

    registry = get_registry()
    assert registry[Channel.FACEBOOK_PAGE] is registry.get(Channel.FACEBOOK_PAGE)
    assert set(iter(registry)) == set(registry._by_channel)  # noqa: SLF001
    assert len(registry) == len(list(registry))


@pytest.mark.asyncio
async def test_null_budget_usage_reader_has_no_oldest_generation() -> None:
    from src.modules.ai_gateway.budget import NullBudgetUsageReader

    reader = NullBudgetUsageReader()
    assert await reader.oldest_generation_since(uuid4(), datetime.now(tz=UTC)) is None


@pytest.mark.asyncio
async def test_entitlement_activate_refuses_months_out_of_range() -> None:
    from src.modules.entitlements.exceptions import InvalidActivationError
    from src.modules.entitlements.service import MAX_ACTIVATION_MONTHS, EntitlementService

    class _Repo:
        async def lock_brand(self, brand_id):  # noqa: ANN001, ANN202
            raise AssertionError("months are validated before any repository call")

    service = EntitlementService(_Repo())  # type: ignore[arg-type]
    for months in (0, MAX_ACTIVATION_MONTHS + 1):
        with pytest.raises(InvalidActivationError):
            await service.activate(uuid4(), "starter", months, actor_user_id=uuid4())  # type: ignore[arg-type]
