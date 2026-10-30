"""Deterministic facade used by the test-suite and whenever ``AI_PROVIDER=fake`` (default)."""

from __future__ import annotations

import hashlib
import time

from src.modules.ai_gateway.contract import Usage
from src.modules.ai_gateway.gateway import CompletionRequest, CompletionResult


class FakeProvider:
    """Same request ⇒ same text. The text echoes a digest of the prompt so tests can prove
    provenance (``prompt_hash``) without any network."""

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
            usage=Usage(
                prompt_tokens=len(req.system.split()) + len(req.user.split()),
                completion_tokens=len(text.split()),
            ),
            credential_mode=req.credential_mode,
            latency_ms=int((time.perf_counter() - started) * 1000),
            finish_reason="stop",
        )
