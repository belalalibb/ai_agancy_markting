"""Unit tests — ``src.core.metrics`` (SPRINT-05 A-0 counters)."""

from __future__ import annotations

import threading

import pytest
from src.core.metrics import Counters, counters

pytestmark = pytest.mark.unit


def test_default_counter_exists_and_increments() -> None:
    c = Counters()
    assert c["meta_debug_token_failures"] == 0
    assert c.increment("meta_debug_token_failures") == 1
    assert c.increment("meta_debug_token_failures", 2) == 3
    assert c.get("meta_debug_token_failures") == 3
    assert c.snapshot() == {"meta_debug_token_failures": 3}
    assert list(c) == ["meta_debug_token_failures"]


def test_unknown_counter_is_created_lazily_and_reset_zeroes_all() -> None:
    c = Counters()
    assert c["nope"] == 0
    c.increment("nope")
    assert c.snapshot()["nope"] == 1
    c.reset()
    assert all(v == 0 for v in c.snapshot().values())


def test_increment_is_thread_safe() -> None:
    c = Counters()

    def work() -> None:
        for _ in range(1_000):
            c.increment("meta_debug_token_failures")

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert c["meta_debug_token_failures"] == 8_000


def test_module_level_singleton_is_a_counters_instance() -> None:
    assert isinstance(counters, Counters)
    counters.reset()
    assert counters["meta_debug_token_failures"] == 0
