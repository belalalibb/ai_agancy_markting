"""Literal error codes of ``ops`` (SPRINT-13 §5-5) — no new ``ErrorCategory``."""

from __future__ import annotations


class OpsError(Exception):
    error = "ops_error"
    status = 400

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.error.replace("_", " ")
        super().__init__(self.message)


class BrandSuspendedError(OpsError):
    error = "brand_suspended"
    status = 403


class PlatformReadonlyError(OpsError):
    error = "platform_readonly"
    status = 503


class JobNotFailedError(OpsError):
    error = "job_not_failed"
    status = 409


class JobNotFoundError(OpsError):
    error = "publish_job_not_found"
    status = 404


class BrandNotFoundError(OpsError):
    error = "brand_not_found"
    status = 404


class FlagUnknownError(OpsError):
    error = "flag_unknown"
    status = 404


class ManualVerificationRequiredError(OpsError):
    """D-070: ``ambiguous_outcome`` is never requeued blindly (SPRINT-13.1 F-1)."""

    error = "manual_verification_required"
    status = 409


class PublishInProgressError(OpsError):
    """Another active job (queued/leased) already owns the item (SPRINT-13.1 F-2)."""

    error = "publish_in_progress"
    status = 409


class ItemNotPublishFailedError(OpsError):
    """The item is no longer ``publish_failed`` (archived/edited) (SPRINT-13.1 F-3)."""

    error = "item_not_publish_failed"
    status = 409
