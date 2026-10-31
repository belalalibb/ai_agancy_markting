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


class MetaAmbiguousError(MetaGraphError):
    """SPRINT-09 A-2 / D-070: a **side-effecting** request (``POST``) failed *after* it may
    have reached Meta — read/write timeout, broken connection mid-response, HTTP 5xx or
    Graph codes 1/2. The post may or may not be live, so the client never retries and the
    caller must record the attempt as ``ambiguous`` (never auto-retry).

    Base class is :class:`MetaGraphError` (the directive names it ``MetaClientError``; the
    codebase's root exception is ``MetaGraphError`` — see SPRINT-09 §6 deviations).
    """


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


class UnknownPageIdsError(ValueError):
    """B6 selection: requested ``page_ids`` not among the Pages Meta returns for the user.

    Mapped to 422 ``{"error": "unknown_page_ids", "page_ids": [...]}``; nothing is written.
    """

    def __init__(self, page_ids: list[str]) -> None:
        super().__init__(f"unknown page ids: {', '.join(page_ids)}")
        self.page_ids = list(page_ids)


class NotConnectionOwnerError(PermissionError):
    """SPRINT-04.1 H1: only the tenant that performed the OAuth flow may change the
    selection of (or, unless brand OWNER, disconnect) a connection.

    Mapped to **403** ``{"error": "not_connection_owner"}`` — the connection is visible to the
    caller, so hiding it (404) would be wrong.
    """

    error = "not_connection_owner"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or "only the tenant that connected this account may do this"
        super().__init__(self.message)


class PageNotSelectableError(ValueError):
    """SPRINT-04.1 H3: a requested Page exists but Meta returned no ``access_token`` for it
    (the user has no role on the Page). Mapped to **422** ``page_not_selectable``; no change.
    """

    def __init__(self, page_ids: list[str]) -> None:
        super().__init__(f"pages without a page token: {', '.join(page_ids)}")
        self.page_ids = list(page_ids)


# --- SPRINT-05 operational reads (B2/B3/B4) ----------------------------------
class PageNotSelectedError(LookupError):
    """SPRINT-05 B3/B4: the Page is not a *selected* Page (with a stored token) of this
    connection — or does not belong to it at all. Mapped to **404** ``page_not_selected`` for
    *every* role (even the connection owner): unselected Pages are not brand resources.
    """

    error = "page_not_selected"

    def __init__(self, page_id: str) -> None:
        self.page_id = page_id
        self.message = "this Page is not selected for the brand"
        super().__init__(self.message)


class InstagramNotLinkedError(LookupError):
    """SPRINT-05 B4: the selected Page has no linked Instagram Business account.
    Mapped to **404** ``instagram_not_linked``."""

    error = "instagram_not_linked"

    def __init__(self, page_id: str) -> None:
        self.page_id = page_id
        self.message = "this Page has no linked Instagram Business account"
        super().__init__(self.message)


class ConnectionRevokedError(RuntimeError):
    """SPRINT-05 B2/B3/B4: the connection is ``revoked`` — nothing may be read or resynced
    through it; reconnect instead. Mapped to **409** ``connection_revoked``."""

    error = "connection_revoked"

    def __init__(self) -> None:
        self.message = "this connection was revoked; reconnect the Meta account"
        super().__init__(self.message)


class ReauthRequiredError(RuntimeError):
    """SPRINT-05 B3/B4: the connection is already ``reauth_required`` — no Graph call is made.
    Mapped to **409** ``reauth_required`` with ``required_action: reconnect``."""

    error = "reauth_required"

    def __init__(self) -> None:
        self.message = "the Meta token needs re-authorisation; reconnect the account"
        super().__init__(self.message)
