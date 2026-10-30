"""Gateway contract (SPRINT-06 B4)."""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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


class CompletionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int
    finish_reason: str | None = None


@runtime_checkable
class AIGateway(Protocol):
    async def complete(self, req: CompletionRequest) -> CompletionResult: ...
