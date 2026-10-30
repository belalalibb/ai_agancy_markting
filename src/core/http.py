"""Shared outbound HTTP client (SPRINT-07 B-0, D-068 No-Bypass).

This is the **only** place outside ``src/modules/ai_gateway/`` and
``src/modules/social_connect/meta_client.py`` allowed to import ``httpx`` (ruff
``banned-api`` + ``test_no_bypass_imports``). The app creates one pooled
``httpx.AsyncClient`` for its lifetime (``lifespan``) and every outbound integration receives
it through :func:`get_http_client` — no module opens ad-hoc clients or talks to a model /
Graph endpoint directly.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import Depends, Request

from src.core.config import Settings

USER_AGENT = "ai-growth-os/0.1"


def build_http_client(settings: Settings) -> httpx.AsyncClient:
    """Pooled client with the Meta timeout as the default (per-call timeouts may override)."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.meta_http_timeout_seconds),
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )


def get_http_client(request: Request) -> httpx.AsyncClient:
    """FastAPI dependency: the app-wide client created in ``lifespan``."""
    return request.app.state.http_client


HttpClient = Annotated[httpx.AsyncClient, Depends(get_http_client)]
