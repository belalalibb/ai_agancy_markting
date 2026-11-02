"""Gateway contract (SPRINT-06 B4; SPRINT-07 B-0 adds ``Usage`` + ``credential_mode``;
SPRINT-10 B-2 adds the four Brand Brain purposes, ``json_mode`` and ``prompt_version``)."""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.modules.ai_gateway.contract import CredentialMode, Operation, Usage

# SPRINT-09 B-3 adds ``reply_suggestion``; SPRINT-10 B-2 adds the four Brand Brain purposes
# (directive §2-0: a Literal, not a new layer). SPRINT-11.1 C-3 adds ``admin_probe`` — the
# only purpose that may travel without a brand (``brand_id=None``; platform-paid, capped).
Purpose = Literal[
    "content_draft",
    "reply_suggestion",
    "brand_diagnosis",
    "competitor_gap",
    "content_plan",
    "tone_profile",
    "admin_probe",
]


class CompletionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    purpose: Purpose
    system: str = Field(min_length=1)
    user: str = Field(min_length=1)
    max_tokens: int = Field(default=800, ge=1, le=2000)
    temperature: float = Field(default=0.7, ge=0.0, le=1.0)
    # SPRINT-11.1 (§5-5 note 3): ``None`` only for ``purpose="admin_probe"`` — the BudgetGuard
    # then skips the brand-level checks and writes ``ai_cost_log.brand_id = NULL``.
    brand_id: UUID | None = None
    tenant_id: UUID
    operation: Operation = "generate_text"

    @model_validator(mode="after")
    def _brand_required_unless_probe(self) -> CompletionRequest:
        if self.brand_id is None and self.purpose != "admin_probe":
            raise ValueError("brand_id is required unless purpose is 'admin_probe'")
        return self

    # B-0 (D-067): ``platform`` = the platform's own key (today's only mode); ``user_key`` is
    # reserved for BYOK (M7) and never selected in SPRINT-07. SPRINT-10 B-2: ``user_key`` is
    # selected by the ``X-AI-Key`` header on Brand Brain routes (memory only, no fallback).
    credential_mode: CredentialMode = "platform"
    # SPRINT-10 B-2: ``json_mode`` ⇒ ``response_format={"type": "json_object"}`` on the
    # OpenAI-compatible facade and a strict JSON parse of the completion; ``prompt_version``
    # is metadata for ``ai_cost_log`` (never the prompt text).
    json_mode: bool = False
    prompt_version: str = Field(default="", max_length=32)


class CompletionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    provider: str
    model: str
    usage: Usage = Field(default_factory=Usage)
    credential_mode: CredentialMode = "platform"
    latency_ms: int
    finish_reason: str | None = None

    # Backwards-compatible accessors (SPRINT-06 call sites).
    @property
    def prompt_tokens(self) -> int | None:
        return self.usage.prompt_tokens

    @property
    def completion_tokens(self) -> int | None:
        return self.usage.completion_tokens


@runtime_checkable
class AIGateway(Protocol):
    async def complete(self, req: CompletionRequest) -> CompletionResult: ...
