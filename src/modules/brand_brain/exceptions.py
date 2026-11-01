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


# ------------------------------------------------------------------- B-3 --
class BrainRateLimitedError(BrandBrainError):
    """``POST /brain/diagnose`` / ``/content-plan`` — 5/hour/brand."""

    error = "brain_rate_limited"

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("brain rate limited")


class BrainBudgetExceededError(BrandBrainError):
    """The shared daily AI token budget (``DailyBudget``) is exhausted ⇒ 429."""

    error = "generation_budget_exceeded"

    def __init__(self, budget: int, used: int, resets_in_seconds: int) -> None:
        self.budget = budget
        self.used = used
        self.resets_in_seconds = resets_in_seconds
        super().__init__("daily token budget exceeded")


class InvalidModelOutputError(BrandBrainError):
    """The model answered with JSON that does not match the template schema ⇒ 502."""

    error = "ai_invalid_output"


class RecommendationNotFoundError(BrandBrainError):
    error = "recommendation_not_found"


class ProfileConflictError(BrandBrainError):
    """A-6 (B4): two concurrent ``accept`` calls for a ``reply_template`` on a brand without a
    profile both tried the first ``INSERT`` — the loser gets 409 instead of 500
    («الملف أُنشئ للتو — أعد المحاولة»)."""

    error = "profile_conflict"


class RecommendationAlreadyDecidedError(BrandBrainError):
    """``accept`` / ``dismiss`` on a row that is no longer ``proposed`` ⇒ 409."""

    error = "recommendation_already_decided"

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"recommendation is {status}")
