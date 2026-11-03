"""One HTTP mapping for every :class:`BudgetGuardError` (SPRINT-11 B-3) — used by the
``content``, ``whatsapp`` and ``brand_brain`` routers so clients see the same codes:

* ``BrandBudgetExceededError`` / ``EntitlementLimitError`` ⇒ **429**
  ``generation_budget_exceeded`` (the existing name) + ``limit_kind`` + ``Retry-After``;
* ``EntitlementLimitError(plan_inactive)`` ⇒ **402** ``entitlement_plan_inactive``
  ``{plan, active_until}`` — SPRINT-12 A-1 (H1): *pay*, not *wait*, so no ``Retry-After``;
* ``PlatformBudgetExhaustedError`` ⇒ **503** ``platform_budget_exhausted`` + ``Retry-After``;
* ``UnpricedModelError`` ⇒ **503** ``ai_model_unpriced``;
* ``CostLogUnavailableError`` ⇒ **503** ``cost_log_unavailable``.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from src.modules.ai_gateway.budget_guard import (
    BrandBudgetExceededError,
    BudgetGuardError,
    EntitlementLimitError,
    PlatformBudgetExhaustedError,
)


def plan_inactive_http(*, plan: str | None, active_until: str | None) -> HTTPException:
    """SPRINT-12 A-1 (H1): the one 402 ``entitlement_plan_inactive`` body shared by the AI
    path (``BudgetGuard``) and the counter ports (``content`` / ``whatsapp``)."""
    return HTTPException(
        status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "error": "entitlement_plan_inactive",
            "message": "the brand's plan is suspended or expired — renew it to continue",
            "plan": plan,
            "active_until": active_until,
        },
    )


def budget_error_to_http(exc: BudgetGuardError) -> HTTPException:
    detail: dict[str, object] = {"error": exc.error, "message": exc.message}
    headers: dict[str, str] | None = None
    if isinstance(exc, BrandBudgetExceededError):
        code = status.HTTP_429_TOO_MANY_REQUESTS
        detail.update(
            limit_kind=exc.limit_kind,
            budget=exc.budget,
            used=exc.used,
            resets_in_seconds=exc.resets_in_seconds,
        )
        headers = {"Retry-After": str(exc.resets_in_seconds)}
    elif isinstance(exc, EntitlementLimitError) and exc.is_plan_inactive:
        return plan_inactive_http(plan=exc.plan, active_until=exc.active_until)
    elif isinstance(exc, EntitlementLimitError):
        code = status.HTTP_429_TOO_MANY_REQUESTS
        detail.update(limit_kind=exc.limit_kind, resets_in_seconds=exc.resets_in_seconds)
        if exc.budget is not None:
            detail["budget"] = exc.budget
        if exc.used is not None:
            detail["used"] = exc.used
        headers = {"Retry-After": str(exc.resets_in_seconds)}
    elif isinstance(exc, PlatformBudgetExhaustedError):
        code = status.HTTP_503_SERVICE_UNAVAILABLE
        detail["resets_in_seconds"] = exc.resets_in_seconds
        headers = {"Retry-After": str(exc.resets_in_seconds)}
    else:  # UnpricedModelError / CostLogUnavailableError / future guard errors
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HTTPException(code, detail=detail, headers=headers)


__all__ = ["budget_error_to_http", "plan_inactive_http"]
