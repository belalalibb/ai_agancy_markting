"""Fixed per-model price table (``AI_PRICE_TABLE_JSON``) ⇒ ``estimated_cost_usd``.

SPRINT-11 B-3 (D-075): pricing is **fail-closed** for platform-paid calls — a model missing
from the table is refused by :class:`~src.modules.ai_gateway.budget_guard.BudgetGuard`
(503 ``ai_model_unpriced``) before any row is written or any provider is called. A price of
``Decimal("0")`` is valid (the ``fake`` provider is priced 0/0 automatically).
"""

from __future__ import annotations

import json
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_USD = Decimal("0.000001")
PRICE_KEYS = ("prompt_per_1k", "completion_per_1k")


@runtime_checkable
class PriceSource(Protocol):
    """What :class:`~src.modules.ai_gateway.budget_guard.BudgetGuard` needs from a price
    table (SPRINT-11.1 B-3): the static :class:`PriceTable` and the catalog's per-model
    ``CatalogPriceTable`` both satisfy it."""

    @property
    def models(self) -> frozenset[str]: ...

    def estimate_unit(self, model: str) -> tuple[Decimal, Decimal] | None: ...

    def estimate(
        self, model: str, prompt_tokens: int | None, completion_tokens: int | None
    ) -> Decimal | None: ...


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

    def estimate_unit(self, model: str) -> tuple[Decimal, Decimal] | None:
        """``(prompt_per_1k, completion_per_1k)`` of ``model`` or ``None``."""
        return self._prices.get(model)

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


__all__ = ["PRICE_KEYS", "PriceSource", "PriceTable"]
