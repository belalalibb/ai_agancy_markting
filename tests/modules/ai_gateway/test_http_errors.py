"""SPRINT-11 B-3 — ``budget_error_to_http``: one HTTP mapping for every ``BudgetGuardError``
(429 ``generation_budget_exceeded`` + ``limit_kind`` / 503 ``platform_budget_exhausted`` /
503 ``ai_model_unpriced`` / 503 ``cost_log_unavailable``) with ``Retry-After`` where due."""

from __future__ import annotations

import pytest
from src.modules.ai_gateway.budget import BudgetVerdict
from src.modules.ai_gateway.budget_guard import (
    PLATFORM_CAP_RETRY_SECONDS,
    BrandBudgetExceededError,
    BudgetGuardError,
    CostLogUnavailableError,
    EntitlementLimitError,
    PlatformBudgetExhaustedError,
    UnpricedModelError,
)
from src.modules.ai_gateway.http_errors import budget_error_to_http

pytestmark = pytest.mark.unit


def test_brand_budget_is_429_with_limit_kind_and_retry_after() -> None:
    verdict = BudgetVerdict(allowed=False, budget=1_000, used=1_200, resets_in_seconds=77)
    http = budget_error_to_http(BrandBudgetExceededError(verdict))
    assert http.status_code == 429
    assert http.detail == {
        "error": "generation_budget_exceeded",
        "message": "daily token budget exceeded",
        "limit_kind": "brand_daily_tokens",
        "budget": 1_000,
        "used": 1_200,
        "resets_in_seconds": 77,
    }
    assert http.headers == {"Retry-After": "77"}


def test_entitlement_limit_is_429_with_plan_limit_kind() -> None:
    bare = budget_error_to_http(EntitlementLimitError("plan_daily_tokens"))
    assert bare.status_code == 429
    assert bare.detail["error"] == "generation_budget_exceeded"
    assert bare.detail["limit_kind"] == "plan_daily_tokens"
    assert "budget" not in bare.detail and "used" not in bare.detail
    assert bare.headers == {"Retry-After": str(PLATFORM_CAP_RETRY_SECONDS)}


def test_plan_inactive_is_402_entitlement_plan_inactive_without_retry_after() -> None:
    """SPRINT-12 A-1 (H1): *pay*, not *wait* — 402 with ``plan`` / ``active_until``."""
    exc = EntitlementLimitError("plan_inactive", plan="growth", active_until="2026-12-01T00:00:00")
    assert exc.is_plan_inactive is True
    http = budget_error_to_http(exc)
    assert http.status_code == 402
    assert http.detail == {
        "error": "entitlement_plan_inactive",
        "message": "the brand's plan is suspended or expired — renew it to continue",
        "plan": "growth",
        "active_until": "2026-12-01T00:00:00",
    }
    assert http.headers is None
    assert EntitlementLimitError("monthly_posts").is_plan_inactive is False

    full = budget_error_to_http(
        EntitlementLimitError("plan_daily_tokens", budget=20_000, used=20_001, resets_in_seconds=5)
    )
    assert full.status_code == 429
    assert full.detail["limit_kind"] == "plan_daily_tokens"
    assert full.detail["budget"] == 20_000 and full.detail["used"] == 20_001
    assert full.detail["resets_in_seconds"] == 5 and full.headers == {"Retry-After": "5"}


def test_platform_cap_is_503_with_retry_after() -> None:
    http = budget_error_to_http(PlatformBudgetExhaustedError(120))
    assert http.status_code == 503
    assert http.detail["error"] == "platform_budget_exhausted"
    assert http.detail["resets_in_seconds"] == 120
    assert http.headers == {"Retry-After": "120"}
    assert PlatformBudgetExhaustedError(0).resets_in_seconds == 1  # never a zero Retry-After


def test_unpriced_and_cost_log_unavailable_are_503_without_retry_after() -> None:
    unpriced = budget_error_to_http(UnpricedModelError("gpt-x"))
    assert unpriced.status_code == 503 and unpriced.headers is None
    assert (
        unpriced.detail["error"] == "ai_model_unpriced" and "gpt-x" in unpriced.detail["message"]
    )
    unavailable = budget_error_to_http(CostLogUnavailableError())
    assert unavailable.status_code == 503 and unavailable.headers is None
    assert unavailable.detail["error"] == "cost_log_unavailable"
    # any future guard error falls into the fail-closed 503 bucket
    generic = budget_error_to_http(BudgetGuardError("boom"))
    assert generic.status_code == 503 and generic.detail["error"] == "budget_guard_error"
