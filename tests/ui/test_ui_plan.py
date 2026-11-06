"""SPRINT-14-B B-3 — ``POST /app/b/{id}/diagnosis/content-plan`` (drafts only, D-055)."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.modules.ai_gateway.contract import AIError, ErrorCategory
from src.ui.router import I18N_PATH

from tests.conftest import BRAND_ID
from tests.fakes import FakeBrandBrainRepository, FakeContentRepository
from tests.modules.brand_brain.test_b3_advisor import (  # noqa: F401 - fixtures
    ScriptedGateway,
    _evidence,
    _plan_payload,
    gateway,
)
from tests.ui.conftest import DIAGNOSIS, VIEWER_LOGIN, csrf_of, login

PLAN = f"{DIAGNOSIS}/content-plan"
MESSAGES = json.loads(I18N_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def plan_app(ui_app: FastAPI, gateway: ScriptedGateway) -> FastAPI:  # noqa: F811
    from src.modules.ai_gateway.budget import DailyBudget
    from src.modules.ai_gateway.budget_guard import BudgetGuard
    from src.modules.ai_gateway.pricing import PriceTable
    from src.modules.brand_brain import router

    settings = ui_app.state.settings
    guard = BudgetGuard(
        gateway,
        writer=ui_app.state.cost_writer,
        price_table=PriceTable({"scripted-1": (Decimal(0), Decimal(0))}),
        daily_budget=DailyBudget(settings.ai_daily_token_budget_per_brand),
        platform_daily_usd_cap=settings.platform_daily_usd_cap,
        usage_reader=ui_app.state.budget_reader,
    )
    ui_app.dependency_overrides[router.get_brain_gateway] = lambda: guard
    return ui_app


@pytest.fixture
def client(plan_app: FastAPI) -> TestClient:
    with TestClient(plan_app, follow_redirects=False) as c:
        yield c


def _csrf(client: TestClient) -> str:
    return csrf_of(client.get(DIAGNOSIS).text)


def test_plan_creates_drafts_only_and_shows_counts_and_links(
    client: TestClient,
    gateway: ScriptedGateway,  # noqa: F811
    brain_repo: FakeBrandBrainRepository,
    content_repo: FakeContentRepository,
) -> None:
    ev = _evidence(brain_repo, "inferred")
    gateway.payload = _plan_payload(ev, 3)
    gateway.payload["items"].append(
        {"title": "t", "channel": "tiktok", "body": "b", "evidence_ids": [str(ev.id)]}
    )
    login(client)
    res = client.post(PLAN, data={"csrf": _csrf(client)})
    assert res.status_code == 200, res.text
    html = res.text
    assert "data-plan-counts" in html and MESSAGES["plan.created"] in html
    assert html.count("data-plan-item=") == 3 and html.count('data-status="draft"') >= 3
    assert MESSAGES["plan.done"].format(n=3, dropped=1) in html
    assert f"/app/b/{BRAND_ID}/content?status=draft" in html
    assert len(content_repo.items) == 3
    assert {i.status.value for i in content_repo.items.values()} == {
        "draft"
    }  # never auto-approved
    assert all(getattr(i, "scheduled_for", None) is None for i in content_repo.items.values())
    assert not getattr(content_repo, "jobs", {})  # nothing queued


def test_plan_errors_are_arabic_and_create_nothing(
    client: TestClient,
    gateway: ScriptedGateway,  # noqa: F811
    brain_repo: FakeBrandBrainRepository,
    content_repo: FakeContentRepository,
) -> None:
    ev = _evidence(brain_repo, "inferred")
    login(client)
    csrf = _csrf(client)
    gateway.payload = _plan_payload(ev, 2)
    gateway.error = AIError(ErrorCategory.PROVIDER_UNAVAILABLE, provider_code="boom-secret-detail")
    res = client.post(PLAN, data={"csrf": csrf})
    assert MESSAGES["error.ai_upstream_unavailable"] in res.text
    assert "boom-secret-detail" not in res.text
    gateway.error = AIError(ErrorCategory.QUOTA_EXCEEDED)
    assert MESSAGES["error.ai_quota_exceeded"] in client.post(PLAN, data={"csrf": csrf}).text
    gateway.error = None
    gateway.raw = "not json"
    assert MESSAGES["error.ai_invalid_output"] in client.post(PLAN, data={"csrf": csrf}).text
    assert not content_repo.items


def test_plan_rate_limit_is_translated(client: TestClient, limiter) -> None:  # noqa: ANN001
    login(client)
    csrf = _csrf(client)
    limiter.reject_after = len(limiter.hits)
    res = client.post(PLAN, data={"csrf": csrf})
    assert MESSAGES["error.brain_rate_limited"] in res.text


def test_plan_csrf_viewer_and_anonymous_have_no_effect(
    client: TestClient,
    gateway: ScriptedGateway,  # noqa: F811
    brain_repo: FakeBrandBrainRepository,
    content_repo: FakeContentRepository,
) -> None:
    gateway.payload = _plan_payload(_evidence(brain_repo, "inferred"), 3)
    assert client.post(PLAN, data={"csrf": "x"}).status_code == 303  # anonymous
    login(client)
    assert client.post(PLAN, data={"csrf": "bad"}).status_code == 403
    client.cookies.clear()
    login(client, VIEWER_LOGIN)
    page = client.get(DIAGNOSIS).text
    assert 'data-form="content-plan"' not in page
    res = client.post(PLAN, data={"csrf": csrf_of(page)})
    assert MESSAGES["error.insufficient_role"] in res.text
    assert not content_repo.items and not gateway.calls
