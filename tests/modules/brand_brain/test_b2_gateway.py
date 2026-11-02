"""SPRINT-10 B-2 / SPRINT-11 B-3 — the real-provider side: ``json_mode`` on
``openai_compatible`` (strict JSON object ⇒ ``invalid_output``), the four new purposes, the
``BudgetGuard`` (reserve ⇒ settle / refund, metadata only, fail-closed pricing), ``PriceTable``
(``AI_PRICE_TABLE_JSON``), and the ``X-AI-Key`` wiring (``credential_mode=user_key``, memory
only, **no fallback** — 0 calls with the platform Bearer when the user key fails; the key never
reaches logs / cost log / response).
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
import respx
from fastapi import Request
from pydantic import SecretStr, ValidationError
from src.core.config import Settings
from src.modules.ai_gateway.budget import DailyBudget, NullBudgetUsageReader
from src.modules.ai_gateway.budget_guard import (
    BrandBudgetExceededError,
    BudgetGuard,
    CostLogUnavailableError,
    EntitlementLimitError,
    PlatformBudgetExhaustedError,
    UnpricedModelError,
)
from src.modules.ai_gateway.contract import AIError, ErrorCategory
from src.modules.ai_gateway.cost_log import CostOutcome
from src.modules.ai_gateway.facades import FakeProvider, HttpOpenAICompatibleProvider
from src.modules.ai_gateway.facades.openai_compatible import (
    INVALID_OUTPUT_CODE,
    strict_json_object,
)
from src.modules.ai_gateway.gateway import CompletionRequest
from src.modules.ai_gateway.pricing import PriceTable
from src.modules.brand_brain.router import build_brain_gateway, get_brain_gateway

from tests.fakes import FakeCostLogWriter
from tests.modules.ai_gateway.test_providers import BASE, KEY, _ok_body

pytestmark = pytest.mark.unit

# Assembled at runtime — no secret-shaped literal in the repo (gitleaks).
USER_KEY = "".join(("sk-", "USERKEY", "9876543210", "zyxwvu"))
PLATFORM_KEY = KEY


def _req(**overrides: object) -> CompletionRequest:
    base: dict = {
        "purpose": "brand_diagnosis",
        "system": "rules",
        "user": "profile + evidence",
        "brand_id": uuid4(),
        "tenant_id": uuid4(),
        "json_mode": True,
        "prompt_version": "brand_diagnosis_v1",
    }
    base.update(overrides)
    return CompletionRequest(**base)


# ----------------------------------------------------------------- Purpose --
@pytest.mark.parametrize(
    "purpose", ["brand_diagnosis", "competitor_gap", "content_plan", "tone_profile"]
)
def test_new_purposes_are_accepted(purpose: str) -> None:
    assert _req(purpose=purpose).purpose == purpose


def test_unknown_purpose_rejected() -> None:
    with pytest.raises(ValidationError):
        _req(purpose="scrape_everything")


# --------------------------------------------------------------- json_mode --
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('  \n{"a": "ب"}\n', {"a": "ب"}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": [1, 2]}\n```', {"a": [1, 2]}),
    ],
)
def test_strict_json_object_accepts_objects_and_fences(text: str, expected: dict) -> None:
    assert json.loads(strict_json_object(text)) == expected


@pytest.mark.parametrize("text", ["not json", "[1, 2]", '"str"', "42", "", '{"a": 1} trailing'])
def test_strict_json_object_rejects_non_objects(text: str) -> None:
    with pytest.raises(AIError) as info:
        strict_json_object(text)
    assert info.value.category is ErrorCategory.NON_RETRYABLE_ERROR
    assert info.value.provider_code == INVALID_OUTPUT_CODE


async def test_json_mode_sends_response_format_and_parses_strictly() -> None:
    provider = HttpOpenAICompatibleProvider(
        base_url=BASE, api_key=SecretStr(PLATFORM_KEY), model="gpt-x"
    )
    with respx.mock(base_url=BASE) as router:
        route = router.post("/chat/completions").mock(
            return_value=httpx.Response(200, json=_ok_body('```json\n{"summary": "ok"}\n```'))
        )
        result = await provider.complete(_req())
    sent = json.loads(route.calls[0].request.content)
    assert sent["response_format"] == {"type": "json_object"}
    assert json.loads(result.text) == {"summary": "ok"}


async def test_json_mode_off_sends_no_response_format() -> None:
    provider = HttpOpenAICompatibleProvider(
        base_url=BASE, api_key=SecretStr(PLATFORM_KEY), model="gpt-x"
    )
    with respx.mock(base_url=BASE) as router:
        route = router.post("/chat/completions").mock(
            return_value=httpx.Response(200, json=_ok_body("plain"))
        )
        result = await provider.complete(_req(json_mode=False))
    assert "response_format" not in json.loads(route.calls[0].request.content)
    assert result.text == "plain"


async def test_json_mode_invalid_output_is_classified(caplog: pytest.LogCaptureFixture) -> None:
    provider = HttpOpenAICompatibleProvider(
        base_url=BASE, api_key=SecretStr(PLATFORM_KEY), model="gpt-x"
    )
    with respx.mock(base_url=BASE) as router:
        router.post("/chat/completions").mock(
            return_value=httpx.Response(200, json=_ok_body("Sure! Here is the plan: ..."))
        )
        with caplog.at_level(logging.WARNING), pytest.raises(AIError) as info:
            await provider.complete(_req())
    assert info.value.category is ErrorCategory.NON_RETRYABLE_ERROR
    assert info.value.provider_code == INVALID_OUTPUT_CODE
    assert "not a JSON object" in caplog.text
    assert "Here is the plan" not in caplog.text  # the completion text is never logged


# -------------------------------------------------------------- PriceTable --
def test_price_table_parsing_and_estimate() -> None:
    table = PriceTable.from_json(
        json.dumps(
            {
                "gpt-x": {"prompt_per_1k": "0.00015", "completion_per_1k": 0.0006},
                "bad": {"prompt_per_1k": "x", "completion_per_1k": 1},
                "neg": {"prompt_per_1k": -1, "completion_per_1k": 1},
                "missing": {"prompt_per_1k": 1},
                "notdict": 3,
            }
        )
    )
    assert table.models == {"gpt-x"}
    # 1000 prompt ⇒ 0.00015 ; 2000 completion ⇒ 0.0012 ⇒ 0.00135
    assert table.estimate("gpt-x", 1000, 2000) == Decimal("0.001350")
    assert table.estimate("gpt-x", None, 2000) is None
    assert table.estimate("other", 1, 1) is None
    assert PriceTable.from_json("").models == frozenset()
    assert PriceTable.from_json("not json").models == frozenset()
    assert PriceTable.from_json("[1]").models == frozenset()


# -------------------------------------------------------------- BudgetGuard --
FAKE_PRICES = PriceTable.from_json(
    '{"fake-1": {"prompt_per_1k": 1, "completion_per_1k": 2}, '
    '"gpt-x": {"prompt_per_1k": 0.5, "completion_per_1k": 1}}'
)


def _guard(inner, writer, *, prices=FAKE_PRICES, **overrides):  # noqa: ANN001, ANN202
    kwargs: dict = {
        "writer": writer,
        "price_table": prices,
        "daily_budget": DailyBudget(1_000_000),
        "platform_daily_usd_cap": Decimal("25"),
        "usage_reader": NullBudgetUsageReader(),
    }
    kwargs.update(overrides)
    return BudgetGuard(inner, **kwargs)


async def test_budget_guard_reserves_then_settles_ok_with_estimate() -> None:
    writer = FakeCostLogWriter()
    seen: list[str] = []

    async def spy(entry) -> None:  # noqa: ANN001
        seen.append(entry.outcome.value)  # the row exists *before* the provider answers

    writer.on_reserve = spy
    gateway = _guard(FakeProvider(), writer)
    req = _req(json_mode=False)
    result = await gateway.complete(req)
    assert result.provider == "fake"
    assert seen == ["pending"]
    assert len(writer.entries) == 1 and writer.settled == [writer.entries[0].id]
    entry = writer.entries[0]
    assert entry.brand_id == req.brand_id
    assert entry.purpose == "brand_diagnosis"
    assert entry.provider == "fake" and entry.model == "fake-1"
    assert entry.credential_mode == "platform"
    assert entry.outcome is CostOutcome.OK and entry.error_category is None
    assert entry.settled_at is not None
    assert entry.prompt_tokens == result.usage.prompt_tokens
    assert entry.completion_tokens == result.usage.completion_tokens
    expected = (Decimal(entry.prompt_tokens) * 1 + Decimal(entry.completion_tokens) * 2) / 1000
    assert entry.estimated_cost_usd == expected.quantize(Decimal("0.000001"))
    assert entry.prompt_version == "brand_diagnosis_v1"
    dumped = entry.model_dump_json()
    assert "rules" not in dumped and "profile + evidence" not in dumped  # no prompt text


async def test_a4_reservation_row_carries_estimated_tokens_until_settled() -> None:
    """SPRINT-11.1 A-4 (E4): the ``pending`` row is written with ``prompt_tokens =
    estimate`` and ``completion_tokens = max_tokens`` so the brand's daily budget already
    counts the reservation; the successful settle replaces both with the provider's actual
    usage (B-1: the final count is the real 150-style figure, not the estimate)."""
    from src.modules.ai_gateway.budget_guard import estimate_prompt_tokens

    writer = FakeCostLogWriter()
    snapshot: dict = {}

    async def spy(entry) -> None:  # noqa: ANN001
        snapshot["pending"] = (entry.outcome, entry.prompt_tokens, entry.completion_tokens)

    writer.on_reserve = spy
    req = _req(json_mode=False, max_tokens=321)
    result = await _guard(FakeProvider(), writer).complete(req)
    assert snapshot["pending"] == (CostOutcome.PENDING, estimate_prompt_tokens(req), 321)
    assert estimate_prompt_tokens(req) > 0
    entry = writer.entries[0]
    assert entry.outcome is CostOutcome.OK
    assert (entry.prompt_tokens, entry.completion_tokens) == (
        result.usage.prompt_tokens,
        result.usage.completion_tokens,
    )
    assert (entry.prompt_tokens, entry.completion_tokens) != snapshot["pending"][1:]


async def test_budget_guard_settles_error_for_provider_failures_and_reraises() -> None:
    writer = FakeCostLogWriter()
    provider = HttpOpenAICompatibleProvider(
        base_url=BASE, api_key=SecretStr(PLATFORM_KEY), model="gpt-x"
    )
    gateway = _guard(provider, writer)
    with respx.mock(base_url=BASE) as router:
        router.post("/chat/completions").mock(return_value=httpx.Response(429))
        with pytest.raises(AIError) as info:
            await gateway.complete(_req(prompt_version=""))
    assert info.value.category is ErrorCategory.RATE_LIMITED
    assert len(writer.entries) == 1 and writer.refunded == []
    entry = writer.entries[0]
    assert entry.outcome is CostOutcome.ERROR
    assert entry.error_category == "rate_limited"
    assert entry.provider == "openai_compatible" and entry.model == "gpt-x"
    # conservative: estimated prompt tokens + 0 completion, priced
    assert entry.prompt_tokens >= 1 and entry.completion_tokens == 0
    assert entry.estimated_cost_usd is not None
    assert entry.prompt_version == "brand_diagnosis"  # falls back to the purpose


async def test_budget_guard_refunds_when_provider_was_never_reached() -> None:
    writer = FakeCostLogWriter()
    provider = HttpOpenAICompatibleProvider(
        base_url=BASE, api_key=SecretStr(PLATFORM_KEY), model="gpt-x"
    )
    gateway = _guard(provider, writer)
    with respx.mock(base_url=BASE) as router:
        router.post("/chat/completions").mock(return_value=httpx.Response(401))
        with pytest.raises(AIError) as info:
            await gateway.complete(_req())
    assert info.value.category is ErrorCategory.INVALID_CREDENTIAL
    assert writer.entries == [] and len(writer.refunded) == 1


async def test_budget_guard_unpriced_platform_model_is_refused_before_any_call() -> None:
    writer = FakeCostLogWriter()
    provider = FakeProvider()
    gateway = _guard(provider, writer, prices=PriceTable())
    with pytest.raises(UnpricedModelError) as info:
        await gateway.complete(_req(json_mode=False))
    assert info.value.model == "fake-1" and info.value.error == "ai_model_unpriced"
    assert provider.calls == [] and writer.entries == []
    # kill switch (D-076): strict off ⇒ the call passes with a NULL cost
    relaxed = _guard(provider, writer, prices=PriceTable(), strict_pricing=False)
    await relaxed.complete(_req(json_mode=False))
    assert writer.entries[0].estimated_cost_usd is None
    assert writer.entries[0].outcome is CostOutcome.OK


async def test_budget_guard_zero_price_is_valid_and_user_key_unpriced_passes() -> None:
    writer = FakeCostLogWriter()
    zero = PriceTable.from_json('{"fake-1": {"prompt_per_1k": 0, "completion_per_1k": 0}}')
    await _guard(FakeProvider(), writer, prices=zero).complete(_req(json_mode=False))
    assert writer.entries[0].estimated_cost_usd == Decimal("0")
    await _guard(FakeProvider(), writer, prices=PriceTable()).complete(
        _req(json_mode=False, credential_mode="user_key")
    )
    assert writer.entries[1].credential_mode == "user_key"
    assert writer.entries[1].estimated_cost_usd is None


async def test_budget_guard_brand_budget_and_platform_cap_and_plan_limits() -> None:
    class Reader:
        async def combined_usage_since(self, brand_id, since) -> int:  # noqa: ANN001
            return 1_000

        async def oldest_generation_since(self, brand_id, since):  # noqa: ANN001, ANN201
            return None

    provider = FakeProvider()
    writer = FakeCostLogWriter()
    gateway = _guard(provider, writer, daily_budget=DailyBudget(1_000), usage_reader=Reader())
    with pytest.raises(BrandBudgetExceededError) as info:
        await gateway.complete(_req(json_mode=False))
    assert info.value.used == 1_000 and info.value.limit_kind == "brand_daily_tokens"
    assert provider.calls == [] and writer.entries == []

    # platform cap: spend 25 USD on the platform key ⇒ 503; user_key is not counted and passes
    await _guard(provider, writer).complete(_req(json_mode=False))
    writer.entries[0] = writer.entries[0].model_copy(update={"estimated_cost_usd": Decimal("25")})
    capped = _guard(provider, writer)
    with pytest.raises(PlatformBudgetExhaustedError) as cap_info:
        await capped.complete(_req(json_mode=False))
    assert cap_info.value.resets_in_seconds >= 1 and len(provider.calls) == 1
    await capped.complete(_req(json_mode=False, credential_mode="user_key"))
    assert len(provider.calls) == 2

    class Limits:
        async def check(self, brand_id, purpose: str) -> None:  # noqa: ANN001
            raise EntitlementLimitError("plan_inactive")

    with pytest.raises(EntitlementLimitError) as lim:
        await _guard(provider, FakeCostLogWriter(), limits_reader=Limits()).complete(
            _req(json_mode=False)
        )
    assert lim.value.limit_kind == "plan_inactive" and lim.value.error == (
        "generation_budget_exceeded"
    )


async def test_budget_guard_writer_failure_platform_503_user_key_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    writer = FakeCostLogWriter()
    writer.fail = RuntimeError("db down")
    provider = FakeProvider()
    with pytest.raises(CostLogUnavailableError):
        await _guard(provider, writer).complete(_req(json_mode=False))
    assert provider.calls == []  # fail closed: platform money is never spent unlogged
    with caplog.at_level(logging.ERROR):
        result = await _guard(provider, writer).complete(
            _req(json_mode=False, credential_mode="user_key")
        )
    assert result.text.startswith("[fake:brand_diagnosis")
    assert "ai_cost_log reserve failed" in caplog.text


async def test_budget_guard_settle_failure_after_call_is_503_on_platform() -> None:
    writer = FakeCostLogWriter()

    async def arm(entry) -> None:  # noqa: ANN001
        writer.fail = RuntimeError("db down after reserve")

    writer.on_reserve = arm
    with pytest.raises(CostLogUnavailableError):
        await _guard(FakeProvider(), writer).complete(_req(json_mode=False))
    assert writer.entries[0].outcome is CostOutcome.PENDING  # stays for settle_stale


async def test_budget_guard_non_ai_exception_is_settled_as_error() -> None:
    class Boom:
        name = "boom"
        model = "fake-1"

        async def complete(self, req):  # noqa: ANN001, ANN202
            raise RuntimeError("socket exploded")

    writer = FakeCostLogWriter()
    with pytest.raises(RuntimeError):
        await _guard(Boom(), writer).complete(_req(json_mode=False))
    assert writer.entries[0].outcome is CostOutcome.ERROR
    assert writer.entries[0].error_category == "non_retryable_error"


# ---------------------------------------------------- X-AI-Key / no fallback --
def _settings(**overrides: object) -> Settings:
    base = {
        "ai_provider": "openai_compatible",
        "ai_provider_base_url": BASE,
        "ai_provider_api_key": SecretStr(PLATFORM_KEY),
        "ai_default_model": "gpt-x",
        "ai_price_table_json": '{"fake-1": {"prompt_per_1k": 1, "completion_per_1k": 2}, '
        '"gpt-x": {"prompt_per_1k": 0.5, "completion_per_1k": 1}}',
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def _bearer_of(call: respx.models.Call) -> str:
    return call.request.headers["Authorization"].removeprefix("Bearer ")


async def test_user_key_is_used_and_marked_without_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The user's key fails (401) ⇒ ``invalid_credential`` with ``credential_mode=user_key``
    and **0** calls carrying the platform Bearer; the key appears nowhere."""
    writer = FakeCostLogWriter()
    platform = FakeProvider()
    gateway = build_brain_gateway(
        _settings(),
        platform=platform,
        writer=writer,
        user_key=USER_KEY,
        http_client=None,
        usage_reader=NullBudgetUsageReader(),
    )
    with respx.mock(base_url=BASE) as router:
        route = router.post("/chat/completions").mock(
            return_value=httpx.Response(401, json={"error": {"code": "invalid_api_key"}})
        )
        with caplog.at_level(logging.DEBUG), pytest.raises(AIError) as info:
            await gateway.complete(_req(credential_mode="user_key"))
    assert info.value.category is ErrorCategory.INVALID_CREDENTIAL
    assert route.call_count == 1
    assert _bearer_of(route.calls[0]) == USER_KEY
    assert all(_bearer_of(c) != PLATFORM_KEY for c in route.calls)
    assert platform.calls == []  # the platform gateway was never consulted
    # invalid_credential ⇒ the provider never did any work ⇒ the reservation is refunded
    assert writer.entries == [] and len(writer.refunded) == 1
    assert USER_KEY not in caplog.text
    assert "USERKEY" not in caplog.text


async def test_user_key_success_carries_credential_mode() -> None:
    writer = FakeCostLogWriter()
    gateway = build_brain_gateway(
        _settings(),
        platform=FakeProvider(),
        writer=writer,
        user_key=USER_KEY,
        http_client=None,
        usage_reader=NullBudgetUsageReader(),
    )
    with respx.mock(base_url=BASE) as router:
        route = router.post("/chat/completions").mock(
            return_value=httpx.Response(200, json=_ok_body('{"ok": true}'))
        )
        result = await gateway.complete(_req(credential_mode="user_key"))
    assert result.credential_mode == "user_key"
    assert _bearer_of(route.calls[0]) == USER_KEY
    assert writer.entries[0].credential_mode == "user_key"
    assert writer.entries[0].outcome is CostOutcome.OK


async def test_platform_gateway_is_wrapped_when_no_user_key() -> None:
    writer = FakeCostLogWriter()
    platform = FakeProvider()
    gateway = build_brain_gateway(
        _settings(),
        platform=platform,
        writer=writer,
        user_key=None,
        http_client=None,
        usage_reader=NullBudgetUsageReader(),
    )
    assert isinstance(gateway, BudgetGuard)
    assert gateway.inner is platform
    await gateway.complete(_req(json_mode=False))
    assert platform.calls and writer.entries[0].credential_mode == "platform"


def _request(settings: Settings) -> Request:
    from src.main import create_app

    app = create_app(settings)
    scope = {"type": "http", "app": app, "headers": [], "method": "GET", "path": "/"}
    return Request(scope)


def test_get_brain_gateway_dependency_paths() -> None:
    writer = FakeCostLogWriter()
    # user key ⇒ openai_compatible on the user's key even when the platform is ``fake``
    fake_settings = Settings(_env_file=None, ai_provider="fake")  # type: ignore[call-arg]
    reader = NullBudgetUsageReader()
    gw = get_brain_gateway(
        _request(fake_settings), fake_settings, writer, reader, None, x_ai_key=USER_KEY
    )
    assert isinstance(gw, BudgetGuard)
    assert isinstance(gw.inner, HttpOpenAICompatibleProvider)
    assert USER_KEY not in repr(gw.inner)
    # no key ⇒ the platform gateway (fake here)
    gw = get_brain_gateway(_request(fake_settings), fake_settings, writer, reader, None)
    assert isinstance(gw.inner, FakeProvider)
    # no key and openai_compatible without a platform key ⇒ 503 from get_ai_gateway
    from fastapi import HTTPException

    unconfigured = Settings(_env_file=None, ai_provider="openai_compatible")  # type: ignore[call-arg]
    with pytest.raises(HTTPException) as info:
        get_brain_gateway(_request(unconfigured), unconfigured, writer, reader, None)
    assert info.value.status_code == 503
