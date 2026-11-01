"""``CostLoggingGateway`` (SPRINT-10 B-2): one ``ai_cost_log`` row per gateway call.

The row is written through :class:`CostLogWriter` (PostgreSQL: ``independent_session()``,
D-072) **after** the inner call returns or raises, so it survives a later failure of the
request transaction and a failed call is recorded as ``outcome='error'`` with its
``error_category``. The row carries metadata only — purpose, provider, model,
``credential_mode``, token counts, latency, ``prompt_version`` — never the prompt, the output
or the key (§2-0 هـ / D-069).

``estimated_cost_usd`` comes from the optional :class:`PriceTable` (``AI_PRICE_TABLE_JSON``);
an unknown model or missing usage ⇒ ``NULL``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from src.modules.ai_gateway.contract import AIError
from src.modules.ai_gateway.gateway import AIGateway, CompletionRequest, CompletionResult
from src.modules.brand_brain.domain.models import CostEntry, CostOutcome
from src.modules.brand_brain.domain.repository import CostLogWriter

logger = logging.getLogger(__name__)

_USD = Decimal("0.000001")
PRICE_KEYS = ("prompt_per_1k", "completion_per_1k")


class PriceTable:
    """``{"gpt-4o-mini": {"prompt_per_1k": 0.00015, "completion_per_1k": 0.0006}}``.

    Malformed JSON or malformed entries are ignored (logged once at WARNING) — a price table
    can never make an AI call fail.
    """

    def __init__(self, prices: dict[str, tuple[Decimal, Decimal]] | None = None) -> None:
        self._prices = dict(prices or {})

    @classmethod
    def from_json(cls, raw: str) -> PriceTable:
        if not raw.strip():
            return cls()
        try:
            payload = json.loads(raw)
        except ValueError:
            logger.warning("AI_PRICE_TABLE_JSON is not valid JSON — cost estimates disabled")
            return cls()
        prices: dict[str, tuple[Decimal, Decimal]] = {}
        if isinstance(payload, dict):
            for model, entry in payload.items():
                parsed = _parse_entry(entry)
                if parsed is None:
                    logger.warning("AI_PRICE_TABLE_JSON: ignoring malformed entry for a model")
                    continue
                prices[str(model)] = parsed
        return cls(prices)

    @property
    def models(self) -> frozenset[str]:
        return frozenset(self._prices)

    def estimate(
        self, model: str, prompt_tokens: int | None, completion_tokens: int | None
    ) -> Decimal | None:
        entry = self._prices.get(model)
        if entry is None or prompt_tokens is None or completion_tokens is None:
            return None
        prompt_price, completion_price = entry
        total = (
            Decimal(prompt_tokens) * prompt_price + Decimal(completion_tokens) * completion_price
        ) / 1000
        return total.quantize(_USD, rounding=ROUND_HALF_UP)


def _parse_entry(entry: Any) -> tuple[Decimal, Decimal] | None:
    if not isinstance(entry, dict) or any(k not in entry for k in PRICE_KEYS):
        return None
    try:
        values = tuple(Decimal(str(entry[k])) for k in PRICE_KEYS)
    except (ArithmeticError, ValueError, TypeError):
        return None
    if any(v < 0 for v in values):
        return None
    return values[0], values[1]


class CostLoggingGateway:
    """Wraps any :class:`AIGateway`; satisfies the same protocol."""

    def __init__(
        self,
        inner: AIGateway,
        writer: CostLogWriter,
        *,
        price_table: PriceTable | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        self._inner = inner
        self._writer = writer
        self._prices = price_table or PriceTable()
        self._clock = clock

    @property
    def inner(self) -> AIGateway:
        return self._inner

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        started = time.perf_counter()
        try:
            result = await self._inner.complete(req)
        except AIError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            await self._record(req, result=None, error=exc, latency_ms=latency_ms)
            raise
        await self._record(req, result=result, error=None, latency_ms=result.latency_ms)
        return result

    async def _record(
        self,
        req: CompletionRequest,
        *,
        result: CompletionResult | None,
        error: AIError | None,
        latency_ms: int,
    ) -> None:
        provider = result.provider if result else str(getattr(self._inner, "name", "unknown"))
        model = result.model if result else str(getattr(self._inner, "model", "unknown"))
        prompt_tokens = result.usage.prompt_tokens if result else None
        completion_tokens = result.usage.completion_tokens if result else None
        entry = CostEntry(
            brand_id=req.brand_id,
            purpose=req.purpose,
            provider=provider[:32],
            model=model[:64],
            credential_mode=req.credential_mode,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=self._prices.estimate(model, prompt_tokens, completion_tokens),
            latency_ms=max(0, latency_ms),
            prompt_version=req.prompt_version or req.purpose,
            outcome=CostOutcome.ERROR if error is not None else CostOutcome.OK,
            error_category=error.category.value if error is not None else None,
            created_at=self._clock(),
        )
        try:
            await self._writer.write(entry)
        except Exception:  # noqa: BLE001 - the cost row must never break the AI call
            logger.exception(
                "ai_cost_log write failed purpose=%s outcome=%s", entry.purpose, entry.outcome
            )
