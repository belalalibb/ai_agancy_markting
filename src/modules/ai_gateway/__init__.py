"""``ai_gateway`` — the single interface every module uses to call a language model (D-056).

No SDK dependency: a ``Protocol``, a deterministic ``FakeProvider`` and an OpenAI-compatible
HTTP provider over the shared ``httpx.AsyncClient``. Provider keys travel only in the
``Authorization`` header and are never logged (SPRINT-06 §2-0 a).
"""

from src.modules.ai_gateway.contract import AIError, ErrorCategory, Usage
from src.modules.ai_gateway.gateway import (
    AIGateway,
    CompletionRequest,
    CompletionResult,
)

__all__ = [
    "AIError",
    "AIGateway",
    "CompletionRequest",
    "CompletionResult",
    "ErrorCategory",
    "Usage",
]
