"""Domain exceptions for auth (A11). Mapped to HTTP in ``router.py`` / ``dependencies.py``."""

from __future__ import annotations


class AuthError(Exception):
    """Base class; ``error`` is the machine-readable code used in HTTP bodies."""

    error: str = "auth_error"
    status_code: int = 400

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.error)
        self.message = message or self.error


class EmailTakenError(AuthError):
    error = "email_taken"
    status_code = 409


class InvalidCredentialsError(AuthError):
    """Unified for unknown email *and* wrong password (no account enumeration)."""

    error = "invalid_credentials"
    status_code = 401


class UserInactiveError(AuthError):
    error = "user_inactive"
    status_code = 403


class RefreshMissingError(AuthError):
    error = "refresh_missing"
    status_code = 401


class RefreshInvalidError(AuthError):
    error = "refresh_invalid"
    status_code = 401


class RefreshExpiredError(AuthError):
    error = "refresh_expired"
    status_code = 401


class RefreshReuseDetectedError(AuthError):
    """A revoked refresh token was presented ⇒ theft suspected ⇒ every session revoked."""

    error = "refresh_reuse_detected"
    status_code = 401


class SessionRevokedError(AuthError):
    error = "session_revoked"
    status_code = 401


class RateLimitedError(AuthError):
    error = "rate_limited"
    status_code = 429

    def __init__(self, retry_after: int, message: str | None = None) -> None:
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))


class CsrfRejectedError(AuthError):
    error = "csrf_rejected"
    status_code = 403
