"""SPRINT-11 C-6 (D-080) — ``brand_brain`` learning signals follow ``brands.learning_consent``:
``none`` ⇒ accept/dismiss still decide the recommendation but write **no** signal row."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import BRAND_ID
from tests.fakes import FakeBrandBrainRepository, FakeStore
from tests.modules.brand_brain.conftest import BRAIN
from tests.modules.brand_brain.test_b3_advisor import (  # noqa: F401 - fixtures
    _proposed,
    b3_client,
    gateway,
)


@pytest.mark.parametrize(("consent", "expected"), [("signals_only", 2), ("full", 2), ("none", 0)])
def test_c6_brain_signals_follow_learning_consent(
    b3_client: TestClient,  # noqa: F811 - fixture imported from test_b3_advisor
    brain_repo: FakeBrandBrainRepository,
    store: FakeStore,
    owner_headers: dict[str, str],
    consent: str,
    expected: int,
) -> None:
    brand = store.brands[BRAND_ID]
    store.brands[BRAND_ID] = brand.model_copy(update={"learning_consent": consent})
    a = _proposed(brain_repo)
    d = _proposed(brain_repo)
    res = b3_client.post(f"{BRAIN}/recommendations/{a}/accept", headers=owner_headers)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "accepted"
    res = b3_client.post(f"{BRAIN}/recommendations/{d}/dismiss", headers=owner_headers)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "dismissed"
    assert brain_repo.locked_recommendations == [a, d]
    assert len(brain_repo.learning_signals) == expected
    if expected:
        assert [s.signal for s in brain_repo.learning_signals] == ["accepted", "dismissed"]
