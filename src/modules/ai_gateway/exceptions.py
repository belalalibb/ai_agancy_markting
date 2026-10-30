"""Gateway wiring failures — fixed snake_case ``error`` codes.

Provider-side failures are **not** here: every facade raises
:class:`src.modules.ai_gateway.contract.AIError` with one of the twelve D-067 categories.
"""

from __future__ import annotations


class AIGatewayError(Exception):
    error: str = "ai_gateway_error"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.__class__.__doc__ or self.error
        super().__init__(self.message)


class AIProviderNotConfiguredError(AIGatewayError):
    """AI provider is not configured on the server (missing API key)."""

    error = "ai_provider_not_configured"
