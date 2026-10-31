"""Typed errors for the WhatsApp Cloud API client and service (SPRINT-09 B-1).

Never carries Meta's human-readable message into API responses — only ``code`` /
``http_status`` / ``fbtrace_id`` (§2-0 ج: no provider text leaks).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class WhatsAppClientError(Exception):
    """Base error for any failure while talking to the Cloud API."""

    message: str
    code: int | None = None
    subcode: int | None = None
    http_status: int | None = None
    fbtrace_id: str | None = None

    def __str__(self) -> str:
        parts = [self.message]
        if self.code is not None:
            parts.append(f"code={self.code}")
        if self.http_status is not None:
            parts.append(f"http={self.http_status}")
        return " | ".join(parts)


class WhatsAppTransientError(WhatsAppClientError):
    """The request provably never reached Meta (connect failure) — a new attempt is safe."""


class WhatsAppAmbiguousError(WhatsAppClientError):
    """D-070: a side-effecting POST may have been processed (read/write timeout, 5xx).
    The caller records ``delivery_status='ambiguous'`` and never retries automatically."""


@dataclass(slots=True)
class WhatsAppRateLimitedError(WhatsAppClientError):
    """Codes 130429 / 80007 or HTTP 429 — never retried by the client."""

    retry_after_seconds: float | None = None


class WhatsAppRecipientError(WhatsAppClientError):
    """Codes 131026 (not a WhatsApp user / blocked), 131047 (re-engagement — 24h window
    closed), 131051 (unsupported message type)."""


class WhatsAppAuthError(WhatsAppClientError):
    """Codes 190 (token expired/invalid) and 10 (permission) ⇒ account disabled +
    ``required_action=reauth``."""


class WhatsAppPermanentError(WhatsAppClientError):
    """Any other 4xx / Graph error — not retried."""


# ------------------------------------------------------------- service-level -----
class WhatsAppError(Exception):
    """Base class of service errors mapped to HTTP by the router."""

    error: str = "whatsapp_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.error)


class AccountNotFoundError(WhatsAppError):
    error = "whatsapp_account_not_found"


class AccountAlreadyExistsError(WhatsAppError):
    error = "whatsapp_account_exists"


class CredentialsInvalidError(WhatsAppError):
    error = "whatsapp_credentials_invalid"


class ConversationNotFoundError(WhatsAppError):
    error = "whatsapp_conversation_not_found"


class WindowClosedError(WhatsAppError):
    error = "whatsapp_window_closed"


class ReauthRequiredError(WhatsAppError):
    error = "whatsapp_reauth_required"


class RecipientRejectedError(WhatsAppError):
    error = "whatsapp_recipient_rejected"

    def __init__(self, code: int | None) -> None:
        super().__init__()
        self.code = code


class TransientSendError(WhatsAppError):
    error = "whatsapp_transient"


class RateLimitedSendError(WhatsAppError):
    error = "whatsapp_rate_limited"

    def __init__(self, retry_after_seconds: float | None) -> None:
        super().__init__()
        self.retry_after_seconds = retry_after_seconds


class PermanentSendError(WhatsAppError):
    error = "whatsapp_send_failed"

    def __init__(self, code: int | None) -> None:
        super().__init__()
        self.code = code
