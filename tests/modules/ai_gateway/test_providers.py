"""SPRINT-06 B4 — gateway providers (no SDK, no network: respx)."""

from __future__ import annotations

import logging
from uuid import uuid4

import httpx
import pytest
import respx
from pydantic import SecretStr
from src.core.config import Settings
from src.core.logging import redact
from src.modules.ai_gateway.dependencies import build_gateway
from src.modules.ai_gateway.exceptions import (
    AIProviderNotConfiguredError,
    AIRateLimitedError,
    AIUpstreamError,
)
from src.modules.ai_gateway.gateway import AIGateway, CompletionRequest
from src.modules.ai_gateway.providers import FakeProvider, HttpOpenAICompatibleProvider

BASE = "https://llm.example/v1"
# Assembled at runtime so no secret-shaped literal lives in the repo (gitleaks full-history).
KEY = "".join(("sk-", "TESTKEY", "0123456789", "abcdef"))


def _req(user: str = "اكتب منشورًا عن عرض القهوة") -> CompletionRequest:
    return CompletionRequest(
        purpose="content_draft",
        system="system rules",
        user=user,
        max_tokens=300,
        temperature=0.5,
        brand_id=uuid4(),
        tenant_id=uuid4(),
    )


def _ok_body(text: str = "منشور جاهز") -> dict:
    return {
        "model": "gpt-x",
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
    }


@pytest.fixture
def llm_mock() -> respx.MockRouter:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


def _provider() -> HttpOpenAICompatibleProvider:
    return HttpOpenAICompatibleProvider(base_url=BASE, api_key=SecretStr(KEY), model="gpt-x")


# B4-U1 — request capture: Bearer present, key not in URL, body shape, nothing in logs.
async def test_b4_u1_request_capture_key_only_in_authorization_header(
    llm_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    route = llm_mock.post("/chat/completions").mock(
        return_value=httpx.Response(200, json=_ok_body())
    )
    caplog.set_level(logging.DEBUG, logger="src.modules.ai_gateway")
    result = await _provider().complete(_req())
    assert route.called and route.call_count == 1
    request = route.calls[0].request
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert KEY not in str(request.url)
    assert dict(request.url.params) == {}
    import json

    body = json.loads(request.content)
    assert body["model"] == "gpt-x" and body["max_tokens"] == 300 and body["temperature"] == 0.5
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert result.text == "منشور جاهز"
    assert result.provider == "openai_compatible" and result.model == "gpt-x"
    assert (result.prompt_tokens, result.completion_tokens) == (12, 7)
    assert result.finish_reason == "stop" and result.latency_ms >= 0
    assert KEY not in caplog.text
    # The redaction filter would also mask it if it ever leaked.
    assert redact(f"Authorization: Bearer {KEY} and {KEY}") == (
        "Authorization: Bearer *** and sk-***"
    )
    # The user prompt is only ever logged at DEBUG, truncated.
    assert all(r.levelno <= logging.DEBUG for r in caplog.records if "user_preview" in r.msg)


# B4-U2 — 429 ⇒ AIRateLimitedError (Retry-After carried); 5xx ⇒ one retry then
# AIUpstreamError; timeout ⇒ one retry then AIUpstreamError; 4xx ⇒ no retry.
async def test_b4_u2_rate_limited_carries_retry_after(llm_mock: respx.MockRouter) -> None:
    route = llm_mock.post("/chat/completions").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "17"}, json={"error": "slow"})
    )
    with pytest.raises(AIRateLimitedError) as exc:
        await _provider().complete(_req())
    assert exc.value.retry_after_seconds == 17 and exc.value.error == "ai_rate_limited"
    assert route.call_count == 1  # 4xx: never retried


async def test_b4_u2_5xx_is_retried_once_then_upstream_error(
    llm_mock: respx.MockRouter,
) -> None:
    route = llm_mock.post("/chat/completions").mock(
        return_value=httpx.Response(503, json={"error": "down"})
    )
    with pytest.raises(AIUpstreamError) as exc:
        await _provider().complete(_req())
    assert exc.value.error == "ai_upstream_unavailable"
    assert route.call_count == 2


async def test_b4_u2_5xx_then_success_recovers(llm_mock: respx.MockRouter) -> None:
    route = llm_mock.post("/chat/completions").mock(
        side_effect=[httpx.Response(502), httpx.Response(200, json=_ok_body("ok"))]
    )
    result = await _provider().complete(_req())
    assert result.text == "ok" and route.call_count == 2


async def test_b4_u2_timeout_is_retried_once_then_upstream_error(
    llm_mock: respx.MockRouter,
) -> None:
    route = llm_mock.post("/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(AIUpstreamError):
        await _provider().complete(_req())
    assert route.call_count == 2


async def test_b4_u2_4xx_and_malformed_bodies_are_upstream_errors_without_retry(
    llm_mock: respx.MockRouter,
) -> None:
    route = llm_mock.post("/chat/completions").mock(
        return_value=httpx.Response(400, json={"error": "bad request"})
    )
    with pytest.raises(AIUpstreamError):
        await _provider().complete(_req())
    assert route.call_count == 1
    for body in ({"choices": []}, {"choices": [{"message": {"content": "   "}}]}, {"x": 1}):
        llm_mock.post("/chat/completions").mock(return_value=httpx.Response(200, json=body))
        with pytest.raises(AIUpstreamError):
            await _provider().complete(_req())
    llm_mock.post("/chat/completions").mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(AIUpstreamError):
        await _provider().complete(_req())


async def test_b4_u2_shared_client_is_used_and_not_closed(llm_mock: respx.MockRouter) -> None:
    llm_mock.post("/chat/completions").mock(return_value=httpx.Response(200, json=_ok_body()))
    async with httpx.AsyncClient() as client:
        provider = HttpOpenAICompatibleProvider(
            base_url=BASE + "/", api_key=SecretStr(KEY), model="m", http_client=client
        )
        await provider.complete(_req())
        assert not client.is_closed
        await provider.complete(_req())  # still usable


# B4-U3 — the Fake is deterministic and never touches the network.
async def test_b4_u3_fake_provider_is_deterministic(llm_mock: respx.MockRouter) -> None:
    fake = FakeProvider()
    assert isinstance(fake, AIGateway)
    a = await fake.complete(_req("brief one"))
    b = await fake.complete(_req("brief one"))
    c = await fake.complete(_req("brief two"))
    assert a.text == b.text and a.text != c.text
    assert a.provider == "fake" and a.model == "fake-1"
    assert a.text.startswith("[fake:content_draft:") and "brief one" in a.text
    assert a.prompt_tokens and a.completion_tokens and a.finish_reason == "stop"
    assert len(fake.calls) == 3
    assert not llm_mock.calls


def test_build_gateway_selects_provider_and_503_path(settings: Settings) -> None:
    assert isinstance(build_gateway(settings), FakeProvider)  # default AI_PROVIDER=fake
    cfg = settings.model_copy(update={"ai_provider": "openai_compatible"})
    with pytest.raises(AIProviderNotConfiguredError):
        build_gateway(cfg)
    with pytest.raises(AIProviderNotConfiguredError):
        build_gateway(cfg.model_copy(update={"ai_provider_api_key": SecretStr("")}))
    real = build_gateway(
        cfg.model_copy(update={"ai_provider_api_key": SecretStr(KEY), "ai_default_model": "gpt-y"})
    )
    assert isinstance(real, HttpOpenAICompatibleProvider) and real.model == "gpt-y"
    assert KEY not in repr(real) and KEY not in repr(cfg)
