"""Brand Brain service errors — mapped to HTTP in ``router.to_http`` (codes only, no text
from providers or fetched pages)."""

from __future__ import annotations


class BrandBrainError(Exception):
    error = "brand_brain_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.error)


class ProfileVersionConflictError(BrandBrainError):
    """``If-Match`` does not equal the stored ``version`` (412)."""

    error = "profile_version_conflict"

    def __init__(self, current_version: int) -> None:
        self.current_version = current_version
        super().__init__(f"profile is at version {current_version}")


class InvalidIfMatchError(BrandBrainError):
    error = "if_match_invalid"


class CompetitorLimitError(BrandBrainError):
    error = "competitor_limit_reached"


class CompetitorExistsError(BrandBrainError):
    error = "competitor_exists"


class CompetitorNotFoundError(BrandBrainError):
    error = "competitor_not_found"


class InvalidHostError(BrandBrainError):
    error = "host_invalid"


class BlockedPlatformHostError(BrandBrainError):
    """D-074 (ج): closed social platforms are never a public source."""

    error = "host_blocked_platform"


class UnknownMetricError(BrandBrainError):
    error = "metric_unknown"


class RefreshRateLimitedError(BrandBrainError):
    error = "refresh_rate_limited"

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("refresh rate limited")
