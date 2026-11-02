"""SPRINT-11.1 A-1 (E1, D-080) — ``POST /brain/content-plan`` with ``learning_consent='none'``
still materialises every item (recommendations + content items) but writes **zero**
``learning_signals`` rows; ``signals_only`` / ``full`` keep writing one ``materialized`` signal
per item."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import BRAND_ID
from tests.fakes import FakeBrandBrainRepository, FakeContentRepository, FakeStore
from tests.modules.brand_brain.conftest import BRAIN
from tests.modules.brand_brain.test_b3_advisor import (  # noqa: F401 - fixtures
    ScriptedGateway,
    _evidence,
    _plan_payload,
    b3_client,
    gateway,
)


@pytest.mark.parametrize(("consent", "expected"), [("none", 0), ("signals_only", 3), ("full", 3)])
def test_a1_content_plan_signals_follow_learning_consent(
    b3_client: TestClient,  # noqa: F811 - fixture imported from test_b3_advisor
    gateway: ScriptedGateway,  # noqa: F811
    brain_repo: FakeBrandBrainRepository,
    content_repo: FakeContentRepository,
    store: FakeStore,
    owner_headers: dict[str, str],
    consent: str,
    expected: int,
) -> None:
    brand = store.brands[BRAND_ID]
    store.brands[BRAND_ID] = brand.model_copy(update={"learning_consent": consent})
    ev = _evidence(brain_repo, "inferred")
    gateway.payload = _plan_payload(ev, 3)
    res = b3_client.post(f"{BRAIN}/content-plan", headers=owner_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["items"]) == 3 and body["items_dropped"] == 0
    # The plan is fully materialised regardless of consent …
    materialized = [r for r in brain_repo.recommendations.values() if r.status == "materialized"]
    assert len(materialized) == 3
    assert len(content_repo.items) == 3
    # … only the learning signals follow D-080.
    signals = brain_repo.learning_signals
    assert len(signals) == expected
    assert all(s.signal == "materialized" for s in signals)
