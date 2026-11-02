"""SPRINT-11.1 Part B — provider catalog (D-077), DB-less.

Covers: purpose alias mapping, host guard, per-model price table, credential resolution,
the gateway resolver's fallback contract (two cost rows on 503→200, no fallback on
``bad_request`` or ``user_key``, 503 ``ai_model_unpriced`` **before** any provider call),
the in-process snapshot cache and the ``FEATURE_PROVIDER_CATALOG_ENABLED`` kill switch.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
import respx
from httpx import Response
from pydantic import SecretStr, ValidationError
from src.core.config import Settings
from src.core.security import TokenEncryptionService
from src.modules.ai_gateway.budget import NullBudgetUsageReader
from src.modules.ai_gateway.budget_guard import BudgetGuard, UnpricedModelError
from src.modules.ai_gateway.catalog.domain import (
    PURPOSE_ALIASES,
    PURPOSES,
    CatalogSnapshot,
    Model,
    ModelPricing,
    Provider,
    Route,
    catalog_purpose,
)
from src.modules.ai_gateway.catalog.infrastructure.pg_repository import (
    CachedCatalogReader,
    CatalogCache,
)
from src.modules.ai_gateway.catalog.service import (
    FALLBACK_CATEGORIES,
    CatalogGatewayResolver,
    CatalogNotFoundError,
    CatalogPriceTable,
    CredentialResolver,
    CredentialUnavailableError,
    NoRouteError,
    ProviderCatalog,
    ProviderHostNotAllowedError,
    build_provider_client,
    validate_provider_host,
)
from src.modules.ai_gateway.contract import AIError, ErrorCategory
from src.modules.ai_gateway.cost_log import CostOutcome
from src.modules.ai_gateway.dependencies import (
    build_budget_guard,
    build_catalog_gateway,
)
from src.modules.ai_gateway.facades import FakeProvider
from src.modules.ai_gateway.gateway import AIGateway, CompletionRequest
from src.modules.ai_gateway.pricing import PriceSource, PriceTable

from tests.fakes import FakeCostLogWriter
from tests.modules.ai_gateway.test_providers import KEY, _ok_body

BRAND = uuid4()
TENANT = uuid4()
ZERO = Decimal("0")


# ------------------------------------------------------------------ helpers --
def _req(**overrides: Any) -> CompletionRequest:
    base: dict[str, Any] = {
        "purpose": "content_draft",
        "system": "s",
        "user": "u",
        "brand_id": BRAND,
        "tenant_id": TENANT,
        "max_tokens": 50,
    }
    base.update(overrides)
    return CompletionRequest(**base)


def _provider(slug: str = "fake", **kw: Any) -> Provider:
    base: dict[str, Any] = {"slug": slug, "kind": "fake", "base_url": "https://llm.example/v1"}
    base.update(kw)
    return Provider(**base)


def _model(provider: Provider, name: str, price: Decimal | None = ZERO, **kw: Any) -> Model:
    pricing = ModelPricing() if price is None else ModelPricing.token(price, price)
    return Model(provider_id=provider.id, model_name=name, pricing=pricing, **kw)


def _snapshot(
    *chain: tuple[Provider, Model], purpose: str = "content_generation"
) -> CatalogSnapshot:
    providers = {p.id: p for p, _ in chain}
    return CatalogSnapshot(
        providers=tuple(providers.values()),
        models=tuple(m for _, m in chain),
        routes=tuple(
            Route(purpose=purpose, model_id=m.id, priority=i) for i, (_, m) in enumerate(chain)
        ),
    )


class StaticReader:
    def __init__(self, snapshot: CatalogSnapshot) -> None:
        self.snapshot_value = snapshot
        self.calls = 0

    async def snapshot(self) -> CatalogSnapshot:
        self.calls += 1
        return self.snapshot_value


class Failing:
    """Provider that fails ``n`` times with ``category`` then answers like ``FakeProvider``."""

    name = "fake"

    def __init__(self, model: str, category: ErrorCategory, times: int = 99) -> None:
        self.model = model
        self._category = category
        self._times = times
        self.calls = 0

    async def complete(self, req: CompletionRequest):  # noqa: ANN201
        self.calls += 1
        if self.calls <= self._times:
            raise AIError(self._category)
        return await FakeProvider(model=self.model).complete(req)


def _guard_factory(
    writer: FakeCostLogWriter, prices: PriceSource, *, cap: Decimal = Decimal("25")
):
    def factory(inner: AIGateway, _model: str) -> AIGateway:
        return BudgetGuard(
            inner,
            writer=writer,
            price_table=prices,
            daily_budget=__import__(
                "src.modules.ai_gateway.budget", fromlist=["DailyBudget"]
            ).DailyBudget(200_000),
            platform_daily_usd_cap=cap,
            usage_reader=NullBudgetUsageReader(),
        )

    return factory


def _resolver(
    snapshot: CatalogSnapshot,
    writer: FakeCostLogWriter,
    *,
    clients: dict[str, AIGateway] | None = None,
    user_key: SecretStr | None = None,
    max_attempts: int = 3,
) -> CatalogGatewayResolver:
    resolver = CatalogGatewayResolver(
        ProviderCatalog(StaticReader(snapshot)),
        credentials=CredentialResolver(None, env={}),
        guard_factory=_guard_factory(writer, CatalogPriceTable(snapshot)),
        user_key=user_key,
        max_attempts=max_attempts,
    )
    if clients:
        # swap the facade builder for scripted clients keyed by model name
        import src.modules.ai_gateway.catalog.service as svc

        original = svc.build_provider_client

        def scripted(resolved, **_: Any):  # noqa: ANN001, ANN202
            return clients.get(resolved.model.model_name) or original(
                resolved, credentials=CredentialResolver(None, env={}), http_client=None
            )

        async def _attempt(candidate, req):  # noqa: ANN001, ANN202
            inner = scripted(candidate)
            guarded = resolver._guard_factory(inner, candidate.model.model_name)
            resolver.attempts.append(candidate.model.model_name)
            return await guarded.complete(req)

        resolver._attempt = _attempt  # type: ignore[method-assign]
    return resolver


# ------------------------------------------------------------------- domain --
def test_purpose_aliases_cover_every_gateway_purpose_and_land_in_the_check_set() -> None:
    from typing import get_args

    from src.modules.ai_gateway.gateway import Purpose as GatewayPurpose

    gateway_purposes = set(get_args(GatewayPurpose)) - {"admin_probe"}
    assert gateway_purposes <= set(PURPOSE_ALIASES)
    assert set(PURPOSE_ALIASES.values()) <= set(PURPOSES)
    assert catalog_purpose("reply_suggestion") == "whatsapp_suggest_reply"
    assert catalog_purpose("competitor_gap") == "brand_diagnosis"
    assert catalog_purpose("admin_probe") is None


def test_provider_refuses_two_credentials_and_reports_has_credential() -> None:
    with pytest.raises(ValidationError):
        _provider(credential_cipher="c", credential_ref="REF")
    assert _provider().has_credential is False
    assert _provider(credential_ref="OPENAI_KEY").has_credential is True


def test_model_pricing_shape_matches_capability() -> None:
    p = _provider()
    with pytest.raises(ValidationError):
        Model(
            provider_id=p.id,
            model_name="whisper",
            capability="audio",
            pricing=ModelPricing.token(ZERO, ZERO),
        )
    with pytest.raises(ValidationError):
        Model(
            provider_id=p.id,
            model_name="gpt",
            capability="text",
            pricing=ModelPricing.audio(ZERO, 60),
        )
    with pytest.raises(ValidationError):
        ModelPricing.audio(ZERO, 121)
    audio = Model(
        provider_id=p.id,
        model_name="whisper",
        capability="audio",
        pricing=ModelPricing.audio(Decimal("0.006"), 120),
    )
    assert audio.pricing.token_unit is None
    assert audio.pricing.is_shape_for("audio")


def test_route_priority_bounds_and_purpose_literal() -> None:
    with pytest.raises(ValidationError):
        Route(purpose="content_generation", model_id=uuid4(), priority=40_000)
    with pytest.raises(ValidationError):
        Route(purpose="content_draft", model_id=uuid4(), priority=0)  # gateway name, not catalog


# --------------------------------------------------------------- host guard --
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1",
        "http://localhost:11434/v1",
        "http://10.0.0.5/v1",
        "http://[::1]:8080/v1",
        "http://169.254.169.254/latest",
        "ftp://llm.example/v1",
        "http:///v1",
        "http://app.localhost/v1",
    ],
)
def test_private_or_malformed_hosts_are_refused(url: str) -> None:
    with pytest.raises(ProviderHostNotAllowedError) as info:
        validate_provider_host(url, [])
    assert info.value.error == "provider_host_not_allowed"
    assert info.value.status == 422


def test_allow_listed_local_host_and_public_hosts_pass() -> None:
    assert (
        validate_provider_host("http://localhost:11434/v1", ["localhost:11434"])
        == "localhost:11434"
    )
    assert (
        validate_provider_host("http://127.0.0.1:11434/v1", [" 127.0.0.1:11434 "])
        == "127.0.0.1:11434"
    )
    assert validate_provider_host("https://api.openai.com/v1", []) == "api.openai.com:443"
    assert validate_provider_host("http://8.8.8.8/v1", []) == "8.8.8.8:80"
    # an allow-list entry for another port does not open this one
    with pytest.raises(ProviderHostNotAllowedError):
        validate_provider_host("http://localhost:11435/v1", ["localhost:11434"])


# ------------------------------------------------------------------ catalog --
async def test_resolve_orders_by_priority_and_skips_disabled_links() -> None:
    p_on, p_off = _provider("on"), _provider("off", enabled=False)
    m1, m2, m3 = _model(p_on, "a"), _model(p_on, "b", enabled=False), _model(p_off, "c")
    m4 = _model(p_on, "d")
    snap = CatalogSnapshot(
        providers=(p_on, p_off),
        models=(m1, m2, m3, m4),
        routes=(
            Route(purpose="content_generation", model_id=m4.id, priority=0),
            Route(purpose="content_generation", model_id=m1.id, priority=5),
            Route(purpose="content_generation", model_id=m2.id, priority=1),
            Route(purpose="content_generation", model_id=m3.id, priority=2),
            Route(purpose="content_generation", model_id=uuid4(), priority=3, enabled=False),
        ),
    )
    catalog = ProviderCatalog(StaticReader(snap))
    resolved = await catalog.resolve("content_draft")  # gateway alias
    assert [r.model.model_name for r in resolved] == ["d", "a"]
    assert [r.priority for r in resolved] == [0, 5]
    with pytest.raises(NoRouteError) as info:
        await catalog.resolve("content_plan")
    assert info.value.error == "ai_route_unavailable"
    assert info.value.status == 503
    assert (await catalog.model_by_id(m1.id))[1] is m1
    with pytest.raises(CatalogNotFoundError):
        await catalog.model_by_id(uuid4())
    orphan = CatalogSnapshot(models=(m1,))
    with pytest.raises(CatalogNotFoundError):
        await ProviderCatalog(StaticReader(orphan)).model_by_id(m1.id)


def test_catalog_price_table_prefers_priced_entry_and_prices_audio() -> None:
    p = _provider()
    unpriced = _model(p, "dup", None)
    priced = _model(_provider("other"), "dup", Decimal("0.5"))
    audio = Model(
        provider_id=p.id,
        model_name="whisper",
        capability="audio",
        pricing=ModelPricing.audio(Decimal("0.006"), 60),
    )
    table = CatalogPriceTable(CatalogSnapshot(models=(unpriced, priced, audio)))
    assert table.models == frozenset({"dup", "whisper"})
    assert table.estimate_unit("dup") == (Decimal("0.5"), Decimal("0.5"))
    assert table.estimate("dup", 1000, 1000) == Decimal("1.000000")
    assert table.estimate("dup", None, 1) is None
    assert table.estimate("missing", 1, 1) is None
    assert table.estimate_audio("whisper", 30) == Decimal("0.003000")
    assert table.estimate_audio("whisper", 61) is None
    assert table.estimate_audio("whisper", -1) is None
    assert table.estimate_audio("dup", 10) is None
    assert table.estimate_audio("missing", 10) is None
    unpriced_audio = Model(provider_id=p.id, model_name="w2", capability="audio")
    assert (
        CatalogPriceTable(CatalogSnapshot(models=(unpriced_audio,))).estimate_audio("w2", 1)
        is None
    )
    assert isinstance(table, PriceSource)
    assert isinstance(PriceTable(), PriceSource)


def test_credential_resolver_paths() -> None:
    enc = TokenEncryptionService(TokenEncryptionService.generate_key())
    cipher = enc.encrypt_token(KEY)
    assert (
        CredentialResolver(enc).resolve(_provider(credential_cipher=cipher)).get_secret_value()
        == KEY
    )
    with pytest.raises(CredentialUnavailableError):
        CredentialResolver(None).resolve(_provider(credential_cipher=cipher))
    env = {"OPENAI_KEY": KEY}
    assert (
        CredentialResolver(None, env=env)
        .resolve(_provider(credential_ref="OPENAI_KEY"))
        .get_secret_value()
        == KEY
    )
    with pytest.raises(CredentialUnavailableError) as info:
        CredentialResolver(None, env={}).resolve(_provider(credential_ref="OPENAI_KEY"))
    assert info.value.error == "provider_credential_unavailable"
    assert CredentialResolver(None, env={}).resolve(_provider()) is None


def test_build_provider_client_kinds() -> None:
    fake_p = _provider()
    fake = build_provider_client(
        __import__(
            "src.modules.ai_gateway.catalog.domain", fromlist=["ResolvedModel"]
        ).ResolvedModel(provider=fake_p, model=_model(fake_p, "fake-9"), priority=0),
        credentials=CredentialResolver(None, env={}),
        http_client=None,
    )
    assert isinstance(fake, FakeProvider)
    assert fake.model == "fake-9"
    from src.modules.ai_gateway.catalog.domain import ResolvedModel
    from src.modules.ai_gateway.facades import HttpOpenAICompatibleProvider

    http_p = _provider("oa", kind="openai_compatible", credential_ref="K")
    resolved = ResolvedModel(provider=http_p, model=_model(http_p, "gpt-x"), priority=0)
    client = build_provider_client(
        resolved, credentials=CredentialResolver(None, env={"K": KEY}), http_client=None
    )
    assert isinstance(client, HttpOpenAICompatibleProvider)
    with pytest.raises(CredentialUnavailableError):
        build_provider_client(
            ResolvedModel(
                provider=_provider("nk", kind="openai_compatible"),
                model=_model(http_p, "m"),
                priority=0,
            ),
            credentials=CredentialResolver(None, env={}),
            http_client=None,
        )
    # the caller's own key wins over the stored credential
    user = build_provider_client(
        resolved,
        credentials=CredentialResolver(None, env={}),
        http_client=None,
        user_key=SecretStr("sk-user"),
    )
    assert isinstance(user, HttpOpenAICompatibleProvider)


# ----------------------------------------------------------------- resolver --
async def test_fallback_503_then_200_writes_two_cost_rows_priced_per_model() -> None:
    p = _provider()
    primary, secondary = _model(p, "prim", Decimal("1")), _model(p, "sec", Decimal("2"))
    writer = FakeCostLogWriter()
    resolver = _resolver(
        _snapshot((p, primary), (p, secondary)),
        writer,
        clients={"prim": Failing("prim", ErrorCategory.PROVIDER_UNAVAILABLE)},
    )
    result = await resolver.complete(_req())
    assert result.model == "sec"
    assert resolver.attempts == ["prim", "sec"]
    outcomes = [(e.model, e.outcome) for e in writer.entries]
    assert outcomes == [("prim", CostOutcome.ERROR), ("sec", CostOutcome.OK)]
    err, ok = writer.entries
    assert err.error_category == "provider_unavailable"
    sec_price = CatalogPriceTable(_snapshot((p, primary), (p, secondary)))
    expected = sec_price.estimate(
        "sec", result.usage.prompt_tokens, result.usage.completion_tokens
    )
    assert ok.estimated_cost_usd == expected  # priced with model "sec" ($2/1k), not "prim"
    assert err.model == "prim" and err.estimated_cost_usd is not None


@pytest.mark.parametrize("category", sorted(FALLBACK_CATEGORIES, key=lambda c: c.value))
async def test_every_fallback_category_falls_through(category: ErrorCategory) -> None:
    p = _provider()
    a, b = _model(p, "a"), _model(p, "b")
    writer = FakeCostLogWriter()
    resolver = _resolver(_snapshot((p, a), (p, b)), writer, clients={"a": Failing("a", category)})
    assert (await resolver.complete(_req())).model == "b"


async def test_bad_request_never_falls_back_and_is_refunded() -> None:
    p = _provider()
    a, b = _model(p, "a"), _model(p, "b")
    writer = FakeCostLogWriter()
    resolver = _resolver(
        _snapshot((p, a), (p, b)), writer, clients={"a": Failing("a", ErrorCategory.BAD_REQUEST)}
    )
    with pytest.raises(AIError) as info:
        await resolver.complete(_req())
    assert info.value.category is ErrorCategory.BAD_REQUEST
    assert resolver.attempts == ["a"]
    assert writer.entries == [] and len(writer.refunded) == 1


async def test_max_three_attempts_then_last_error_propagates() -> None:
    p = _provider()
    models = [_model(p, n) for n in ("a", "b", "c", "d")]
    writer = FakeCostLogWriter()
    clients = {m.model_name: Failing(m.model_name, ErrorCategory.TIMEOUT) for m in models}
    resolver = _resolver(_snapshot(*[(p, m) for m in models]), writer, clients=clients)
    with pytest.raises(AIError) as info:
        await resolver.complete(_req())
    assert info.value.category is ErrorCategory.TIMEOUT
    assert resolver.attempts == ["a", "b", "c"]
    assert clients["d"].calls == 0
    assert [e.outcome for e in writer.entries] == [CostOutcome.ERROR] * 3


async def test_user_key_never_falls_back() -> None:
    p = _provider()
    a, b = _model(p, "a"), _model(p, "b")
    writer = FakeCostLogWriter()
    resolver = _resolver(
        _snapshot((p, a), (p, b)),
        writer,
        clients={"a": Failing("a", ErrorCategory.PROVIDER_UNAVAILABLE)},
        user_key=SecretStr("sk-user"),
    )
    with pytest.raises(AIError):
        await resolver.complete(_req())
    assert resolver.attempts == ["a"]
    # credential_mode=user_key on the request has the same effect (without a key object)
    writer2 = FakeCostLogWriter()
    resolver2 = _resolver(
        _snapshot((p, a), (p, b)),
        writer2,
        clients={"a": Failing("a", ErrorCategory.PROVIDER_UNAVAILABLE)},
    )
    with pytest.raises(AIError):
        await resolver2.complete(_req(credential_mode="user_key"))
    assert resolver2.attempts == ["a"]


async def test_unpriced_model_is_503_before_any_provider_call() -> None:
    p = _provider("oa", kind="openai_compatible", credential_ref="K")
    model = _model(p, "gpt-x", None)
    writer = FakeCostLogWriter()
    snap = _snapshot((p, model))
    resolver = CatalogGatewayResolver(
        ProviderCatalog(StaticReader(snap)),
        credentials=CredentialResolver(None, env={"K": KEY}),
        guard_factory=_guard_factory(writer, CatalogPriceTable(snap)),
    )
    with respx.mock(assert_all_called=False) as router:
        route = router.post("https://llm.example/v1/chat/completions").mock(
            return_value=Response(200, json=_ok_body())
        )
        with pytest.raises(UnpricedModelError) as info:
            await resolver.complete(_req())
    assert info.value.error == "ai_model_unpriced"
    assert route.call_count == 0
    assert writer.entries == []


async def test_zero_zero_price_is_a_valid_price() -> None:
    p = _provider()
    writer = FakeCostLogWriter()
    resolver = _resolver(_snapshot((p, _model(p, "free", ZERO))), writer)
    result = await resolver.complete(_req())
    assert result.model == "free"
    assert writer.entries[0].estimated_cost_usd == ZERO
    assert writer.entries[0].outcome is CostOutcome.OK


async def test_no_route_is_503_ai_route_unavailable() -> None:
    writer = FakeCostLogWriter()
    resolver = _resolver(CatalogSnapshot(), writer)
    with pytest.raises(NoRouteError):
        await resolver.complete(_req())
    assert writer.entries == []
    assert resolver.name == "catalog" and resolver.model == "catalog"


async def test_admin_probe_without_brand_skips_brand_checks_and_logs_null_brand() -> None:
    p = _provider()
    writer = FakeCostLogWriter()
    resolver = _resolver(
        _snapshot((p, _model(p, "probe-m")), purpose="content_generation"), writer
    )
    # admin_probe has no catalog purpose of its own: the route list is resolved by the purpose
    # literal, so the probe router passes the target model explicitly (Part C). Here we only
    # prove the BudgetGuard contract for brand_id=None.
    guard = _guard_factory(writer, CatalogPriceTable(_snapshot((p, _model(p, "probe-m")))))(
        FakeProvider(model="probe-m"), "probe-m"
    )
    result = await guard.complete(_req(purpose="admin_probe", brand_id=None))
    assert result.model == "probe-m"
    assert writer.entries[0].brand_id is None
    assert writer.entries[0].purpose == "admin_probe"
    del resolver


def test_brand_id_is_required_unless_admin_probe() -> None:
    with pytest.raises(ValidationError):
        _req(brand_id=None)
    assert _req(purpose="admin_probe", brand_id=None).brand_id is None


# -------------------------------------------------------------------- cache --
async def test_cached_reader_serves_within_ttl_and_honours_invalidate_and_zero_ttl() -> None:
    now = [100.0]
    cache = CatalogCache(clock=lambda: now[0])
    snap = CatalogSnapshot()
    loads = 0

    async def load() -> CatalogSnapshot:
        nonlocal loads
        loads += 1
        return snap

    reader = CachedCatalogReader(load, ttl_seconds=30, cache=cache)
    assert await reader.snapshot() is snap
    assert await reader.snapshot() is snap
    assert loads == 1
    now[0] += 31
    await reader.snapshot()
    assert loads == 2
    cache.invalidate()
    await reader.snapshot()
    assert loads == 3
    disabled = CachedCatalogReader(load, ttl_seconds=0, cache=cache)
    await disabled.snapshot()
    await disabled.snapshot()
    assert loads == 5


# -------------------------------------------------------------- kill switch --
def test_build_budget_guard_defaults_to_static_price_table_and_accepts_override() -> None:
    settings = Settings(ai_provider="fake")
    writer = FakeCostLogWriter()
    guard = build_budget_guard(
        FakeProvider(), settings, writer=writer, usage_reader=NullBudgetUsageReader()
    )
    assert guard._prices.estimate_unit("fake-1") == (ZERO, ZERO)
    custom = CatalogPriceTable(CatalogSnapshot())
    guard2 = build_budget_guard(
        FakeProvider(),
        settings,
        writer=writer,
        usage_reader=NullBudgetUsageReader(),
        price_table=custom,
    )
    assert guard2._prices is custom


async def test_build_catalog_gateway_wraps_each_attempt_in_a_budget_guard() -> None:
    p = _provider()
    snap = _snapshot((p, _model(p, "x")))
    writer = FakeCostLogWriter()
    settings = Settings(
        ai_provider="fake", token_encryption_key=TokenEncryptionService.generate_key()
    )
    gateway = build_catalog_gateway(
        settings,
        snapshot=snap,
        reader=StaticReader(snap),
        writer=writer,
        usage_reader=NullBudgetUsageReader(),
        limits_reader=None,
        request_id="req-1",
        http_client=None,
        user_key=None,
    )
    assert isinstance(gateway, CatalogGatewayResolver)
    result = await gateway.complete(_req())
    assert result.model == "x"
    assert writer.entries[0].request_id == "req-1"
    assert writer.entries[0].outcome is CostOutcome.OK
    # without an encryption key the resolver is still built (ref/env credentials keep working)
    plain = build_catalog_gateway(
        Settings(ai_provider="fake"),
        snapshot=snap,
        reader=StaticReader(snap),
        writer=writer,
        usage_reader=NullBudgetUsageReader(),
        limits_reader=None,
        request_id=None,
        http_client=None,
        user_key="sk-user",
    )
    assert plain._user_key is not None


def test_runtime_catalog_errors_map_through_budget_error_to_http() -> None:
    """B-6: ``NoRouteError`` / ``CredentialUnavailableError`` are raised *inside* the guarded
    gateway, so they must be ``BudgetGuardError``s for the consumers' one mapping ⇒ 503 with
    the literal names (no new ``ErrorCategory``)."""
    from src.modules.ai_gateway.budget_guard import BudgetGuardError
    from src.modules.ai_gateway.catalog.service import CredentialUnavailableError
    from src.modules.ai_gateway.http_errors import budget_error_to_http

    no_route = NoRouteError("content_plan")
    assert isinstance(no_route, BudgetGuardError)
    http = budget_error_to_http(no_route)
    assert http.status_code == 503
    assert http.detail == {
        "error": "ai_route_unavailable",
        "message": "no enabled route for purpose 'content_plan'",
    }
    no_cred = CredentialUnavailableError("environment variable 'X' is not set")
    assert isinstance(no_cred, BudgetGuardError)
    http = budget_error_to_http(no_cred)
    assert http.status_code == 503
    assert http.detail["error"] == "provider_credential_unavailable"
