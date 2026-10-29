"""Domain exceptions for the Meta Graph API integration.

Mapping of Graph API error payloads (``{"error": {...}}``) to typed exceptions so
callers never have to inspect raw HTTP responses.

Reference: https://developers.facebook.com/docs/graph-api/guides/error-handling
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MetaGraphError(Exception):
    """Base error for any failure while talking to the Meta Graph API."""

    message: str
    code: int | None = None
    subcode: int | None = None
    error_type: str | None = None
    http_status: int | None = None
    fbtrace_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        parts = [self.message]
        if self.error_type:
            parts.append(f"type={self.error_type}")
        if self.code is not None:
            parts.append(f"code={self.code}")
        if self.subcode is not None:
            parts.append(f"subcode={self.subcode}")
        if self.http_status is not None:
            parts.append(f"http={self.http_status}")
        return " | ".join(parts)


class MetaOAuthError(MetaGraphError):
    """``OAuthException``: invalid / expired token, revoked permission, bad code."""


class MetaTokenExpiredError(MetaOAuthError):
    """Token expired (code 190, subcode 463/464) or session invalidated (460)."""


class MetaTokenInvalidError(MetaOAuthError):
    """``/debug_token`` reported ``is_valid == false`` for a freshly issued token."""


class MetaTokenForeignAppError(MetaOAuthError):
    """Token belongs to another app (``app_id`` mismatch) or ``user_id`` ≠ ``/me``."""


class MetaPermissionError(MetaGraphError):
    """Missing permission (code 10 or 200-299)."""


class MetaRateLimitError(MetaGraphError):
    """Application / user / page-level throttling (codes 4, 17, 32, 613; HTTP 429)."""

    retry_after_seconds: float | None = None


class MetaTransientError(MetaGraphError):
    """Temporary upstream failure (codes 1, 2; HTTP 5xx) — safe to retry."""


class MetaNetworkError(MetaGraphError):
    """Connection / timeout failure before an HTTP response was received."""


# --- Error-code tables (Graph API documented codes) -------------------------
_TOKEN_EXPIRED_SUBCODES: frozenset[int] = frozenset({458, 459, 460, 463, 464, 467})
_RATE_LIMIT_CODES: frozenset[int] = frozenset({4, 17, 32, 613, 80001, 80002, 80004, 80005})
_TRANSIENT_CODES: frozenset[int] = frozenset({1, 2})
_PERMISSION_CODES: frozenset[int] = frozenset({10, *range(200, 300)})


def map_graph_error(
    payload: dict[str, Any] | None,
    http_status: int,
    retry_after_seconds: float | None = None,
) -> MetaGraphError:
    """Translate a Graph API error response into the most specific exception."""
    err = (payload or {}).get("error") or {}
    message = str(err.get("message") or f"Meta Graph API request failed (HTTP {http_status})")
    code = _as_int(err.get("code"))
    subcode = _as_int(err.get("error_subcode"))
    error_type = err.get("type")
    common: dict[str, Any] = {
        "code": code,
        "subcode": subcode,
        "error_type": error_type,
        "http_status": http_status,
        "fbtrace_id": err.get("fbtrace_id"),
        "raw": payload or {},
    }

    if http_status == 429 or code in _RATE_LIMIT_CODES:
        exc = MetaRateLimitError(message, **common)
        exc.retry_after_seconds = retry_after_seconds
        return exc

    # Permission codes win over the generic ``OAuthException`` type: Meta reports a
    # *missing permission* (code 10 / 200-299) with ``type == "OAuthException"`` too,
    # but the remedy is re-authorising with extra scopes (403), not re-login (401).
    if code in _PERMISSION_CODES:
        return MetaPermissionError(message, **common)

    if error_type == "OAuthException" or code == 190 or http_status == 401:
        if code == 190 or subcode in _TOKEN_EXPIRED_SUBCODES:
            return MetaTokenExpiredError(message, **common)
        return MetaOAuthError(message, **common)

    if http_status == 403:
        return MetaPermissionError(message, **common)

    if code in _TRANSIENT_CODES or http_status >= 500:
        return MetaTransientError(message, **common)

    return MetaGraphError(message, **common)


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
