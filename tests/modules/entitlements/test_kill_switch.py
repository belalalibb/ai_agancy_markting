"""SPRINT-11 C-8 (D-076) — ``FEATURE_ENTITLEMENTS_ENFORCED=false``: a **suspended** brand
still generates content (the plan check is skipped) and one WARNING is logged at boot.
With the switch on, the same brand gets 429 ``generation_budget_exceeded`` /
``limit_kind=plan_inactive`` (acceptance #15)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.config import Settings
from src.modules.entitlements.domain.models import DEFAULT_LIMITS, BrandEntitlement, Plan

from tests.conftest import BRAND_ID, OWNER_ID

API = "/api/v1"
Headers = Callable[[UUID], dict[str, str]]


def _suspend(app: FastAPI) -> None:
    app.state.entitlement_repo.entitlements[BRAND_ID] = BrandEntitlement(
        brand_id=BRAND_ID, plan=Plan.STARTER, limits=DEFAULT_LIMITS[Plan.STARTER], is_active=False
    )


def _generate(client: TestClient, headers: dict[str, str]):  # noqa: ANN202
    item = client.post(
        f"{API}/brands/{BRAND_ID}/content/items",
        json={"channel": "facebook_page", "body": "نص"},
        headers=headers,
    )
    assert item.status_code == 201, item.text
    return client.post(
        f"{API}/brands/{BRAND_ID}/content/items/{item.json()['id']}/generate",
        json={"brief": "اكتب منشوراً عن العرض"},
        headers=headers,
    )


def test_suspended_brand_is_402_plan_inactive_when_enforced(
    app: FastAPI, client: TestClient, auth_headers: Headers
) -> None:
    """SPRINT-12 A-1 (H1): a suspended plan is **402 ``entitlement_plan_inactive``** on the
    AI path (was 429 ``plan_inactive`` — 429 is reserved for time-bound limits)."""
    _suspend(app)
    res = _generate(client, auth_headers(OWNER_ID))
    assert res.status_code == 402, res.text
    detail = res.json()["detail"]
    assert detail["error"] == "entitlement_plan_inactive"
    assert detail["plan"] == "starter"  # the suspended plan (see _suspend)
    assert "Retry-After" not in res.headers


def test_kill_switch_lets_suspended_brand_generate_and_warns_at_boot(
    settings: Settings,
    store,  # noqa: ANN001
    fake_repo,  # noqa: ANN001
    status_marker,  # noqa: ANN001
    hasher,  # noqa: ANN001
    limiter,  # noqa: ANN001
    content_repo,  # noqa: ANN001
    publish_repo,  # noqa: ANN001
    auth_headers: Headers,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from tests.conftest import app as build_app

    off = settings.model_copy(update={"feature_entitlements_enforced": False})
    with caplog.at_level(logging.WARNING, logger="src.main"):
        application = build_app.__wrapped__(  # the fixture body, with the switched settings
            off, store, fake_repo, status_marker, hasher, limiter, content_repo, publish_repo
        )
    assert any("FEATURE_ENTITLEMENTS_ENFORCED=false" in r.getMessage() for r in caplog.records)
    _suspend(application)
    with TestClient(application) as client:
        res = _generate(client, auth_headers(OWNER_ID))
    assert res.status_code == 200, res.text  # generated despite the suspended plan
    assert res.json()["current_body"].startswith("[fake:content_draft:")


def test_no_warning_when_enforced(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    from src.main import create_app

    with caplog.at_level(logging.WARNING, logger="src.main"):
        create_app(settings)
    assert not any("FEATURE_ENTITLEMENTS_ENFORCED" in r.getMessage() for r in caplog.records)
