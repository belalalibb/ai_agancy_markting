"""FastAPI wiring for the gateway (pattern of ``get_encryption_service``: misconfiguration
⇒ 503, never a stack trace)."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.database import get_db_session
from src.modules.ai_gateway.budget import BudgetUsageReader, DailyBudget, EntitlementLimitsReader
from src.modules.ai_gateway.budget_guard import BudgetGuard
from src.modules.ai_gateway.cost_log import CostLogWriter
from src.modules.ai_gateway.exceptions import AIProviderNotConfiguredError
from src.modules.ai_gateway.facades import FakeProvider, HttpOpenAICompatibleProvider
from src.modules.ai_gateway.gateway import AIGateway
from src.modules.ai_gateway.pricing import PriceTable

_FAKE = FakeProvider()
_ZERO = Decimal("0")


def build_gateway(settings: Settings, http_client: httpx.AsyncClient | None = None) -> AIGateway:
    """Pure factory (no FastAPI): raises :class:`AIProviderNotConfiguredError` when
    ``openai_compatible`` is selected without a key."""
    if settings.ai_provider == "fake":
        return _FAKE
    key = settings.ai_provider_api_key
    if key is None or not key.get_secret_value():
        raise AIProviderNotConfiguredError()
    return HttpOpenAICompatibleProvider(
        base_url=settings.ai_provider_base_url,
        api_key=key,
        model=settings.ai_default_model,
        http_client=http_client,
        timeout_seconds=settings.ai_http_timeout_seconds,
    )


def get_ai_gateway(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> AIGateway:
    try:
        return build_gateway(settings, getattr(request.app.state, "http_client", None))
    except AIProviderNotConfiguredError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": exc.error, "message": "AI provider is not configured on the server."},
        ) from exc


Gateway = Annotated[AIGateway, Depends(get_ai_gateway)]


def get_budget_usage_reader(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BudgetUsageReader:
    """A-4: the one PostgreSQL token-usage reader shared by every AI feature."""
    from src.modules.ai_gateway.infrastructure.pg_budget import PgBudgetUsageReader

    return PgBudgetUsageReader(session)


BudgetReader = Annotated[BudgetUsageReader, Depends(get_budget_usage_reader)]


# ----------------------------------------------------------- SPRINT-11 B-4 --
def build_price_table(settings: Settings) -> PriceTable:
    """``AI_PRICE_TABLE_JSON`` plus ``fake-1 ⇒ (0, 0)`` whenever the provider is ``fake`` so
    CI and local development work without a price table (D-075: zero is a valid price)."""
    table = PriceTable.from_json(settings.ai_price_table_json)
    if settings.ai_provider == "fake":
        prices = {model: table.estimate_unit(model) for model in table.models}
        prices[_FAKE.model] = (_ZERO, _ZERO)
        table = PriceTable(prices)  # type: ignore[arg-type]
    return table


def get_cost_log_writer() -> CostLogWriter:
    """The one ``ai_cost_log`` writer (independent transactions, D-072)."""
    from src.modules.ai_gateway.infrastructure.pg_cost_log import PgCostLogWriter

    return PgCostLogWriter()


def get_entitlement_limits_reader(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    usage_reader: Annotated[BudgetUsageReader, Depends(get_budget_usage_reader)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EntitlementLimitsReader | None:
    """SPRINT-11 C-1: the plan-limits reader injected into ``BudgetGuard`` (D-073). This is
    the **only** import of ``entitlements`` outside ``src/main.py`` (D-076 — removing the
    module = return ``None`` here). ``None`` ⇒ no plan check; the brand daily budget and the
    platform cap still apply."""
    from src.modules.entitlements.infrastructure.postgres_repository import (
        PgCounterUsageReader,
        PgEntitlementRepository,
    )
    from src.modules.entitlements.service import PgEntitlementLimitsReader

    return PgEntitlementLimitsReader(
        PgEntitlementRepository(session),
        usage_reader=usage_reader,
        counters=PgCounterUsageReader(session),
        enforced=settings.feature_entitlements_enforced,
    )


CostWriter = Annotated[CostLogWriter, Depends(get_cost_log_writer)]
LimitsReader = Annotated[EntitlementLimitsReader | None, Depends(get_entitlement_limits_reader)]


def build_budget_guard(
    inner: AIGateway,
    settings: Settings,
    *,
    writer: CostLogWriter,
    usage_reader: BudgetUsageReader,
    limits_reader: EntitlementLimitsReader | None = None,
    request_id: str | None = None,
) -> BudgetGuard:
    """Pure factory used by every consumer (also by ``brand_brain`` for ``X-AI-Key``)."""
    return BudgetGuard(
        inner,
        writer=writer,
        price_table=build_price_table(settings),
        daily_budget=DailyBudget(settings.ai_daily_token_budget_per_brand),
        platform_daily_usd_cap=settings.platform_daily_usd_cap,
        usage_reader=usage_reader,
        limits_reader=limits_reader,
        strict_pricing=settings.feature_budget_guard_strict,
        request_id=request_id,
    )


def get_budget_guard(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    inner: Gateway,
    writer: CostWriter,
    usage_reader: BudgetReader,
    limits_reader: LimitsReader,
) -> BudgetGuard:
    """The single dependency every AI consumer uses instead of the raw gateway (``inner`` is
    resolved through ``Depends`` so tests can still override ``get_ai_gateway``)."""
    return build_budget_guard(
        inner,
        settings,
        writer=writer,
        usage_reader=usage_reader,
        limits_reader=limits_reader,
        request_id=request.headers.get("x-request-id"),
    )


GuardedGateway = Annotated[BudgetGuard, Depends(get_budget_guard)]
