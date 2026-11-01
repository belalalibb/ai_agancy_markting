"""SPRINT-10 B-3 — diagnosis + recommendations ⇒ drafts under approval (unit, fake wiring).

Covers: server-side grade tags replace the model's own; a recommendation without a valid
evidence id is dropped and counted; ≤10 recommendations / ≤7 items; every content-plan item is
``status=draft`` with ``provenance.kind="ai_generated"`` + ``recommendation_id`` and **zero**
approval events; accept/dismiss need a fresh session + OWNER/MANAGER; ``reply_template`` lands
in ``tone.reply_templates`` only after accept (≤10); learning signals carry no text; limiter
5/h and the shared budget ⇒ 429; invalid output ⇒ 502; AI errors use the D-067 table.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.modules.ai_gateway.contract import AIError, ErrorCategory, Usage
from src.modules.ai_gateway.gateway import CompletionRequest, CompletionResult
from src.modules.brand_brain import router
from src.modules.brand_brain.advisor import (
    GRADE_TAG,
    BrandAdvisor,
    grade_tag,
    strip_fake_tags,
    tag_text,
)
from src.modules.brand_brain.domain.models import Evidence, Recommendation
from src.modules.brand_brain.prompts import BRAND_DIAGNOSIS_V1, CONTENT_PLAN_V1

from tests.conftest import BRAND_ID, OWNER_USER_ID
from tests.fakes import (
    FakeBrandBrainRepository,
    FakeContentRepository,
    FakeRateLimiter,
    FakeStore,
)
from tests.modules.brand_brain.conftest import BRAIN

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------ helpers --
class ScriptedGateway:
    """Returns the scripted JSON (or raises) and records every request."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None
        self.raw: str | None = None
        self.error: AIError | None = None
        self.calls: list[CompletionRequest] = []

    async def complete(self, req: CompletionRequest) -> CompletionResult:
        self.calls.append(req)
        if self.error is not None:
            raise self.error
        text = self.raw if self.raw is not None else json.dumps(self.payload or {})
        return CompletionResult(
            text=text,
            provider=self.name,
            model=self.model,
            usage=Usage(prompt_tokens=120, completion_tokens=80),
            credential_mode=req.credential_mode,
            latency_ms=7,
            finish_reason="stop",
        )


_SOURCE = {"measured": "internal_db", "inferred": "model_inference", "estimated": "public_web"}
_METRIC = {
    "measured": "posting_frequency_7d",
    "inferred": "tone_match_score",
    "estimated": "public_page_summary",
}


def _evidence(repo: FakeBrandBrainRepository, kind: str, *, offset: int = 0) -> Evidence:
    ev = Evidence(
        brand_id=BRAND_ID,
        subject_kind="brand",
        subject_id=BRAND_ID,
        evidence_kind=kind,  # type: ignore[arg-type]
        metric=_METRIC[kind],
        value={"count": 3},
        confidence=Decimal("0.9"),
        source_kind=_SOURCE[kind],  # type: ignore[arg-type]
        source_ref=None,
        observed_at=NOW - timedelta(minutes=offset),
        created_at=NOW - timedelta(minutes=offset),
    )
    repo.evidence[ev.id] = ev
    return ev


@pytest.fixture
def gateway() -> ScriptedGateway:
    return ScriptedGateway()


@pytest.fixture
def b3_client(brain_app: FastAPI, gateway: ScriptedGateway) -> TestClient:
    brain_app.dependency_overrides[router.get_brain_gateway] = lambda: gateway
    return TestClient(brain_app)


def _diagnosis_payload(measured: Evidence, estimated: Evidence) -> dict[str, Any]:
    return {
        "summary": "الملف متسق [مُقاس] لكن الإيقاع ضعيف.",
        "strengths": [{"text": "نبرة واضحة [measured]", "evidence_ids": [str(measured.id)]}],
        "gaps": [
            {"text": "النشر أقل من الهدف", "evidence_ids": [str(measured.id), str(estimated.id)]},
            {"text": "ادعاء بلا دليل", "evidence_ids": []},
        ],
        "recommendations": [
            {
                "kind": "gap_action",
                "title": "انشر 3 مرات أسبوعياً",
                "rationale": "الوتيرة الحالية منخفضة [مُقاس] [estimated]",
                "evidence_ids": [str(measured.id)],
            },
            {
                "kind": "reply_template",
                "title": "قالب ترحيب",
                "rationale": "زمن الرد طويل",
                "evidence_ids": [str(estimated.id), str(measured.id)],
                "template": "أهلاً بك! استلمنا طلبك وسنعود إليك خلال ساعة عمل.",
            },
            {
                "kind": "tone_adjustment",
                "title": "بلا دليل",
                "rationale": "اختراع",
                "evidence_ids": [str(uuid4())],
            },
        ],
    }


# ------------------------------------------------------------- pure helpers --
def test_grade_tag_weakest_grade_wins(brain_repo: FakeBrandBrainRepository) -> None:
    m = _evidence(brain_repo, "measured")
    i = _evidence(brain_repo, "inferred")
    e = _evidence(brain_repo, "estimated")
    assert grade_tag([m]) == "[مُقاس]"
    assert grade_tag([m, i]) == "[مُستنتج]"
    assert grade_tag([m, i, e]) == "[مُقدَّر]"
    assert grade_tag([]) == GRADE_TAG["measured"]


def test_strip_fake_tags_and_tag_text(brain_repo: FakeBrandBrainRepository) -> None:
    m = _evidence(brain_repo, "measured")
    assert strip_fake_tags("نص [مُقاس] آخر [estimated] [مُستنتج]") == "نص آخر"
    tagged = tag_text("x" * 2000 + " [مُقدَّر]", [m], limit=100)
    assert tagged.endswith(" [مُقاس]") and len(tagged) <= 100
    assert tagged.count("[") == 1


# ---------------------------------------------------------------- diagnose --
def test_diagnose_stores_proposed_recommendations_with_server_tags(
    b3_client: TestClient,
    gateway: ScriptedGateway,
    brain_repo: FakeBrandBrainRepository,
    owner_headers: dict[str, str],
) -> None:
    measured = _evidence(brain_repo, "measured")
    estimated = _evidence(brain_repo, "estimated", offset=1)
    gateway.payload = _diagnosis_payload(measured, estimated)

    res = b3_client.post(f"{BRAIN}/diagnose", headers=owner_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    req = gateway.calls[0]
    assert req.json_mode is True and req.purpose == "brand_diagnosis"
    assert req.prompt_version == "brand_diagnosis_v1" and req.credential_mode == "platform"
    assert str(measured.id) in req.user and "[measured]" in req.user
    assert body["summary"] == "الملف متسق لكن الإيقاع ضعيف."
    assert body["strengths"] == [{"text": "نبرة واضحة [مُقاس]", "evidence_ids": [str(measured.id)]}]
    assert len(body["gaps"]) == 1 and body["gaps"][0]["text"].endswith("[مُقدَّر]")
    assert {e["id"] for e in body["evidence_used"]} == {str(measured.id), str(estimated.id)}
    assert {e["kind"] for e in body["evidence_used"]} == {"measured", "estimated"}
    assert body["recommendations_dropped"] == 1
    assert body["prompt_version"] == "brand_diagnosis_v1"
    recs = body["recommendations"]
    assert [r["kind"] for r in recs] == ["gap_action", "reply_template"]
    assert all(r["status"] == "proposed" for r in recs)
    assert recs[0]["rationale"] == "الوتيرة الحالية منخفضة [مُقاس]"
    assert recs[1]["rationale"] == "زمن الرد طويل [مُقدَّر]"
    assert recs[0]["evidence_kinds_summary"] == {"measured": 1, "inferred": 0, "estimated": 0}
    assert recs[1]["evidence_kinds_summary"] == {"measured": 1, "inferred": 0, "estimated": 1}
    assert recs[0]["decided_by_user_id"] is None and recs[0]["materialized_item_id"] is None
    prov = recs[0]["provenance"]
    assert prov["kind"] == "ai_generated" and prov["prompt_version"] == "brand_diagnosis_v1"
    assert prov["model"] == "scripted-1" and prov["prompt_hash"] == BRAND_DIAGNOSIS_V1.prompt_hash
    assert "system" not in prov and BRAND_DIAGNOSIS_V1.system[:20] not in json.dumps(prov)
    assert len(brain_repo.recommendations) == 2
    assert brain_repo.learning_signals == []


def test_diagnose_caps_at_ten_and_counts_the_rest(
    b3_client: TestClient,
    gateway: ScriptedGateway,
    brain_repo: FakeBrandBrainRepository,
    owner_headers: dict[str, str],
) -> None:
    m = _evidence(brain_repo, "measured")
    gateway.payload = {
        "summary": "s",
        "strengths": [],
        "gaps": [],
        "recommendations": [
            {"kind": "gap_action", "title": f"r{i}", "rationale": "x", "evidence_ids": [str(m.id)]}
            for i in range(13)
        ],
    }
    res = b3_client.post(f"{BRAIN}/diagnose", headers=owner_headers)
    assert res.status_code == 200, res.text
    assert len(res.json()["recommendations"]) == 10
    assert res.json()["recommendations_dropped"] == 3


def test_diagnose_without_evidence_still_answers_but_stores_nothing(
    b3_client: TestClient, gateway: ScriptedGateway, owner_headers: dict[str, str]
) -> None:
    gateway.payload = {
        "summary": "لا أدلة بعد",
        "strengths": [],
        "gaps": [{"text": "لا نقيس شيئاً", "evidence_ids": [str(uuid4())]}],
        "recommendations": [
            {"kind": "gap_action", "title": "اجمع الأدلة", "rationale": "r", "evidence_ids": []}
        ],
    }
    res = b3_client.post(f"{BRAIN}/diagnose", headers=owner_headers)
    assert res.status_code == 200, res.text
    assert res.json()["recommendations"] == [] and res.json()["recommendations_dropped"] == 1
    assert res.json()["gaps"] == [] and res.json()["evidence_used"] == []
    assert "(no evidence collected yet)" in gateway.calls[0].user


# ------------------------------------------------------- SPRINT-11 A-7 (B5) --
def test_a7_unknown_recommendation_kind_is_dropped_not_502(
    b3_client: TestClient,
    gateway: ScriptedGateway,
    brain_repo: FakeBrandBrainRepository,
    owner_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A-7 (B5): three recommendations, one with ``kind="content_plan_item"`` ⇒ 200 with the
    two valid ones stored, ``dropped == 1`` (and the legacy ``recommendations_dropped``), a
    WARNING logged — the diagnosis is not lost to one bad row."""
    m = _evidence(brain_repo, "measured")
    ok = {"kind": "gap_action", "title": "ok", "rationale": "x", "evidence_ids": [str(m.id)]}
    gateway.payload = {
        "summary": "s",
        "strengths": [],
        "gaps": [],
        "recommendations": [
            ok,
            {**ok, "kind": "content_plan_item", "title": "unknown kind"},
            {**ok, "kind": "tone_adjustment", "title": "ok2"},
        ],
    }
    with caplog.at_level("WARNING", logger="src.modules.brand_brain.advisor"):
        res = b3_client.post(f"{BRAIN}/diagnose", headers=owner_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert [r["title"] for r in body["recommendations"]] == ["ok", "ok2"]
    assert body["dropped"] == 1 and body["recommendations_dropped"] == 1
    assert len(brain_repo.recommendations) == 2
    assert any("dropped recommendation" in r.message for r in caplog.records)


def test_a7_all_recommendations_invalid_is_200_with_empty_list(
    b3_client: TestClient,
    gateway: ScriptedGateway,
    brain_repo: FakeBrandBrainRepository,
    owner_headers: dict[str, str],
) -> None:
    m = _evidence(brain_repo, "measured")
    gateway.payload = {
        "summary": "s",
        "strengths": [],
        "gaps": [],
        "recommendations": [
            {"kind": "nope", "title": "a", "rationale": "x", "evidence_ids": [str(m.id)]},
            "not even an object",
            {"kind": "gap_action", "title": "", "rationale": "x", "evidence_ids": [str(m.id)]},
        ],
    }
    res = b3_client.post(f"{BRAIN}/diagnose", headers=owner_headers)
    assert res.status_code == 200, res.text
    assert res.json()["recommendations"] == [] and res.json()["dropped"] == 3
    assert brain_repo.recommendations == {}


# ------------------------------------------------------- SPRINT-11 A-6 (B4) --
@pytest.mark.parametrize("template", ["", "   ", "x" * 601], ids=["empty", "blank", "overlong"])
def test_a6_empty_or_overlong_reply_template_is_dropped_at_parse(
    b3_client: TestClient,
    gateway: ScriptedGateway,
    brain_repo: FakeBrandBrainRepository,
    owner_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    template: str,
) -> None:
    m = _evidence(brain_repo, "measured")
    gateway.payload = {
        "summary": "s",
        "strengths": [],
        "gaps": [],
        "recommendations": [
            {
                "kind": "reply_template",
                "title": "t",
                "rationale": "x",
                "evidence_ids": [str(m.id)],
                "template": template,
            },
            {"kind": "gap_action", "title": "keep", "rationale": "x", "evidence_ids": [str(m.id)]},
        ],
    }
    with caplog.at_level("WARNING", logger="src.modules.brand_brain.advisor"):
        res = b3_client.post(f"{BRAIN}/diagnose", headers=owner_headers)
    assert res.status_code == 200, res.text
    assert [r["kind"] for r in res.json()["recommendations"]] == ["gap_action"]
    assert res.json()["dropped"] == 1
    assert any("dropped reply_template (empty)" in r.message for r in caplog.records)


def test_a6_reply_template_without_body_is_dropped_too(
    b3_client: TestClient,
    gateway: ScriptedGateway,
    brain_repo: FakeBrandBrainRepository,
    owner_headers: dict[str, str],
) -> None:
    m = _evidence(brain_repo, "measured")
    gateway.payload = {
        "summary": "s",
        "strengths": [],
        "gaps": [],
        "recommendations": [
            {"kind": "reply_template", "title": "t", "rationale": "x", "evidence_ids": [str(m.id)]}
        ],
    }
    res = b3_client.post(f"{BRAIN}/diagnose", headers=owner_headers)
    assert res.status_code == 200 and res.json()["recommendations"] == []
    assert res.json()["dropped"] == 1


def test_a6_concurrent_first_profile_insert_is_409_profile_conflict(
    b3_client: TestClient, brain_repo: FakeBrandBrainRepository, owner_headers: dict[str, str]
) -> None:
    """A-6 (B4): the brand has no profile yet; our ``insert_profile`` loses the race (unique
    violation) ⇒ 409 ``profile_conflict`` — not 500 — and the recommendation stays
    ``proposed`` so the caller can simply retry."""
    from sqlalchemy.exc import IntegrityError

    assert BRAND_ID not in brain_repo.profiles
    rid = _proposed(brain_repo, "reply_template", template="أهلاً")
    brain_repo.insert_profile_error = IntegrityError("brand_profiles_pkey", {}, Exception())
    res = b3_client.post(f"{BRAIN}/recommendations/{rid}/accept", headers=owner_headers)
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == {"error": "profile_conflict"}
    assert brain_repo.recommendations[rid].status == "proposed"
    assert brain_repo.learning_signals == []

    retry = b3_client.post(f"{BRAIN}/recommendations/{rid}/accept", headers=owner_headers)
    assert retry.status_code == 200, retry.text
    assert brain_repo.profiles[BRAND_ID].tone.reply_templates == ["أهلاً"]


@pytest.mark.parametrize(
    "raw",
    ['{"summary": 5}', '{"strengths": "x"}', "[]", '{"summary": "ok", "recommendations": 1}'],
)
def test_diagnose_schema_mismatch_is_502_invalid_output(
    b3_client: TestClient, gateway: ScriptedGateway, owner_headers: dict[str, str], raw: str
) -> None:
    gateway.raw = raw
    res = b3_client.post(f"{BRAIN}/diagnose", headers=owner_headers)
    assert res.status_code == 502, res.text
    assert res.json()["detail"] == {"error": "ai_invalid_output", "category": "invalid_output"}


def test_diagnose_maps_ai_errors_with_the_content_table(
    b3_client: TestClient, gateway: ScriptedGateway, owner_headers: dict[str, str]
) -> None:
    gateway.error = AIError(ErrorCategory.RATE_LIMITED, retry_after_ms=2500)
    res = b3_client.post(f"{BRAIN}/diagnose", headers=owner_headers)
    assert res.status_code == 429 and res.headers["Retry-After"] == "3"
    assert res.json()["detail"]["error"] == "ai_rate_limited"
    gateway.error = AIError(ErrorCategory.INVALID_CREDENTIAL)
    assert b3_client.post(f"{BRAIN}/diagnose", headers=owner_headers).status_code == 503


def test_diagnose_roles_rate_limit_and_budget(
    b3_client: TestClient,
    brain_app: FastAPI,
    gateway: ScriptedGateway,
    brain_repo: FakeBrandBrainRepository,
    content_repo: FakeContentRepository,
    limiter: FakeRateLimiter,
    owner_headers: dict[str, str],
    manager_headers: dict[str, str],
    viewer_headers: dict[str, str],
) -> None:
    _evidence(brain_repo, "measured")
    gateway.payload = {"summary": "s", "strengths": [], "gaps": [], "recommendations": []}
    assert b3_client.post(f"{BRAIN}/diagnose", headers=viewer_headers).status_code == 403
    assert b3_client.post(f"{BRAIN}/diagnose", headers=manager_headers).status_code == 200
    assert ("brain:diagnose", str(BRAND_ID)) in limiter.hits
    calls = len(gateway.calls)
    limiter.reject_after = len(limiter.hits)
    res = b3_client.post(f"{BRAIN}/diagnose", headers=owner_headers)
    assert res.status_code == 429 and res.json()["detail"]["error"] == "brain_rate_limited"
    assert res.headers["Retry-After"] == "3600" and len(gateway.calls) == calls
    limiter.reject_after = None
    # shared budget (A-4) exhausted ⇒ 429 generation_budget_exceeded, no model call
    content_repo.token_usage_override = brain_app.state.settings.ai_daily_token_budget_per_brand
    res = b3_client.post(f"{BRAIN}/diagnose", headers=owner_headers)
    assert res.status_code == 429, res.text
    assert res.json()["detail"]["error"] == "generation_budget_exceeded"
    assert "Retry-After" in res.headers and len(gateway.calls) == calls
    assert (
        res.json()["detail"]["budget"] == brain_app.state.settings.ai_daily_token_budget_per_brand
    )


def test_diagnose_with_user_key_header_marks_credential_mode(
    b3_client: TestClient, gateway: ScriptedGateway, owner_headers: dict[str, str]
) -> None:
    gateway.payload = {"summary": "s", "strengths": [], "gaps": [], "recommendations": []}
    # Assembled from short chunks so no secret-shaped literal exists in the source (gitleaks).
    user_header = "".join(("usr", "-", "hdr", "-", "0123", "4567", "abcd"))
    res = b3_client.post(f"{BRAIN}/diagnose", headers={**owner_headers, "X-AI-Key": user_header})
    assert res.status_code == 200, res.text
    assert gateway.calls[0].credential_mode == "user_key"
    assert user_header not in res.text


# ------------------------------------------------------------ content plan --
def _plan_payload(ev: Evidence, n: int = 3) -> dict[str, Any]:
    return {
        "items": [
            {
                "title": f"منشور {i} [مُقاس]",
                "channel": "facebook" if i % 2 == 0 else "instagram",
                "body": f"نص المنشور {i} [estimated] مع دعوة إلى فعل.",
                "rationale": "لأن الجمهور يتفاعل صباحاً",
                "evidence_ids": [str(ev.id)],
            }
            for i in range(n)
        ]
    }


def test_content_plan_creates_drafts_only_with_ai_provenance(
    b3_client: TestClient,
    gateway: ScriptedGateway,
    brain_repo: FakeBrandBrainRepository,
    content_repo: FakeContentRepository,
    owner_headers: dict[str, str],
) -> None:
    ev = _evidence(brain_repo, "inferred")
    gateway.payload = _plan_payload(ev, 3)
    gateway.payload["items"].append(  # unknown channel ⇒ dropped
        {"title": "t", "channel": "tiktok", "body": "b", "evidence_ids": [str(ev.id)]}
    )
    gateway.payload["items"].append(  # no evidence ⇒ dropped
        {"title": "t", "channel": "facebook", "body": "b", "evidence_ids": []}
    )
    res = b3_client.post(f"{BRAIN}/content-plan", headers=owner_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["items"]) == 3 and body["items_dropped"] == 2
    assert body["prompt_version"] == "content_plan_v1"
    assert gateway.calls[0].purpose == "content_plan" and gateway.calls[0].json_mode is True
    assert {i["channel"] for i in body["items"]} == {"facebook_page", "instagram"}
    assert all(i["status"] == "draft" for i in body["items"])
    for planned in body["items"]:
        item = content_repo.items[UUID(planned["item_id"])]
        assert item.status.value == "draft"
        assert content_repo.events[item.id] == []  # approval_events = 0
        revision = sorted(content_repo.revisions[item.id], key=lambda r: r.revision_no)[-1]
        assert revision.provenance["kind"] == "ai_generated"
        assert revision.provenance["recommendation_id"] == planned["recommendation_id"]
        assert revision.provenance["prompt_version"] == "content_plan_v1"
        assert revision.provenance["prompt_hash"] == CONTENT_PLAN_V1.prompt_hash
        assert "[estimated]" not in revision.body and "[مُقاس]" not in planned["title"]
        rec = brain_repo.recommendations[UUID(planned["recommendation_id"])]
        assert rec.kind == "content_plan_item" and rec.status == "materialized"
        assert rec.materialized_item_id == item.id and rec.decided_by_user_id == OWNER_USER_ID
        assert rec.rationale.endswith("[مُستنتج]")
    signals = brain_repo.learning_signals
    assert len(signals) == 3 and {s.signal for s in signals} == {"materialized"}
    assert all(s.purpose == "content_plan" and s.model == "scripted-1" for s in signals)
    assert all("body" not in s.features and "title" not in s.features for s in signals)


def test_content_plan_caps_at_seven(
    b3_client: TestClient,
    gateway: ScriptedGateway,
    brain_repo: FakeBrandBrainRepository,
    content_repo: FakeContentRepository,
    owner_headers: dict[str, str],
) -> None:
    ev = _evidence(brain_repo, "measured")
    gateway.payload = _plan_payload(ev, 9)
    res = b3_client.post(f"{BRAIN}/content-plan", headers=owner_headers)
    assert res.status_code == 200, res.text
    assert len(res.json()["items"]) == 7 and res.json()["items_dropped"] == 2
    assert len([i for i in content_repo.items.values() if i.brand_id == BRAND_ID]) == 7


def test_content_plan_invalid_output_and_roles(
    b3_client: TestClient,
    gateway: ScriptedGateway,
    content_repo: FakeContentRepository,
    owner_headers: dict[str, str],
    viewer_headers: dict[str, str],
    limiter: FakeRateLimiter,
) -> None:
    gateway.raw = '{"items": "nope"}'
    assert b3_client.post(f"{BRAIN}/content-plan", headers=viewer_headers).status_code == 403
    res = b3_client.post(f"{BRAIN}/content-plan", headers=owner_headers)
    assert res.status_code == 502 and res.json()["detail"]["error"] == "ai_invalid_output"
    assert not content_repo.items
    assert ("brain:content-plan", str(BRAND_ID)) in limiter.hits


# ---------------------------------------------------------- accept/dismiss --
def _proposed(repo: FakeBrandBrainRepository, kind: str = "gap_action", **extra: Any) -> UUID:
    ev = _evidence(repo, "measured")
    rec = Recommendation(
        brand_id=BRAND_ID,
        kind=kind,  # type: ignore[arg-type]
        title="t",
        rationale="r [مُقاس]",
        evidence_ids=[ev.id],
        provenance={
            "kind": "ai_generated",
            "prompt_version": "brand_diagnosis_v1",
            "model": "scripted-1",
            "credential_mode": "user_key",
            **extra,
        },
        created_at=NOW,
    )
    repo.recommendations[rec.id] = rec
    return rec.id


def test_accept_and_dismiss_write_decisions_and_learning_signals(
    b3_client: TestClient,
    brain_repo: FakeBrandBrainRepository,
    owner_headers: dict[str, str],
    manager_headers: dict[str, str],
) -> None:
    a = _proposed(brain_repo)
    d = _proposed(brain_repo)
    res = b3_client.post(f"{BRAIN}/recommendations/{a}/accept", headers=owner_headers)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "accepted"
    assert res.json()["decided_by_user_id"] == str(OWNER_USER_ID)
    assert res.json()["decided_at"] is not None
    res = b3_client.post(f"{BRAIN}/recommendations/{d}/dismiss", headers=manager_headers)
    assert res.status_code == 200 and res.json()["status"] == "dismissed"
    assert brain_repo.locked_recommendations == [a, d]
    signals = brain_repo.learning_signals
    assert [s.signal for s in signals] == ["accepted", "dismissed"]
    assert signals[0].subject_id == a and signals[0].purpose == "brand_diagnosis"
    assert signals[0].prompt_version == "brand_diagnosis_v1"
    assert signals[0].credential_mode == "user_key" and signals[0].model == "scripted-1"
    assert set(signals[0].features) == {
        "kind",
        "evidence_count",
        "measured",
        "inferred",
        "estimated",
        "rationale_chars",
    }
    res = b3_client.post(f"{BRAIN}/recommendations/{a}/dismiss", headers=owner_headers)
    assert res.status_code == 409
    assert res.json()["detail"] == {
        "error": "recommendation_already_decided",
        "status": "accepted",
    }
    assert len(brain_repo.learning_signals) == 2


def test_accept_reply_template_lands_in_profile_after_accept_only(
    b3_client: TestClient,
    brain_repo: FakeBrandBrainRepository,
    owner_headers: dict[str, str],
) -> None:
    template = "أهلاً! استلمنا طلبك وسنعود إليك قريباً."
    rid = _proposed(brain_repo, "reply_template", template=template)
    assert brain_repo.profiles.get(BRAND_ID) is None
    prof = b3_client.get(f"{BRAIN}/profile", headers=owner_headers).json()
    assert prof["tone"]["reply_templates"] == []
    res = b3_client.post(f"{BRAIN}/recommendations/{rid}/accept", headers=owner_headers)
    assert res.status_code == 200, res.text
    prof = b3_client.get(f"{BRAIN}/profile", headers=owner_headers).json()
    assert prof["tone"]["reply_templates"] == [template]
    assert prof["version"] == 1  # first write creates the row at version 1
    assert brain_repo.revisions[-1].source == "ai_suggested"
    assert brain_repo.revisions[-1].changed_by_user_id == OWNER_USER_ID
    for i in range(11):
        rid = _proposed(brain_repo, "reply_template", template=f"قالب {i}")
        assert (
            b3_client.post(f"{BRAIN}/recommendations/{rid}/accept", headers=owner_headers)
        ).status_code == 200
    profile = brain_repo.profiles[BRAND_ID]
    assert len(profile.tone.reply_templates) == 10
    assert profile.tone.reply_templates[-1] == "قالب 10"
    assert template not in profile.tone.reply_templates
    assert profile.version == 12
    put = b3_client.put(
        f"{BRAIN}/profile",
        json={"products": [], "audience": {}, "tone": {"voice": "warm"}, "goals": []},
        headers={**owner_headers, "If-Match": "12"},
    )
    assert put.status_code == 200, put.text
    assert len(put.json()["tone"]["reply_templates"]) == 10


def test_accept_reply_template_without_template_falls_back_to_title(
    b3_client: TestClient, brain_repo: FakeBrandBrainRepository, owner_headers: dict[str, str]
) -> None:
    rid = _proposed(brain_repo, "reply_template")
    assert (
        b3_client.post(f"{BRAIN}/recommendations/{rid}/accept", headers=owner_headers)
    ).status_code == 200
    assert brain_repo.profiles[BRAND_ID].tone.reply_templates == ["t"]


def test_decisions_require_fresh_session_role_and_existing_row(
    b3_client: TestClient,
    brain_repo: FakeBrandBrainRepository,
    owner_headers: dict[str, str],
    viewer_headers: dict[str, str],
    store: FakeStore,
) -> None:
    rid = _proposed(brain_repo)
    assert (
        b3_client.post(f"{BRAIN}/recommendations/{rid}/accept", headers=viewer_headers)
    ).status_code == 403
    assert (
        b3_client.post(f"{BRAIN}/recommendations/{uuid4()}/accept", headers=owner_headers)
    ).status_code == 404
    assert (
        b3_client.post(f"{BRAIN}/recommendations/{uuid4()}/dismiss", headers=owner_headers)
    ).status_code == 404
    for sid, session in list(store.sessions.items()):
        if session.user_id == OWNER_USER_ID:
            store.sessions[sid] = session.model_copy(update={"revoked_at": NOW})
    assert (
        b3_client.post(f"{BRAIN}/recommendations/{rid}/accept", headers=owner_headers)
    ).status_code == 401
    assert brain_repo.recommendations[rid].status == "proposed"
    assert brain_repo.learning_signals == []


def test_list_recommendations_filters_and_projects_provenance(
    b3_client: TestClient,
    brain_repo: FakeBrandBrainRepository,
    owner_headers: dict[str, str],
    viewer_headers: dict[str, str],
) -> None:
    a = _proposed(brain_repo)
    b = _proposed(brain_repo)
    b3_client.post(f"{BRAIN}/recommendations/{b}/dismiss", headers=owner_headers)
    res = b3_client.get(f"{BRAIN}/recommendations", headers=owner_headers)
    assert res.status_code == 200 and {r["id"] for r in res.json()["items"]} == {str(a), str(b)}
    assert res.json()["limit"] == 50
    res = b3_client.get(f"{BRAIN}/recommendations?status=proposed", headers=owner_headers)
    assert [r["id"] for r in res.json()["items"]] == [str(a)]
    assert "model" in res.json()["items"][0]["provenance"]
    res = b3_client.get(f"{BRAIN}/recommendations?status=proposed&limit=1", headers=viewer_headers)
    assert res.status_code == 200, res.text
    assert set(res.json()["items"][0]["provenance"]) <= {"kind", "generated_at", "prompt_version"}
    assert res.json()["limit"] == 1
    bogus = b3_client.get(f"{BRAIN}/recommendations?status=bogus", headers=owner_headers)
    assert bogus.status_code == 422


# --------------------------------------------------------- service edge cases --
def _advisor(repo: FakeBrandBrainRepository, limiter: FakeRateLimiter) -> BrandAdvisor:
    from src.modules.ai_gateway.budget import DailyBudget, NullBudgetUsageReader

    return BrandAdvisor(
        repo,
        gateway=ScriptedGateway(),
        limiter=limiter,
        budget=DailyBudget(1000),
        budget_reader=NullBudgetUsageReader(),
    )


async def test_advisor_cas_race_on_decide_is_409(
    brain_repo: FakeBrandBrainRepository, limiter: FakeRateLimiter
) -> None:
    from src.modules.brand_brain.exceptions import RecommendationAlreadyDecidedError

    rid = _proposed(brain_repo)
    advisor = _advisor(brain_repo, limiter)
    original = brain_repo.decide_recommendation

    async def _lost_race(*args: Any, **kwargs: Any) -> bool:
        await original(*args, **{**kwargs, "new_status": "dismissed"})
        return False  # another transaction won between our lock and our UPDATE

    brain_repo.decide_recommendation = _lost_race  # type: ignore[method-assign]
    with pytest.raises(RecommendationAlreadyDecidedError):
        await advisor.accept(BRAND_ID, rid, actor_user_id=OWNER_USER_ID)
    with pytest.raises(RecommendationAlreadyDecidedError):
        await advisor.dismiss(BRAND_ID, _proposed(brain_repo), actor_user_id=OWNER_USER_ID)
    assert brain_repo.learning_signals == []


async def test_advisor_reply_template_cas_conflict_is_409(
    brain_repo: FakeBrandBrainRepository, limiter: FakeRateLimiter
) -> None:
    from src.modules.brand_brain.domain.models import BrandProfile
    from src.modules.brand_brain.exceptions import RecommendationAlreadyDecidedError

    brain_repo.profiles[BRAND_ID] = BrandProfile(brand_id=BRAND_ID, version=3, updated_at=NOW)
    rid = _proposed(brain_repo, "reply_template", template="x")
    advisor = _advisor(brain_repo, limiter)

    async def _stale(*args: Any, **kwargs: Any) -> bool:
        return False

    brain_repo.update_profile_cas = _stale  # type: ignore[method-assign]
    with pytest.raises(RecommendationAlreadyDecidedError):
        await advisor.accept(BRAND_ID, rid, actor_user_id=OWNER_USER_ID)
    assert brain_repo.recommendations[rid].status == "proposed"


def test_recommendation_model_invariants() -> None:
    from pydantic import ValidationError

    base: dict[str, Any] = {
        "brand_id": BRAND_ID,
        "kind": "gap_action",
        "title": "t",
        "rationale": "r",
        "evidence_ids": [uuid4()],
        "created_at": NOW,
    }
    Recommendation(**base)
    with pytest.raises(ValidationError):
        Recommendation(**{**base, "evidence_ids": []})
    with pytest.raises(ValidationError):  # decision without actor
        Recommendation(**{**base, "status": "accepted"})
    with pytest.raises(ValidationError):  # actor without decision
        Recommendation(**{**base, "decided_by_user_id": OWNER_USER_ID, "decided_at": NOW})
    with pytest.raises(ValidationError):  # materialized id on a proposed row
        Recommendation(**{**base, "materialized_item_id": uuid4()})
    ok = Recommendation(
        **{
            **base,
            "status": "materialized",
            "decided_by_user_id": OWNER_USER_ID,
            "decided_at": NOW,
            "materialized_item_id": uuid4(),
        }
    )
    assert ok.status == "materialized"
