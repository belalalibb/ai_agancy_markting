"""Gateway contract (SPRINT-06 B4; SPRINT-07 B-0 adds ``Usage`` + ``credential_mode``)."""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.modules.ai_gateway.contract import CredentialMode, Operation, Usage

Purpose = Literal["content_draft"]


class CompletionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    purpose: Purpose
    system: str = Field(min_length=1)
    user: str = Field(min_length=1)
    max_tokens: int = Field(default=800, ge=1, le=2000)
    temperature: float = Field(default=0.7, ge=0.0, le=1.0)
    brand_id: UUID
    tenant_id: UUID
    operation: Operation = "generate_text"
    # B-0 (D-067): ``platform`` = the platform's own key (today's only mode); ``user_key`` is
    # reserved for BYOK (M7) and never selected in SPRINT-07.
    credential_mode: CredentialMode = "platform"


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
