"""Lifecycle errors — codes only (D-069)."""

from __future__ import annotations


class LifecycleError(Exception):
    error = "lifecycle_error"
    status = 400

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.error)
        self.message = message or self.error


class ExportTooLargeError(LifecycleError):
    error = "export_too_large"
    status = 413


class ExportNotFoundError(LifecycleError):
    error = "export_not_found"
    status = 404


class ConfirmationMismatchError(LifecycleError):
    error = "confirmation_mismatch"
    status = 422


class ExportFailedError(LifecycleError):
    """SPRINT-13.1 F-4: any unexpected export error — never a raw ``RuntimeError`` (500)."""

    error = "export_failed"
    status = 500


__all__ = [
    "ConfirmationMismatchError",
    "ExportFailedError",
    "ExportNotFoundError",
    "ExportTooLargeError",
    "LifecycleError",
]
