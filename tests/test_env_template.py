"""SPRINT-11 A-8 — ``.env.template`` documents every AI / platform / feature setting."""

from __future__ import annotations

import re
from pathlib import Path

from src.core.config import Settings

TEMPLATE = Path(__file__).resolve().parents[1] / ".env.template"
PREFIXES = ("ai_", "platform_", "feature_")


def _template_keys() -> set[str]:
    keys: set[str] = set()
    for line in TEMPLATE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if match:
            keys.add(match.group(1))
    return keys


def test_env_template_lists_all_ai_settings() -> None:
    """Every ``Settings`` field starting with ``ai_`` / ``platform_`` / ``feature_`` appears
    (upper-cased) in ``.env.template`` — and nothing with those prefixes is documented
    there without a matching field."""
    expected = {name.upper() for name in Settings.model_fields if name.startswith(PREFIXES)}
    assert expected, "no AI settings found — did the prefixes change?"
    present = {k for k in _template_keys() if k.startswith(tuple(p.upper() for p in PREFIXES))}
    assert expected <= present, f"missing from .env.template: {sorted(expected - present)}"
    assert present <= expected, (
        f"documented but not a Settings field: {sorted(present - expected)}"
    )


def test_env_template_ai_defaults_match_settings() -> None:
    """The documented defaults equal ``Settings`` defaults (A-8 — the template is executable
    documentation, not a wish list)."""
    text = TEMPLATE.read_text(encoding="utf-8")

    def value_of(key: str) -> str:
        line = next(ln for ln in text.splitlines() if ln.startswith(f"{key}="))
        raw = line.split("=", 1)[1]
        return raw.split("  #", 1)[0].strip()

    defaults = Settings.model_fields
    assert value_of("AI_PROVIDER") == defaults["ai_provider"].default
    assert value_of("AI_PROVIDER_BASE_URL") == defaults["ai_provider_base_url"].default
    assert value_of("AI_PROVIDER_API_KEY") == ""
    assert value_of("AI_DEFAULT_MODEL") == defaults["ai_default_model"].default
    assert (
        float(value_of("AI_HTTP_TIMEOUT_SECONDS")) == defaults["ai_http_timeout_seconds"].default
    )
    assert int(value_of("AI_DAILY_TOKEN_BUDGET_PER_BRAND")) == (
        defaults["ai_daily_token_budget_per_brand"].default
    )
    assert "gpt-4o-mini" in value_of("AI_PRICE_TABLE_JSON")
    assert "ai_model_unpriced" in text  # the fail-closed warning is part of the block
