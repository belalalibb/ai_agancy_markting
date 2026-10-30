"""Domain exceptions of ``content`` — each carries a fixed snake_case ``error`` code (B6)."""

from __future__ import annotations

from src.modules.content.domain.models import Channel, ContentStatus


class ContentError(Exception):
    error: str = "content_error"
    http_status: int = 400

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.__class__.__doc__ or self.error
        super().__init__(self.message)


class ContentNotFoundError(ContentError):
    """Content item not found in this brand."""

    error = "content_not_found"
    http_status = 404


class InvalidTransitionError(ContentError):
    """The requested action is not allowed from the item's current status."""

    error = "invalid_transition"
    http_status = 409

    def __init__(
        self, from_status: ContentStatus, action: str, message: str | None = None
    ) -> None:
        self.from_status = from_status
        self.action = action
        super().__init__(message or f"cannot {action} from {from_status.value!r}")


class ApprovalRequiresOwnerError(ContentError):
    """Only the brand owner may approve or reject content (D-055)."""

    error = "approval_requires_owner"
    http_status = 403


class ContentInsufficientRoleError(ContentError):
    """The actor's role may not perform this action."""

    error = "insufficient_role"
    http_status = 403


class RejectNoteRequiredError(ContentError):
    """A rejection needs a note of at least 3 characters."""

    error = "reject_note_required"
    http_status = 422


class ScheduleTooSoonError(ContentError):
    """scheduled_for must be more than 5 minutes in the future."""

    error = "schedule_too_soon"
    http_status = 422


class TargetPageNotSelectedError(ContentError):
    """target_page_id is not a selected Page (with token) of an active connection of this brand."""

    error = "target_page_not_selected"
    http_status = 422


class InstagramNotLinkedError(ContentError):
    """The target Page has no linked Instagram account."""

    error = "instagram_not_linked"
    http_status = 422


class BodyTooLongError(ContentError):
    """The body exceeds the hard character limit of the channel (SPRINT-07 A-2)."""

    error = "body_too_long"
    http_status = 422

    def __init__(self, channel: Channel, limit: int, length: int) -> None:
        self.channel = channel
        self.limit = limit
        self.length = length
        super().__init__(f"body of {length} chars exceeds the {channel.value} limit of {limit}")


class GenerationBudgetExceededError(ContentError):
    """The brand's daily AI token budget is exhausted (SPRINT-07 A-5)."""

    error = "generation_budget_exceeded"
    http_status = 429

    def __init__(self, budget: int, used: int, resets_in_seconds: int) -> None:
        self.budget = budget
        self.used = used
        self.resets_in_seconds = resets_in_seconds
        super().__init__()


class GenerationRateLimitedError(ContentError):
    """Too many AI generations for this brand — try again later."""

    error = "generation_rate_limited"
    http_status = 429

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__()
