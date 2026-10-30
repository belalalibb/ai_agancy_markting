"""OpenAI-compatible HTTP facade (SPRINT-06 B4 ⇒ SPRINT-07 B-0 contract).

``POST {base_url}/chat/completions``; Bearer key **only** in the ``Authorization`` header;
no retry on 4xx; one retry on a *retryable* category (5xx / timeout); prompts are never
logged at INFO (DEBUG only, truncated to 200 characters).

Classification (D-067, B0-U3)::

    401 / 403                         ⇒ invalid_credential
    429                               ⇒ rate_limited (+ retry_after_ms)
    404                               ⇒ model_unavailable
    400 / 413 / 422                   ⇒ bad_request  (content_rejected if error.code ∋ "content")
    5xx                               ⇒ retryable_server_error
    httpx timeout                     ⇒ timeout
    httpx network error               ⇒ provider_unavailable
    200 without choices / refusal /   ⇒ quota_exceeded  (lesson D-01 of general_ai_core —
        finish_reason ∈ quota words      some proxies answer 200 with "insufficient credit")
    200 with an unparsable body       ⇒ non_retryable_error
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from pydantic import SecretStr

from src.modules.ai_gateway.contract import AIError, ErrorCategory, Usage
from src.modules.ai_gateway.gateway import CompletionRequest, CompletionResult

logger = logging.getLogger(__name__)

PROMPT_LOG_CHARS = 200
# The facade's *own* immediate single retry applies to transport/server failures only.
# ``rate_limited`` / ``auth_expired`` are retryable **by the caller** (after ``Retry-After`` /
# after refreshing the credential) — retrying them in-flight would ignore the provider's hint.
_IMMEDIATE_RETRY = frozenset({ErrorCategory.TIMEOUT, ErrorCategory.RETRYABLE_SERVER_ERROR})
_QUOTA_WORDS = ("quota", "credit", "billing", "balance", "insufficient_funds")
_CONTENT_WORDS = ("content", "safety", "moderation", "policy")


def _preview(text: str) -> str:
    return text[:PROMPT_LOG_CHARS] + ("…" if len(text) > PROMPT_LOG_CHARS else "")


def _error_fields(payload: Any) -> tuple[str, str]:
    """``(code, message)`` lower-cased from ``{"error": {...}}`` — both may be empty."""
    if not isinstance(payload, dict):
        return "", ""
    err = payload.get("error")
    if isinstance(err, str):
        return "", err.lower()
    if not isinstance(err, dict):
        return "", ""
    code = err.get("code") or err.get("type") or ""
    message = err.get("message") or ""
    return str(code).lower(), str(message).lower()


def classify_response(response: httpx.Response) -> AIError | None:
    """Map a non-2xx answer to :class:`AIError`; ``None`` for 2xx."""
    status = response.status_code
    if status < 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        payload = None
    code, _message = _error_fields(payload)
    if status in (401, 403):
        return AIError(ErrorCategory.INVALID_CREDENTIAL, provider_code=code or status)
    if status == 429:
        retry_after = response.headers.get("Retry-After")
        return AIError(
            ErrorCategory.RATE_LIMITED,
            retry_after_ms=int(retry_after) * 1000
            if retry_after and retry_after.isdigit()
            else None,
            provider_code=code or status,
        )
    if status == 404:
        return AIError(ErrorCategory.MODEL_UNAVAILABLE, provider_code=code or status)
    if status in (400, 413, 422):
        if any(word in code for word in _CONTENT_WORDS):
            return AIError(ErrorCategory.CONTENT_REJECTED, provider_code=code)
        return AIError(ErrorCategory.BAD_REQUEST, provider_code=code or status)
    if status >= 500:
        return AIError(ErrorCategory.RETRYABLE_SERVER_ERROR, provider_code=code or status)
    return AIError(ErrorCategory.NON_RETRYABLE_ERROR, provider_code=code or status)


def classify_success_body(payload: Any) -> AIError | None:
    """A 200 that is not a usable completion (B0-U3 — the "polite refusal" shapes)."""
    if not isinstance(payload, dict):
        return AIError(ErrorCategory.NON_RETRYABLE_ERROR, provider_code="not_object")
    code, message = _error_fields(payload)
    choices = payload.get("choices")
    if not choices:
        if any(w in code or w in message for w in _QUOTA_WORDS):
            return AIError(ErrorCategory.QUOTA_EXCEEDED, provider_code=code or "quota")
        return AIError(ErrorCategory.QUOTA_EXCEEDED, provider_code="no_choices")
    choice = choices[0] if isinstance(choices, list) else None
    if not isinstance(choice, dict):
        return AIError(ErrorCategory.NON_RETRYABLE_ERROR, provider_code="bad_choice")
    finish = str(choice.get("finish_reason") or "").lower()
    if any(w in finish for w in _QUOTA_WORDS):
        return AIError(ErrorCategory.QUOTA_EXCEEDED, provider_code=finish)
    if finish == "content_filter":
        return AIError(ErrorCategory.CONTENT_REJECTED, provider_code=finish)
    msg = choice.get("message")
    refusal = msg.get("refusal") if isinstance(msg, dict) else None
    if isinstance(refusal, str) and refusal.strip():
        return AIError(ErrorCategory.CONTENT_REJECTED, provider_code="refusal")
    text = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(text, str) or not text.strip():
        return AIError(ErrorCategory.NON_RETRYABLE_ERROR, provider_code="empty_completion")
    return None


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
        failure = classify_response(response)
        if failure is not None:
            logger.warning(
                "ai upstream error status=%s category=%s model=%s",
                response.status_code,
                failure.category.value,
                self.model,
            )
            raise failure
        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning("ai upstream returned a non-JSON body model=%s", self.model)
            raise AIError(ErrorCategory.NON_RETRYABLE_ERROR, provider_code="not_json") from exc
        failure = classify_success_body(payload)
        if failure is not None:
            logger.warning(
                "ai upstream 200 without completion category=%s model=%s",
                failure.category.value,
                self.model,
            )
            raise failure
        choice = payload["choices"][0]
        usage = payload.get("usage") or {}
        return CompletionResult(
            text=choice["message"]["content"],
            provider=self.name,
            model=str(payload.get("model") or self.model),
            usage=Usage(
                prompt_tokens=_int_or_none(usage.get("prompt_tokens")),
                completion_tokens=_int_or_none(usage.get("completion_tokens")),
            ),
            credential_mode=req.credential_mode,
            latency_ms=latency_ms,
            finish_reason=choice.get("finish_reason"),
        )

    async def _send(self, url: str, body: dict[str, Any]) -> httpx.Response:
        """One in-flight retry **only** for ``timeout`` / ``retryable_server_error``; never
        on 4xx (incl. 429 — the caller honours ``Retry-After``)."""
        client = self._client or httpx.AsyncClient()
        owns_client = self._client is None
        try:
            last_error: AIError | None = None
            for attempt in (1, 2):
                try:
                    response = await client.post(
                        url, json=body, headers=self._headers(), timeout=self._timeout
                    )
                except httpx.TimeoutException as exc:
                    last_error = AIError(ErrorCategory.TIMEOUT, provider_code="timeout")
                    last_error.__cause__ = exc
                except httpx.NetworkError as exc:
                    last_error = AIError(
                        ErrorCategory.PROVIDER_UNAVAILABLE, provider_code="network_error"
                    )
                    last_error.__cause__ = exc
                else:
                    failure = classify_response(response)
                    if failure is None or failure.category not in _IMMEDIATE_RETRY:
                        return response
                    if attempt == 2:
                        return response
                    last_error = failure
                logger.warning("ai upstream %s (attempt %d)", last_error.category.value, attempt)
                if last_error.category not in _IMMEDIATE_RETRY:
                    break
            assert last_error is not None  # noqa: S101 - loop always sets it before exit
            raise last_error
        finally:
            if owns_client:
                await client.aclose()


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) and value >= 0 else None
