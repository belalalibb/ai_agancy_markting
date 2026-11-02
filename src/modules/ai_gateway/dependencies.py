"""FastAPI wiring for the gateway (pattern of ``get_encryption_service``: misconfiguration
⇒ 503, never a stack trace)."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request, status
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.database import get_db_session
from src.core.security import get_token_encryption_service
from src.modules.ai_gateway.budget import BudgetUsageReader, DailyBudget, EntitlementLimitsReader
from src.modules.ai_gateway.budget_guard import BudgetGuard
from src.modules.ai_gateway.catalog.domain import CatalogSnapshot
from src.modules.ai_gateway.catalog.service import CatalogReader
from src.modules.ai_gateway.cost_log import CostLogWriter
from src.modules.ai_gateway.exceptions import AIProviderNotConfiguredError
from src.modules.ai_gateway.facades import FakeProvider, HttpOpenAICompatibleProvider
from src.modules.ai_gateway.gateway import AIGateway
from src.modules.ai_gateway.pricing import PriceSource, PriceTable

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
    price_table: PriceSource | None = None,
) -> BudgetGuard:
    """Pure factory used by every consumer (also by ``brand_brain`` for ``X-AI-Key``).
    ``price_table`` defaults to the static ``AI_PRICE_TABLE_JSON`` table; the catalog passes
    its own per-model ``CatalogPriceTable`` (SPRINT-11.1 B-3)."""
    return BudgetGuard(
        inner,
        writer=writer,
        price_table=price_table if price_table is not None else build_price_table(settings),
        daily_budget=DailyBudget(settings.ai_daily_token_budget_per_brand),
        platform_daily_usd_cap=settings.platform_daily_usd_cap,
        usage_reader=usage_reader,
        limits_reader=limits_reader,
        strict_pricing=settings.feature_budget_guard_strict,
        request_id=request_id,
    )


# --------------------------------------------------------- SPRINT-11.1 B-3 --
def get_catalog_reader(settings: Annotated[Settings, Depends(get_settings)]) -> CatalogReader:
    """The cached catalog snapshot reader (D-077): no request session — it reads through an
    independent transaction only when a snapshot is actually requested, so the switch-off
    path performs no catalog I/O. Local import: deleting ``ai_gateway/catalog/`` and this
    block removes the feature (D-076)."""
    from src.modules.ai_gateway.catalog.infrastructure.pg_repository import (
        CachedCatalogReader,
    )

    return CachedCatalogReader(ttl_seconds=settings.ai_catalog_cache_seconds)


CatalogSnapshotReader = Annotated[CatalogReader, Depends(get_catalog_reader)]


def build_catalog_gateway(
    settings: Settings,
    *,
    snapshot: CatalogSnapshot,
    reader: CatalogReader,
    writer: CostLogWriter,
    usage_reader: BudgetUsageReader,
    limits_reader: EntitlementLimitsReader | None,
    request_id: str | None,
    http_client: httpx.AsyncClient | None,
    user_key: str | None = None,
) -> AIGateway:
    """Pure factory: ``CatalogGatewayResolver`` whose **every attempt** is wrapped in its own
    ``BudgetGuard`` priced from ``ai_models`` (two cost rows on primary-503 + fallback-200).
    Credentials are decrypted at client-build time only (Zero-Secret)."""
    from src.modules.ai_gateway.catalog.service import (
        CatalogGatewayResolver,
        CatalogPriceTable,
        CredentialResolver,
        ProviderCatalog,
    )

    prices = CatalogPriceTable(snapshot)
    encryption = get_token_encryption_service(settings) if settings.token_encryption_key else None

    def guard_factory(inner: AIGateway, _model: str) -> AIGateway:
        return build_budget_guard(
            inner,
            settings,
            writer=writer,
            usage_reader=usage_reader,
            limits_reader=limits_reader,
            request_id=request_id,
            price_table=prices,
        )

    return CatalogGatewayResolver(
        ProviderCatalog(reader),
        credentials=CredentialResolver(encryption),
        guard_factory=guard_factory,
        http_client=http_client,
        user_key=SecretStr(user_key) if user_key else None,
    )


async def get_budget_guard(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    writer: CostWriter,
    usage_reader: BudgetReader,
    limits_reader: LimitsReader,
    catalog: CatalogSnapshotReader,
) -> AIGateway:
    """The single dependency every AI consumer uses instead of the raw gateway.

    ``FEATURE_PROVIDER_CATALOG_ENABLED=false`` (default) ⇒ exactly the SPRINT-11 behaviour:
    one ``BudgetGuard`` around ``get_ai_gateway`` (tests still override ``get_ai_gateway``).
    ``true`` ⇒ the catalog resolver (routing + fallback + per-model prices, D-077).
    """
    request_id = request.headers.get("x-request-id")
    if settings.feature_provider_catalog_enabled:
        return build_catalog_gateway(
            settings,
            snapshot=await catalog.snapshot(),
            reader=catalog,
            writer=writer,
            usage_reader=usage_reader,
            limits_reader=limits_reader,
            request_id=request_id,
            http_client=getattr(request.app.state, "http_client", None),
        )
    inner = await _resolve_inner(request, settings)
    return build_budget_guard(
        inner,
        settings,
        writer=writer,
        usage_reader=usage_reader,
        limits_reader=limits_reader,
        request_id=request_id,
    )


async def _resolve_inner(request: Request, settings: Settings) -> AIGateway:
    """``get_ai_gateway`` through the app's ``dependency_overrides`` (tests override it)."""
    override = request.app.dependency_overrides.get(get_ai_gateway)
    if override is not None:
        return override()
    return get_ai_gateway(request, settings)


GuardedGateway = Annotated[AIGateway, Depends(get_budget_guard)]
