"""SPRINT-12.1-B C-2 — the diagnosis screen: grade badges, accept/dismiss, public link."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from src.modules.brand_brain.domain.models import (
    Evidence,
    EvidenceKindsSummary,
    Recommendation,
)
from src.ui.router import I18N_PATH, _grade_of

from tests.conftest import BRAND_ID
from tests.fakes import FakeBrandBrainRepository
from tests.ui.conftest import DIAGNOSIS, VIEWER_LOGIN, csrf_of, login

HX = {"HX-Request": "true"}
MESSAGES = json.loads(I18N_PATH.read_text(encoding="utf-8"))
NOW = datetime.now(UTC)
_SOURCE = {"measured": "internal_db", "inferred": "model_inference", "estimated": "public_web"}
_METRIC = {
    "measured": "posting_frequency_7d",
    "inferred": "tone_match_score",
    "estimated": "public_page_summary",
}


def _rec(
    repo: FakeBrandBrainRepository, grade: str, title: str = "Reply faster"
) -> Recommendation:
    ev = Evidence(
        brand_id=BRAND_ID,
        subject_kind="brand",
        subject_id=BRAND_ID,
        evidence_kind=grade,  # type: ignore[arg-type]
        metric=_METRIC[grade],
        value={"count": 3},
        confidence=Decimal("0.9"),
        source_kind=_SOURCE[grade],  # type: ignore[arg-type]
        source_ref=None,
        observed_at=NOW,
        created_at=NOW,
    )
    repo.evidence[ev.id] = ev
    rec = Recommendation(
        brand_id=BRAND_ID,
        kind="gap_action",
        title=title,
        rationale="Three open chats waited more than two hours.",
        evidence_ids=[ev.id],
        evidence_kinds_summary=EvidenceKindsSummary.of([ev]),
        provenance={"kind": "ai_generated", "prompt_version": "brand_diagnosis_v1"},
        created_at=NOW,
    )
    repo.recommendations[rec.id] = rec
    return rec


def test_grade_derivation_mirrors_share_module() -> None:
    assert _grade_of({"measured": 0, "inferred": 2, "estimated": 5}) == "inferred"
    assert _grade_of({"measured": 1, "inferred": 2}) == "measured"
    assert _grade_of({}) == "estimated"


def test_diagnosis_renders_grade_badges(
    ui_client: TestClient, brain_repo: FakeBrandBrainRepository
) -> None:
    measured = _rec(brain_repo, "measured")
    estimated = _rec(brain_repo, "estimated", title="Post more often")
    login(ui_client)
    res = ui_client.get(DIAGNOSIS)
    assert res.status_code == 200, res.text
    html = res.text
    assert f'data-recommendation="{measured.id}" data-grade="measured"' in html
    assert f'data-recommendation="{estimated.id}" data-grade="estimated"' in html
    assert MESSAGES["grade.measured"] in html and MESSAGES["grade.estimated"] in html
    assert "grade.measured" not in html
    assert f"{MESSAGES['diagnosis.evidence']}: 1" in html
    assert f"/diagnosis/{measured.id}/share" in html and f"/diagnosis/{measured.id}/accept" in html
    assert 'data-form="diagnose"' in html


def test_diagnosis_accept_and_dismiss_success(
    ui_client: TestClient, brain_repo: FakeBrandBrainRepository
) -> None:
    a = _rec(brain_repo, "measured")
    b = _rec(brain_repo, "inferred", title="Tone")
    login(ui_client)
    csrf = csrf_of(ui_client.get(DIAGNOSIS).text)
    res = ui_client.post(f"{DIAGNOSIS}/{a.id}/accept", data={"csrf": csrf}, headers=HX)
    assert res.status_code == 200 and MESSAGES["diagnosis.accept_done"] in res.text
    assert f'data-recommendation="{a.id}" data-grade="measured" data-status="accepted"' in res.text
    res = ui_client.post(f"{DIAGNOSIS}/{b.id}/dismiss", data={"csrf": csrf})
    assert res.status_code == 200 and brain_repo.recommendations[b.id].status == "dismissed"
    assert f"/diagnosis/{b.id}/share" not in res.text  # dismissed ⇒ no share button


def test_diagnosis_share_link_shown_once_then_unshare_and_409(
    ui_client: TestClient, brain_repo: FakeBrandBrainRepository
) -> None:
    rec = _rec(brain_repo, "measured")
    gone = _rec(brain_repo, "inferred", title="Dismissed one")
    login(ui_client)
    csrf = csrf_of(ui_client.get(DIAGNOSIS).text)
    res = ui_client.post(f"{DIAGNOSIS}/{rec.id}/share", data={"csrf": csrf}, headers=HX)
    assert res.status_code == 200, res.text
    assert f'data-share="{rec.id}"' in res.text and "/api/v1/d/" in res.text
    assert "navigator.clipboard" in res.text and MESSAGES["diagnosis.share_done"] in res.text
    assert rec.id in brain_repo.shares
    # the URL is never shown again on a plain reload (the UI stores nothing)
    again = ui_client.get(DIAGNOSIS).text
    assert "data-share=" not in again and "/api/v1/d/" not in again
    res = ui_client.post(f"{DIAGNOSIS}/{rec.id}/unshare", data={"csrf": csrf}, headers=HX)
    assert res.status_code == 200 and MESSAGES["diagnosis.unshare_done"] in res.text
    assert brain_repo.shares.get(rec.id, (None, None))[0] is None
    # 409 recommendation_dismissed ⇒ translated flash
    ui_client.post(f"{DIAGNOSIS}/{gone.id}/dismiss", data={"csrf": csrf})
    res = ui_client.post(f"{DIAGNOSIS}/{gone.id}/share", data={"csrf": csrf}, headers=HX)
    assert res.status_code == 200
    assert MESSAGES["error.recommendation_dismissed"] in res.text
    assert "recommendation_dismissed" not in res.text
    assert ui_client.post(f"{DIAGNOSIS}/{gone.id}/bogus", data={"csrf": csrf}).status_code == 404


def test_diagnose_without_gateway_and_viewer_are_translated(
    ui_client: TestClient, brain_repo: FakeBrandBrainRepository
) -> None:
    rec = _rec(brain_repo, "measured")
    login(ui_client)
    csrf = csrf_of(ui_client.get(DIAGNOSIS).text)
    res = ui_client.post(f"{DIAGNOSIS}/diagnose", data={"csrf": csrf}, headers=HX)
    assert res.status_code == 200 and "data-flash" in res.text
    assert "ai_provider_not_configured" not in res.text and "detail" not in res.text
    ui_client.cookies.clear()
    login(ui_client, VIEWER_LOGIN)
    html = ui_client.get(DIAGNOSIS).text
    assert "data-cost-today" not in html  # cost is OWNER-only upstream ⇒ not even requested
    assert "data-grade-badge" in html
    csrf = csrf_of(html)
    res = ui_client.post(f"{DIAGNOSIS}/{rec.id}/accept", data={"csrf": csrf}, headers=HX)
    assert res.status_code == 200 and MESSAGES["error.insufficient_role"] in res.text
    assert brain_repo.recommendations[rec.id].status == "proposed"
