"""FastAPI wiring for the gateway (pattern of ``get_encryption_service``: misconfiguration
⇒ 503, never a stack trace)."""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request, status

from src.core.config import Settings, get_settings
from src.modules.ai_gateway.exceptions import AIProviderNotConfiguredError
from src.modules.ai_gateway.gateway import AIGateway
from src.modules.ai_gateway.providers import FakeProvider, HttpOpenAICompatibleProvider

_FAKE = FakeProvider()


def build_gateway(settings: Settings, http_client: httpx.AsyncClient | None = None) -> AIGateway:
    """Pure factory (no FastAPI): raises :class:`AIProviderNotConfiguredError` when
    ``openai_compatible`` is selected without a key."""
    if settings.ai_provider == "fake":
        return _FAKE
    key = settings.ai_provider_api_key
    if key is None or not key.get_secret_value():
        raise AIProviderNotConfiguredError()
    return HttpOpenAICompatibleProvider(
        base_url=settings.ai_provider_base_url,
        api_key=key,
        model=settings.ai_default_model,
        http_client=http_client,
        timeout_seconds=settings.ai_http_timeout_seconds,
    )


def get_ai_gateway(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> AIGateway:
    try:
        return build_gateway(settings, getattr(request.app.state, "http_client", None))
    except AIProviderNotConfiguredError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": exc.error, "message": "AI provider is not configured on the server."},
        ) from exc


Gateway = Annotated[AIGateway, Depends(get_ai_gateway)]
