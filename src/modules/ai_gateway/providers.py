"""Providers behind :class:`AIGateway` (SPRINT-06 B4).

* :class:`FakeProvider` — deterministic template from the request; used by the test-suite
  and whenever ``AI_PROVIDER=fake`` (the default until the owner picks a real provider).
* :class:`HttpOpenAICompatibleProvider` — ``POST {base_url}/chat/completions``; Bearer key
  **only** in the ``Authorization`` header; 30 s timeout; no retry on 4xx; one retry on
  5xx / timeout / network error; 429 ⇒ :class:`AIRateLimitedError`; anything else that is
  not a 2xx with ``choices[0].message.content`` ⇒ :class:`AIUpstreamError`.

Prompts are never logged at INFO (DEBUG only, truncated to 200 characters).
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import httpx
from pydantic import SecretStr

from src.modules.ai_gateway.exceptions import AIRateLimitedError, AIUpstreamError
from src.modules.ai_gateway.gateway import CompletionRequest, CompletionResult

logger = logging.getLogger(__name__)

PROMPT_LOG_CHARS = 200
_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


def _preview(text: str) -> str:
    return text[:PROMPT_LOG_CHARS] + ("…" if len(text) > PROMPT_LOG_CHARS else "")


class FakeProvider:
    """Deterministic: same request ⇒ same text. The text echoes a digest of the prompt so
    tests can prove provenance (``prompt_hash``) without any network."""

    name = "fake"

    def __init__(self, model: str = "fake-1") -> None:
        self.model = model
        self.calls: list[CompletionRequest] = []

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        started = time.perf_counter()
        self.calls.append(req)
        digest = hashlib.sha256((req.system + req.user).encode()).hexdigest()[:8]
        text = f"[fake:{req.purpose}:{digest}] {req.user.strip()[:160]}"
        return CompletionResult(
            text=text,
            provider=self.name,
            model=self.model,
            prompt_tokens=len(req.system.split()) + len(req.user.split()),
            completion_tokens=len(text.split()),
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason="stop",
        )


class HttpOpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        model: str,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model = model
        self._client = http_client
        self._timeout = httpx.Timeout(timeout_seconds)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        url = f"{self._base_url}/chat/completions"
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": req.system},
                {"role": "user", "content": req.user},
            ],
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
        logger.debug(
            "ai request purpose=%s model=%s user_preview=%r",
            req.purpose,
            self.model,
            _preview(req.user),
        )
        started = time.perf_counter()
        response = await self._send(url, body)
        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise AIRateLimitedError(
                int(retry_after) if retry_after and retry_after.isdigit() else None
            )
        if response.status_code >= 400:
            logger.warning(
                "ai upstream error status=%s model=%s", response.status_code, self.model
            )
            raise AIUpstreamError()
        try:
            payload = response.json()
            choice = payload["choices"][0]
            text = choice["message"]["content"]
            if not isinstance(text, str) or not text.strip():
                raise AIUpstreamError("empty completion")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("ai upstream returned an unusable body model=%s", self.model)
            raise AIUpstreamError() from exc
        usage = payload.get("usage") or {}
        return CompletionResult(
            text=text,
            provider=self.name,
            model=str(payload.get("model") or self.model),
            prompt_tokens=_int_or_none(usage.get("prompt_tokens")),
            completion_tokens=_int_or_none(usage.get("completion_tokens")),
            latency_ms=latency_ms,
            finish_reason=choice.get("finish_reason"),
        )

    async def _send(self, url: str, body: dict[str, Any]) -> httpx.Response:
        """One retry on 5xx / timeout / network error; **none** on 4xx."""
        client = self._client or httpx.AsyncClient()
        owns_client = self._client is None
        try:
            last_exc: Exception | None = None
            for attempt in (1, 2):
                try:
                    response = await client.post(
                        url, json=body, headers=self._headers(), timeout=self._timeout
                    )
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    last_exc = exc
                    logger.warning("ai upstream %s (attempt %d)", type(exc).__name__, attempt)
                    continue
                if response.status_code in _RETRYABLE_STATUS and attempt == 1:
                    logger.warning(
                        "ai upstream status=%s (attempt %d)", response.status_code, attempt
                    )
                    continue
                return response
            raise AIUpstreamError() from last_exc
        finally:
            if owns_client:
                await client.aclose()


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) else None
