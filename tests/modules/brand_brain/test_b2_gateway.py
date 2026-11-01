"""SPRINT-10 B-2 — the real-provider side: ``json_mode`` on ``openai_compatible`` (strict
JSON object ⇒ ``invalid_output``), the four new purposes, ``CostLoggingGateway`` (one row per
call, ok **and** error, metadata only), ``PriceTable`` (``AI_PRICE_TABLE_JSON``), and the
``X-AI-Key`` wiring (``credential_mode=user_key``, memory only, **no fallback** — 0 calls with
the platform Bearer when the user key fails; the key never reaches logs / cost log / response).
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
from src.modules.ai_gateway.contract import AIError, ErrorCategory
from src.modules.ai_gateway.cost_logging import CostLoggingGateway, PriceTable
from src.modules.ai_gateway.facades import FakeProvider, HttpOpenAICompatibleProvider
from src.modules.ai_gateway.facades.openai_compatible import (
    INVALID_OUTPUT_CODE,
    strict_json_object,
)
from src.modules.ai_gateway.gateway import CompletionRequest
from src.modules.brand_brain.domain.models import CostOutcome
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


# ------------------------------------------------------- CostLoggingGateway --
async def test_cost_logging_gateway_records_ok_with_estimate() -> None:
    writer = FakeCostLogWriter()
    table = PriceTable.from_json('{"fake-1": {"prompt_per_1k": 1, "completion_per_1k": 2}}')
    gateway = CostLoggingGateway(FakeProvider(), writer, price_table=table)
    req = _req(json_mode=False)
    result = await gateway.complete(req)
    assert result.provider == "fake"
    assert len(writer.entries) == 1
    entry = writer.entries[0]
    assert entry.brand_id == req.brand_id
    assert entry.purpose == "brand_diagnosis"
    assert entry.provider == "fake" and entry.model == "fake-1"
    assert entry.credential_mode == "platform"
    assert entry.outcome is CostOutcome.OK and entry.error_category is None
    assert entry.prompt_tokens == result.usage.prompt_tokens
    assert entry.completion_tokens == result.usage.completion_tokens
    expected = (Decimal(entry.prompt_tokens) * 1 + Decimal(entry.completion_tokens) * 2) / 1000
    assert entry.estimated_cost_usd == expected.quantize(Decimal("0.000001"))
    assert entry.prompt_version == "brand_diagnosis_v1"
    dumped = entry.model_dump_json()
    assert "rules" not in dumped and "profile + evidence" not in dumped  # no prompt text


async def test_cost_logging_gateway_records_error_and_reraises() -> None:
    writer = FakeCostLogWriter()
    provider = HttpOpenAICompatibleProvider(
        base_url=BASE, api_key=SecretStr(PLATFORM_KEY), model="gpt-x"
    )
    gateway = CostLoggingGateway(provider, writer)
    with respx.mock(base_url=BASE) as router:
        router.post("/chat/completions").mock(return_value=httpx.Response(429))
        with pytest.raises(AIError) as info:
            await gateway.complete(_req(prompt_version=""))
    assert info.value.category is ErrorCategory.RATE_LIMITED
    assert len(writer.entries) == 1
    entry = writer.entries[0]
    assert entry.outcome is CostOutcome.ERROR
    assert entry.error_category == "rate_limited"
    assert entry.provider == "openai_compatible" and entry.model == "gpt-x"
    assert entry.prompt_tokens is None and entry.estimated_cost_usd is None
    assert entry.prompt_version == "brand_diagnosis"  # falls back to the purpose


async def test_cost_logging_gateway_never_breaks_the_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenWriter:
        async def write(self, entry) -> None:  # noqa: ANN001
            raise RuntimeError("db down")

    gateway = CostLoggingGateway(FakeProvider(), BrokenWriter())
    with caplog.at_level(logging.ERROR):
        result = await gateway.complete(_req(json_mode=False))
    assert result.text.startswith("[fake:brand_diagnosis")
    assert "ai_cost_log write failed" in caplog.text


# ---------------------------------------------------- X-AI-Key / no fallback --
def _settings(**overrides: object) -> Settings:
    base = {
        "ai_provider": "openai_compatible",
        "ai_provider_base_url": BASE,
        "ai_provider_api_key": SecretStr(PLATFORM_KEY),
        "ai_default_model": "gpt-x",
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
        _settings(), platform=platform, writer=writer, user_key=USER_KEY, http_client=None
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
    assert len(writer.entries) == 1
    entry = writer.entries[0]
    assert entry.credential_mode == "user_key"
    assert entry.outcome is CostOutcome.ERROR
    assert USER_KEY not in entry.model_dump_json()
    assert USER_KEY not in caplog.text
    assert "USERKEY" not in caplog.text


async def test_user_key_success_carries_credential_mode() -> None:
    writer = FakeCostLogWriter()
    gateway = build_brain_gateway(
        _settings(), platform=FakeProvider(), writer=writer, user_key=USER_KEY, http_client=None
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
        _settings(), platform=platform, writer=writer, user_key=None, http_client=None
    )
    assert isinstance(gateway, CostLoggingGateway)
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
    gw = get_brain_gateway(_request(fake_settings), fake_settings, writer, x_ai_key=USER_KEY)
    assert isinstance(gw, CostLoggingGateway)
    assert isinstance(gw.inner, HttpOpenAICompatibleProvider)
    assert USER_KEY not in repr(gw.inner)
    # no key ⇒ the platform gateway (fake here)
    gw = get_brain_gateway(_request(fake_settings), fake_settings, writer, x_ai_key=None)
    assert isinstance(gw.inner, FakeProvider)
    # no key and openai_compatible without a platform key ⇒ 503 from get_ai_gateway
    from fastapi import HTTPException

    unconfigured = Settings(_env_file=None, ai_provider="openai_compatible")  # type: ignore[call-arg]
    with pytest.raises(HTTPException) as info:
        get_brain_gateway(_request(unconfigured), unconfigured, writer, x_ai_key=None)
    assert info.value.status_code == 503
