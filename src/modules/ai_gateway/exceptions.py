"""Gateway failures — fixed snake_case ``error`` codes (B6)."""

from __future__ import annotations


class AIGatewayError(Exception):
    error: str = "ai_gateway_error"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.__class__.__doc__ or self.error
        super().__init__(self.message)


class AIProviderNotConfiguredError(AIGatewayError):
    """AI provider is not configured on the server (missing API key)."""

    error = "ai_provider_not_configured"


class AIRateLimitedError(AIGatewayError):
    """The AI provider rate-limited the request."""

    error = "ai_rate_limited"

    def __init__(self, retry_after_seconds: int | None = None) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__()


class AIUpstreamError(AIGatewayError):
    """The AI provider is unavailable or returned an unusable answer."""

    error = "ai_upstream_unavailable"
