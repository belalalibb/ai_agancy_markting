"""Provider contract of the AI gateway (SPRINT-07 B-0 — D-067 / D-068).

Every facade behind :class:`~src.modules.ai_gateway.gateway.AIGateway` must translate its
provider's failures into exactly one of the **twelve** :class:`ErrorCategory` values below
and raise :class:`AIError`. Consumers never see provider SDK exceptions, status codes or
message text — only the category, the retry hint and a *sanitised* ``provider_code``.

The list is frozen by ``test_contract_freeze`` (B0-U2): changing it is a deliberate
architecture decision (D-067), not a refactor.

Operations reserved by D-067 (names only — **not implemented** in SPRINT-07)::

    generate_text        # the only operation today
    # generate_structured, summarize, classify, embed, moderate, transcribe, generate_image
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

Operation = Literal["generate_text"]
CredentialMode = Literal["platform", "user_key"]


class ErrorCategory(StrEnum):
    """The twelve provider-independent failure categories (D-067)."""

    AUTH_EXPIRED = "auth_expired"
    INVALID_CREDENTIAL = "invalid_credential"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    MODEL_UNAVAILABLE = "model_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    BAD_REQUEST = "bad_request"
    CONTENT_REJECTED = "content_rejected"
    TIMEOUT = "timeout"
    RETRYABLE_SERVER_ERROR = "retryable_server_error"
    NON_RETRYABLE_ERROR = "non_retryable_error"


RETRYABLE_DEFAULT: Final[dict[ErrorCategory, bool]] = {
    ErrorCategory.AUTH_EXPIRED: True,
    ErrorCategory.INVALID_CREDENTIAL: False,
    ErrorCategory.RATE_LIMITED: True,
    ErrorCategory.QUOTA_EXCEEDED: False,
    ErrorCategory.MODEL_UNAVAILABLE: False,
    ErrorCategory.PROVIDER_UNAVAILABLE: False,
    ErrorCategory.UNSUPPORTED_CAPABILITY: False,
    ErrorCategory.BAD_REQUEST: False,
    ErrorCategory.CONTENT_REJECTED: False,
    ErrorCategory.TIMEOUT: True,
    ErrorCategory.RETRYABLE_SERVER_ERROR: True,
    ErrorCategory.NON_RETRYABLE_ERROR: False,
}
assert set(RETRYABLE_DEFAULT) == set(ErrorCategory)  # noqa: S101 - table integrity

_PROVIDER_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


def sanitize_provider_code(raw: object) -> str | None:
    """Reduce a provider's error code to ``^[a-z0-9_]{1,64}$`` or drop it.

    Only short identifier-like tokens survive (``insufficient_quota``, ``429``,
    ``model_not_found``). Anything with spaces, punctuation or length > 64 — i.e. anything
    that could be a message, a prompt echo or a key — becomes ``None`` (B0-U4).
    """
    if raw is None or isinstance(raw, bool):
        return None
    text = str(raw).strip().lower().replace("-", "_").replace(".", "_")
    return text if _PROVIDER_CODE.fullmatch(text) else None


class AIError(Exception):
    """The one exception type a facade may raise (besides programming errors)."""

    def __init__(
        self,
        category: ErrorCategory,
        *,
        retryable: bool | None = None,
        retry_after_ms: int | None = None,
        provider_code: object = None,
    ) -> None:
        self.category = category
        self.retryable = RETRYABLE_DEFAULT[category] if retryable is None else retryable
        self.retry_after_ms = retry_after_ms
        self.provider_code = sanitize_provider_code(provider_code)
        super().__init__(f"ai error: {category.value}")

    # The public ``error`` code stays stable for the HTTP layer.
    @property
    def error(self) -> str:
        return f"ai_{self.category.value}"


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
