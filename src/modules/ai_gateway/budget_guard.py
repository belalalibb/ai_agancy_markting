"""``BudgetGuard`` — the one financial wrapper around every AI call (SPRINT-11 B-3, D-075).

Wraps any :class:`AIGateway` and satisfies the same protocol (``complete(req)``), so every
consumer (content drafts, WhatsApp reply suggestions, Brand Brain) goes through exactly one
write path into ``ai_cost_log``. The order inside :meth:`BudgetGuard.complete` is fixed:

1. **price first** — a platform-paid model missing from the price table is refused before
   any row or any call (:class:`UnpricedModelError`); ``Decimal("0")`` is a valid price;
2. the brand's daily token budget (:class:`DailyBudget`) ⇒ :class:`BrandBudgetExceededError`;
3. the platform USD cap over the last 24 h (platform mode only) ⇒
   :class:`PlatformBudgetExhaustedError`;
4. the plan limits (``limits_reader``, Part C) ⇒ :class:`EntitlementLimitError`;
5. **reserve** a ``pending`` row on an independent transaction (D-072) carrying a
   conservative estimate (``price × (prompt_estimate + max_tokens)``) so concurrent calls
   already count against the cap;
6. call the provider, then **settle** (``ok`` / ``error``) or **refund** when the error
   category proves the provider was never reached;
7. a failing cost log is a hard error for platform-paid calls (:class:`CostLogUnavailableError`
   ⇒ 503) and a logged warning for ``user_key`` calls (the user pays their own provider).

These exceptions are **not** :class:`ErrorCategory` members (D-067 keeps twelve); routers map
them through :func:`src.modules.ai_gateway.http_errors.budget_error_to_http`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from src.modules.ai_gateway.budget import (
    TOKEN_BUDGET_WINDOW,
    BudgetUsageReader,
    BudgetVerdict,
    DailyBudget,
    EntitlementLimitsReader,
)
from src.modules.ai_gateway.contract import AIError, ErrorCategory
from src.modules.ai_gateway.cost_log import CostEntry, CostLogWriter, CostOutcome
from src.modules.ai_gateway.gateway import AIGateway, CompletionRequest, CompletionResult
from src.modules.ai_gateway.pricing import PriceTable

logger = logging.getLogger(__name__)

#: Categories that prove the request never reached the provider ⇒ the reservation is refunded.
NOT_REACHED_CATEGORIES: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.INVALID_CREDENTIAL,
        ErrorCategory.AUTH_EXPIRED,
        ErrorCategory.UNSUPPORTED_CAPABILITY,
        ErrorCategory.BAD_REQUEST,
    }
)
#: ``Retry-After`` for a platform cap hit — the window is rolling, one hour is conservative.
PLATFORM_CAP_RETRY_SECONDS = 3600
#: Rough prompt-size estimate (≈4 characters per token) used for reservations and for the
#: conservative cost of an ``error`` row whose usage the provider did not report.
CHARS_PER_TOKEN = 4


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def estimate_prompt_tokens(req: CompletionRequest) -> int:
    return max(1, (len(req.system) + len(req.user)) // CHARS_PER_TOKEN)


# -------------------------------------------------------------- exceptions --
class BudgetGuardError(Exception):
    """Base of every financial refusal; ``error`` is the stable HTTP error code."""

    error = "budget_guard_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnpricedModelError(BudgetGuardError):
    """Platform-paid model missing from ``AI_PRICE_TABLE_JSON`` ⇒ 503 ``ai_model_unpriced``."""

    error = "ai_model_unpriced"

    def __init__(self, model: str) -> None:
        super().__init__(f"model {model!r} has no price — platform calls are refused")
        self.model = model


class BrandBudgetExceededError(BudgetGuardError):
    """Daily token budget of the brand exhausted ⇒ 429 ``generation_budget_exceeded``."""

    error = "generation_budget_exceeded"
    limit_kind = "brand_daily_tokens"

    def __init__(self, verdict: BudgetVerdict) -> None:
        super().__init__("daily token budget exceeded")
        self.budget = verdict.budget
        self.used = verdict.used
        self.resets_in_seconds = verdict.resets_in_seconds


class PlatformBudgetExhaustedError(BudgetGuardError):
    """The platform's 24 h USD cap is reached ⇒ 503 ``platform_budget_exhausted``."""

    error = "platform_budget_exhausted"

    def __init__(self, resets_in_seconds: int = PLATFORM_CAP_RETRY_SECONDS) -> None:
        super().__init__("platform daily budget exhausted")
        self.resets_in_seconds = max(1, int(resets_in_seconds))


class EntitlementLimitError(BudgetGuardError):
    """A plan limit (D-073) refuses the call ⇒ 429 ``generation_budget_exceeded`` with
    ``limit_kind`` = ``plan_inactive`` / ``plan_daily_tokens`` / …"""

    error = "generation_budget_exceeded"

    def __init__(
        self,
        limit_kind: str,
        *,
        budget: int | None = None,
        used: int | None = None,
        resets_in_seconds: int = PLATFORM_CAP_RETRY_SECONDS,
    ) -> None:
        super().__init__(f"plan limit reached: {limit_kind}")
        self.limit_kind = limit_kind
        self.budget = budget
        self.used = used
        self.resets_in_seconds = max(1, int(resets_in_seconds))


class CostLogUnavailableError(BudgetGuardError):
    """The cost log cannot be written and the platform would pay ⇒ 503
    ``cost_log_unavailable`` (fail closed — never spend money that cannot be recorded)."""

    error = "cost_log_unavailable"

    def __init__(self) -> None:
        super().__init__("ai cost log unavailable")


# ----------------------------------------------------------------- context --
@dataclass(frozen=True, slots=True)
class BudgetContext:
    """What the guard needs to know about one call (derived from the request)."""

    brand_id: UUID
    purpose: str
    credential_mode: str
    prompt_version: str
    request_id: str | None = None

    @classmethod
    def from_request(cls, req: CompletionRequest, *, request_id: str | None) -> BudgetContext:
        return cls(
            brand_id=req.brand_id,
            purpose=req.purpose,
            credential_mode=req.credential_mode,
            prompt_version=(req.prompt_version or req.purpose)[:32],
            request_id=request_id[:64] if request_id else None,
        )

    @property
    def platform_pays(self) -> bool:
        return self.credential_mode == "platform"


# ------------------------------------------------------------------- guard --
class BudgetGuard:
    """Satisfies :class:`AIGateway`; see the module docstring for the fixed order."""

    def __init__(
        self,
        inner: AIGateway,
        *,
        writer: CostLogWriter,
        price_table: PriceTable,
        daily_budget: DailyBudget,
        platform_daily_usd_cap: Decimal,
        usage_reader: BudgetUsageReader,
        limits_reader: EntitlementLimitsReader | None = None,
        strict_pricing: bool = True,
        request_id: str | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._inner = inner
        self._writer = writer
        self._prices = price_table
        self._daily_budget = daily_budget
        self._cap = platform_daily_usd_cap
        self._usage_reader = usage_reader
        self._limits = limits_reader
        self._strict = strict_pricing
        self._request_id = request_id
        self._clock = clock

    @property
    def inner(self) -> AIGateway:
        return self._inner

    @property
    def name(self) -> str:
        return str(getattr(self._inner, "name", "unknown"))

    @property
    def model(self) -> str:
        return str(getattr(self._inner, "model", "unknown"))

    # ------------------------------------------------------------- main ----
    async def complete(self, req: CompletionRequest) -> CompletionResult:
        ctx = BudgetContext.from_request(req, request_id=self._request_id)
        model = self.model
        now = self._clock()

        # 1. price first (fail closed for platform money; ``Decimal("0")`` is a price).
        unit_price = self._prices.estimate(model, 1000, 1000)
        if unit_price is None and ctx.platform_pays and self._strict:
            raise UnpricedModelError(model)

        # 2. brand tokens.
        verdict = await self._daily_budget.check(
            ctx.brand_id, usage_reader=self._usage_reader, now=now
        )
        if not verdict.allowed:
            raise BrandBudgetExceededError(verdict)

        # 3. platform cap (platform only).
        if ctx.platform_pays:
            try:
                used_usd = await self._writer.platform_usd_since(now - TOKEN_BUDGET_WINDOW)
            except Exception as exc:  # noqa: BLE001 - fail closed, see step 7
                logger.exception("ai_cost_log unavailable while reading the platform cap")
                raise CostLogUnavailableError() from exc
            if used_usd >= self._cap:
                raise PlatformBudgetExhaustedError()

        # 4. plan limits (Part C).
        if self._limits is not None:
            await self._limits.check(ctx.brand_id, ctx.purpose)

        # 5. reserve.
        prompt_estimate = estimate_prompt_tokens(req)
        reservation = self._prices.estimate(model, prompt_estimate, req.max_tokens)
        entry_id = await self._reserve(
            CostEntry(
                brand_id=ctx.brand_id,
                purpose=ctx.purpose,
                provider=self.name[:32],
                model=model[:64],
                credential_mode=ctx.credential_mode,
                estimated_cost_usd=reservation,
                latency_ms=0,
                prompt_version=ctx.prompt_version,
                outcome=CostOutcome.PENDING,
                created_at=now,
                request_id=ctx.request_id,
            ),
            platform_pays=ctx.platform_pays,
        )

        # 6. call ⇒ settle / refund.
        started = time.perf_counter()
        try:
            result = await self._inner.complete(req)
        except AIError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            if exc.category in NOT_REACHED_CATEGORIES:
                await self._refund(entry_id, platform_pays=ctx.platform_pays)
            else:
                await self._settle_error(
                    entry_id,
                    model=model,
                    prompt_tokens=prompt_estimate,
                    latency_ms=latency_ms,
                    category=exc.category.value,
                    platform_pays=ctx.platform_pays,
                )
            raise
        except Exception:
            latency_ms = int((time.perf_counter() - started) * 1000)
            await self._settle_error(
                entry_id,
                model=model,
                prompt_tokens=prompt_estimate,
                latency_ms=latency_ms,
                category=ErrorCategory.NON_RETRYABLE_ERROR.value,
                platform_pays=ctx.platform_pays,
            )
            raise
        await self._settle(
            entry_id,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            estimated_cost_usd=self._prices.estimate(
                model, result.prompt_tokens, result.completion_tokens
            ),
            latency_ms=result.latency_ms,
            outcome=CostOutcome.OK,
            error_category=None,
            platform_pays=ctx.platform_pays,
        )
        return result

    # ---------------------------------------------------------- writer ----
    async def _reserve(self, entry: CostEntry, *, platform_pays: bool) -> UUID | None:
        try:
            return await self._writer.reserve(entry)
        except Exception as exc:  # noqa: BLE001 - step 7
            if platform_pays:
                logger.exception("ai_cost_log reserve failed — refusing platform-paid call")
                raise CostLogUnavailableError() from exc
            logger.exception("ai_cost_log reserve failed — user_key call continues unlogged")
            return None

    async def _settle_error(
        self,
        entry_id: UUID | None,
        *,
        model: str,
        prompt_tokens: int,
        latency_ms: int,
        category: str,
        platform_pays: bool,
    ) -> None:
        await self._settle(
            entry_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            estimated_cost_usd=self._prices.estimate(model, prompt_tokens, 0),
            latency_ms=latency_ms,
            outcome=CostOutcome.ERROR,
            error_category=category,
            platform_pays=platform_pays,
        )

    async def _settle(
        self,
        entry_id: UUID | None,
        *,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        estimated_cost_usd: Decimal | None,
        latency_ms: int,
        outcome: CostOutcome,
        error_category: str | None,
        platform_pays: bool,
    ) -> None:
        if entry_id is None:
            return
        try:
            await self._writer.settle(
                entry_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                estimated_cost_usd=estimated_cost_usd,
                latency_ms=latency_ms,
                outcome=outcome,
                error_category=error_category,
            )
        except Exception as exc:  # noqa: BLE001 - step 7
            logger.exception("ai_cost_log settle failed outcome=%s", outcome.value)
            if platform_pays:
                # The row stays ``pending`` and is counted conservatively until the
                # scheduler marks it ``ambiguous`` — the money is never lost from the cap.
                raise CostLogUnavailableError() from exc

    async def _refund(self, entry_id: UUID | None, *, platform_pays: bool) -> None:
        if entry_id is None:
            return
        try:
            await self._writer.refund(entry_id)
        except Exception as exc:  # noqa: BLE001 - step 7
            logger.exception("ai_cost_log refund failed")
            if platform_pays:
                raise CostLogUnavailableError() from exc


# ------------------------------------------------------------ scheduler job --
async def settle_stale_reservations(
    writer: CostLogWriter, *, now: datetime, timeout_seconds: float
) -> int:
    """Scheduler step (B-4): ``pending`` rows older than ``2 × AI_HTTP_TIMEOUT_SECONDS`` ⇒
    ``ambiguous`` (their conservative reservation keeps counting against the cap)."""
    older_than = now - 2 * _seconds(timeout_seconds)
    count = await writer.settle_stale(older_than)
    if count:
        logger.warning("ai_cost_log: %d stale reservation(s) marked ambiguous", count)
    return count


def _seconds(value: float):  # noqa: ANN202 - timedelta helper
    from datetime import timedelta

    return timedelta(seconds=value)


__all__ = [
    "NOT_REACHED_CATEGORIES",
    "PLATFORM_CAP_RETRY_SECONDS",
    "BrandBudgetExceededError",
    "BudgetContext",
    "BudgetGuard",
    "BudgetGuardError",
    "CostLogUnavailableError",
    "EntitlementLimitError",
    "PlatformBudgetExhaustedError",
    "UnpricedModelError",
    "estimate_prompt_tokens",
    "settle_stale_reservations",
    "utcnow",
]
