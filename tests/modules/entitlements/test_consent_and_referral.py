"""SPRINT-11 C-6 / C-7 — pure helpers: ``may_write_signal`` (both consumer-owned protocols)
and ``referral_fields`` (CTWA trimming)."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from src.modules.brand_brain import consent as brain_consent
from src.modules.whatsapp import consent as wa_consent
from src.modules.whatsapp.webhook import REFERRAL_KEYS, REFERRAL_MAX_CHARS, referral_fields


class _Reader:
    def __init__(self, value: str) -> None:
        self.value = value
        self.calls = 0

    async def learning_consent(self, brand_id) -> str:  # noqa: ANN001
        self.calls += 1
        return self.value


@pytest.mark.parametrize("module", [wa_consent, brain_consent])
@pytest.mark.parametrize(
    ("value", "allowed"),
    [("signals_only", True), ("full", True), ("none", False), ("garbage", False)],
)
async def test_may_write_signal_gates_on_consent_value(module, value: str, allowed: bool) -> None:
    reader = _Reader(value)
    assert await module.may_write_signal(reader, uuid4()) is allowed
    assert reader.calls == 1
    # No reader injected (legacy callers / fakes) ⇒ writes allowed.
    assert await module.may_write_signal(None, uuid4()) is True
    assert module.CONSENT_WRITE_ALLOWED == frozenset({"signals_only", "full"})


async def test_always_consent_reader_defaults_to_signals_only() -> None:
    assert await wa_consent.AlwaysConsentReader().learning_consent(uuid4()) == "signals_only"


def test_referral_fields_keeps_known_scalar_keys_trimmed() -> None:
    assert referral_fields({}) is None
    assert referral_fields({"referral": "nope"}) is None
    assert referral_fields({"referral": {}}) is None
    assert referral_fields({"referral": {"unknown": "x", "body": True}}) is None
    raw = {
        "source_url": "https://fb.me/ad",
        "source_id": 42,
        "source_type": "post",
        "headline": "h" * (REFERRAL_MAX_CHARS + 1),
        "body": 1.5,
        "ctwa_clid": "c",
        "image_url": "dropped",
        "video": {"id": "dropped"},
    }
    kept = referral_fields({"referral": raw})
    assert kept is not None and set(kept) == set(REFERRAL_KEYS)
    assert kept["source_id"] == "42" and kept["body"] == "1.5"
    assert len(kept["headline"]) == REFERRAL_MAX_CHARS
    # Bounded: 6 keys × 512 chars ⇒ well under 4 KiB of JSON.
    assert len(json.dumps(kept)) < 6 * (REFERRAL_MAX_CHARS + 32)
