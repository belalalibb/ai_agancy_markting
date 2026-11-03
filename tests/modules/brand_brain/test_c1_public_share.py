"""SPRINT-12 C-1 — public diagnosis link ``/d/{token}`` (share / revoke / public card)."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from src.modules.brand_brain.domain.models import Evidence, EvidenceKindsSummary, Recommendation
from src.modules.brand_brain.share import (
    PUBLIC_BUCKET,
    PUBLIC_LIMIT,
    build_public_share,
    evidence_grade,
    hash_token,
    scrub_title,
)

from tests.conftest import BRAND2_ID, BRAND_ID
from tests.fakes import FakeBrandBrainRepository, FakeRateLimiter
from tests.modules.brand_brain.conftest import BRAIN

pytestmark = pytest.mark.contract

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
FORBIDDEN = ("competitor", "phone", "email", "http", "wa_id", "evidence_url")
_SOURCE = {"measured": "internal_db", "inferred": "model_inference", "estimated": "public_web"}
_METRIC = {
    "measured": "posting_frequency_7d",
    "inferred": "tone_match_score",
    "estimated": "public_page_summary",
}


def _evidence(repo: FakeBrandBrainRepository, kind: str, brand_id: UUID = BRAND_ID) -> Evidence:
    ev = Evidence(
        brand_id=brand_id,
        subject_kind="brand",
        subject_id=brand_id,
        evidence_kind=kind,  # type: ignore[arg-type]
        metric=_METRIC[kind],
        value={"count": 3},
        confidence=Decimal("0.9"),
        source_kind=_SOURCE[kind],  # type: ignore[arg-type]
        source_ref=None,
        observed_at=NOW,
        created_at=NOW,
    )
    repo.evidence[ev.id] = ev
    return ev


def _rec(
    repo: FakeBrandBrainRepository,
    *,
    title: str,
    kind: str = "gap_action",
    grade: str = "measured",
    brand_id: UUID = BRAND_ID,
    created_at: datetime = NOW,
) -> Recommendation:
    ev = _evidence(repo, grade, brand_id)
    rec = Recommendation(
        brand_id=brand_id,
        kind=kind,  # type: ignore[arg-type]
        title=title,
        rationale="Competitor Acme posts daily at https://acme.example; phone +20 100 000 0000",
        evidence_ids=[ev.id],
        evidence_kinds_summary=EvidenceKindsSummary.of([ev]),
        provenance={
            "kind": "ai_generated",
            "generated_at": NOW.isoformat(),
            "prompt_version": "brand_diagnosis_v1",
            "provider": "openai",
            "model": "gpt-x",
            "credential_mode": "platform",
            "evidence_url": "https://private.example/evidence",
        },
        created_at=created_at,
    )
    repo.recommendations[rec.id] = rec
    return rec


def _seed(repo: FakeBrandBrainRepository) -> Recommendation:
    anchor = _rec(repo, title="Reply within 2 hours to 3 open chats", grade="measured")
    for i in range(6):
        _rec(
            repo,
            title=f"Post about product {i} — see www.competitor-{i}.example",
            kind="content_plan_item",
            grade="inferred" if i % 2 else "estimated",
            created_at=NOW.replace(minute=i),
        )
    _rec(repo, title="Other brand's secret", brand_id=BRAND2_ID)
    return anchor


def _share(client: TestClient, headers: dict[str, str], rec_id: UUID) -> Any:
    return client.post(f"{BRAIN}/diagnoses/{rec_id}/share", headers=headers)


def _token_of(url: str) -> str:
    return url.rsplit("/d/", 1)[1]


# ------------------------------------------------------------------ share --
def test_owner_shares_once_only_hash_is_stored_and_resharing_rotates(
    brain_client: TestClient, brain_repo: FakeBrandBrainRepository, owner_headers: dict[str, str]
) -> None:
    anchor = _seed(brain_repo)
    res = _share(brain_client, owner_headers, anchor.id)
    assert res.status_code == 201, res.text
    body = res.json()
    token = _token_of(body["url"])
    assert body["url"].startswith("http://testserver/api/v1/d/")
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
    stored_hash, shared_at = brain_repo.shares[anchor.id]
    assert stored_hash == hashlib.sha256(token.encode()).hexdigest() == hash_token(token)
    # the token itself is never stored anywhere in the repository
    stored = json.dumps({str(k): [str(x) for x in v] for k, v in brain_repo.shares.items()})
    assert token not in stored
    assert shared_at.isoformat().startswith("20")
    # re-share ⇒ a new token; the old link dies
    again = _share(brain_client, owner_headers, anchor.id)
    assert again.status_code == 201
    new_token = _token_of(again.json()["url"])
    assert new_token != token
    assert brain_client.get(f"/api/v1/d/{token}").status_code == 404
    assert brain_client.get(f"/api/v1/d/{new_token}").status_code == 200


def test_manager_and_viewer_cannot_share_or_revoke(
    brain_client: TestClient,
    brain_repo: FakeBrandBrainRepository,
    manager_headers: dict[str, str],
    viewer_headers: dict[str, str],
) -> None:
    anchor = _seed(brain_repo)
    for headers in (manager_headers, viewer_headers):
        res = _share(brain_client, headers, anchor.id)
        assert res.status_code == 403 and res.json()["detail"]["error"] == "insufficient_role"
        res = brain_client.delete(f"{BRAIN}/diagnoses/{anchor.id}/share", headers=headers)
        assert res.status_code == 403
    assert brain_repo.shares == {}


def test_share_unknown_or_foreign_recommendation_is_404(
    brain_client: TestClient, brain_repo: FakeBrandBrainRepository, owner_headers: dict[str, str]
) -> None:
    _seed(brain_repo)
    foreign = next(r for r in brain_repo.recommendations.values() if r.brand_id == BRAND2_ID)
    for rec_id in (uuid4(), foreign.id):
        res = _share(brain_client, owner_headers, rec_id)
        assert res.status_code == 404, res.text
        assert res.json()["detail"]["error"] == "recommendation_not_found"
        res = brain_client.delete(f"{BRAIN}/diagnoses/{rec_id}/share", headers=owner_headers)
        assert res.status_code == 404
    assert brain_repo.shares == {}


def test_revoke_makes_the_public_link_404(
    brain_client: TestClient, brain_repo: FakeBrandBrainRepository, owner_headers: dict[str, str]
) -> None:
    anchor = _seed(brain_repo)
    token = _token_of(_share(brain_client, owner_headers, anchor.id).json()["url"])
    assert brain_client.get(f"/api/v1/d/{token}").status_code == 200
    res = brain_client.delete(f"{BRAIN}/diagnoses/{anchor.id}/share", headers=owner_headers)
    assert res.status_code == 204 and res.content == b""
    assert anchor.id not in brain_repo.shares
    res = brain_client.get(f"/api/v1/d/{token}")
    assert res.status_code == 404 and res.json()["detail"]["error"] == "diagnosis_not_found"


# ----------------------------------------------------------------- public --
def test_public_card_is_watermarked_capped_and_leaks_nothing(
    brain_client: TestClient,
    brain_repo: FakeBrandBrainRepository,
    owner_headers: dict[str, str],
    limiter: FakeRateLimiter,
) -> None:
    anchor = _seed(brain_repo)
    token = _token_of(_share(brain_client, owner_headers, anchor.id).json()["url"])
    limiter.hits.clear()
    res = brain_client.get(f"/api/v1/d/{token}")  # no Authorization header at all
    assert res.status_code == 200, res.text
    assert res.headers["cache-control"] == "public, max-age=300"
    body = res.json()
    assert set(body) == {
        "title",
        "recommendations",
        "evidence_grade",
        "generated_at",
        "watermark",
        "cta_url",
    }
    assert body["watermark"] == "Generated by AI Growth OS"
    assert body["cta_url"] == "http://testserver/"
    assert body["title"] == "Reply within hours to open chats"  # numbers scrubbed
    assert 3 <= len(body["recommendations"]) <= 5
    assert body["recommendations"][0]["kind"] == "gap_action"
    assert body["evidence_grade"] == "measured"
    assert {r["evidence_grade"] for r in body["recommendations"]} <= {
        "measured",
        "inferred",
        "estimated",
    }
    # the forbidden list (§3-8) — grep over the JSON minus the CTA (our own base URL)
    raw = res.text.lower().replace(body["cta_url"].lower(), "")
    for needle in FORBIDDEN:
        assert needle not in raw, needle
    assert "acme" not in raw and "secret" not in raw and "example" not in raw
    assert "rationale" not in raw and "provenance" not in raw and "evidence_ids" not in raw
    assert limiter.hits == [(PUBLIC_BUCKET, "testclient")]


def test_public_card_rate_limit_61st_request_is_429_with_retry_after(
    brain_client: TestClient,
    brain_repo: FakeBrandBrainRepository,
    owner_headers: dict[str, str],
    limiter: FakeRateLimiter,
) -> None:
    anchor = _seed(brain_repo)
    token = _token_of(_share(brain_client, owner_headers, anchor.id).json()["url"])
    limiter.hits.clear()
    limiter.reject_after = PUBLIC_LIMIT
    for _ in range(PUBLIC_LIMIT):
        assert brain_client.get(f"/api/v1/d/{token}").status_code == 200
    res = brain_client.get(f"/api/v1/d/{token}")
    assert res.status_code == 429
    assert res.json()["detail"] == {"error": "rate_limited"}
    assert res.headers["retry-after"] == "60"
    assert "cache-control" not in res.headers


def test_public_card_unknown_or_malformed_token(brain_client: TestClient) -> None:
    res = brain_client.get(f"/api/v1/d/{'A' * 43}")
    assert res.status_code == 404 and res.json()["detail"]["error"] == "diagnosis_not_found"
    assert brain_client.get("/api/v1/d/short").status_code == 422
    assert brain_client.get(f"/api/v1/d/{'x' * 43}?x=1").status_code == 404
    assert brain_client.get(f"/api/v1/d/{'A' * 20}%20{'B' * 22}").status_code == 422


# ---------------------------------------------------------------- helpers --
def test_scrub_title_and_grades() -> None:
    assert scrub_title("Call +20 100 000 0000 or mail a@b.co via https://x.y/z @handle") == (
        "Call or mail via"
    )
    assert scrub_title("12345") == "Recommendation"
    assert len(scrub_title("w" * 200)) == 80
    repo = FakeBrandBrainRepository()
    measured = _rec(repo, title="m", grade="measured")
    inferred = _rec(repo, title="i", grade="inferred")
    estimated = _rec(repo, title="e", grade="estimated")
    assert evidence_grade(measured) == "measured"
    assert evidence_grade(inferred) == "inferred"
    assert evidence_grade(estimated) == "estimated"
    empty = measured.model_copy(update={"evidence_kinds_summary": EvidenceKindsSummary()})
    assert evidence_grade(empty) == "estimated"
    card = build_public_share(estimated, [inferred, measured], app_name="X", cta_url="/")
    assert card.evidence_grade == "measured" and card.watermark == "Generated by X"
    assert [r.title for r in card.recommendations] == ["e", "i", "m"]
    alone = build_public_share(estimated, [estimated], app_name="X", cta_url="/")
    assert len(alone.recommendations) == 1 and alone.evidence_grade == "estimated"
    assert alone.generated_at == NOW  # the anchor's time — no actor on the card


# ------------------------------------------------- SPRINT-12.1 A-1 (S-1) --
def _dismiss(repo: FakeBrandBrainRepository, rec: Recommendation) -> Recommendation:
    """What ``decide_recommendation`` leaves behind: the same row with a decision stamp."""
    dismissed = rec.model_copy(
        update={"status": "dismissed", "decided_by_user_id": uuid4(), "decided_at": NOW}
    )
    repo.recommendations[rec.id] = dismissed
    return dismissed


def test_a1_dismissed_siblings_never_appear_in_the_public_card(
    brain_client: TestClient, brain_repo: FakeBrandBrainRepository, owner_headers: dict[str, str]
) -> None:
    """Review S-1 / probe P2: the owner rejected the newest sibling — the card must not show
    it, even though the repository returns every status."""
    anchor = _seed(brain_repo)
    newest = max(
        (r for r in brain_repo.recommendations.values() if r.brand_id == BRAND_ID),
        key=lambda r: r.created_at,
    )
    assert newest.id != anchor.id
    _dismiss(brain_repo, newest)
    token = _token_of(_share(brain_client, owner_headers, anchor.id).json()["url"])
    res = brain_client.get(f"/api/v1/d/{token}")
    assert res.status_code == 200, res.text
    card = res.json()
    titles = [r["title"] for r in card["recommendations"]]
    assert len(titles) == 5 and titles[0] == "Reply within hours to open chats"
    # the dismissed sibling (grade ``inferred``, i = 5) is gone; the four newest *proposed*
    # siblings fill the card — numbers are scrubbed, so we compare the evidence grades, which
    # alternate estimated/inferred by index: i = 4, 3, 2, 1 ⇒ estimated, inferred, estimated,
    # inferred. Had the dismissed row leaked, the sequence would start with ``inferred``.
    assert [r["evidence_grade"] for r in card["recommendations"][1:]] == [
        "estimated",
        "inferred",
        "estimated",
        "inferred",
    ]
    assert all(t.startswith("Post about product") for t in titles[1:])


def test_a1_sharing_a_dismissed_recommendation_is_409_and_stamps_nothing(
    brain_client: TestClient, brain_repo: FakeBrandBrainRepository, owner_headers: dict[str, str]
) -> None:
    anchor = _dismiss(brain_repo, _seed(brain_repo))
    res = _share(brain_client, owner_headers, anchor.id)
    assert res.status_code == 409, res.text
    assert res.json()["detail"] == {
        "error": "recommendation_dismissed",
        "message": "a dismissed recommendation cannot be shared publicly",
    }
    assert anchor.id not in brain_repo.shares  # no hash written, no link exists
    # unknown id is still 404 (checked before the status), VIEWER is still 403 (authorised first)
    assert _share(brain_client, owner_headers, uuid4()).status_code == 404


def test_a1_link_dies_when_its_anchor_is_dismissed_after_sharing(
    brain_client: TestClient, brain_repo: FakeBrandBrainRepository, owner_headers: dict[str, str]
) -> None:
    anchor = _seed(brain_repo)
    token = _token_of(_share(brain_client, owner_headers, anchor.id).json()["url"])
    assert brain_client.get(f"/api/v1/d/{token}").status_code == 200
    _dismiss(brain_repo, anchor)
    res = brain_client.get(f"/api/v1/d/{token}")
    assert res.status_code == 404 and res.json()["detail"]["error"] == "diagnosis_not_found"
    assert "cache-control" not in res.headers


def test_a1_build_public_share_filters_by_status_in_memory() -> None:
    """The projection itself is the last line of defence — independent of the repository."""
    repo = FakeBrandBrainRepository()
    anchor = _rec(repo, title="Anchor")
    kept = _rec(repo, title="Kept", kind="content_plan_item")
    dropped = _dismiss(repo, _rec(repo, title="Dropped", kind="content_plan_item"))
    card = build_public_share(anchor, [dropped, kept], app_name="X", cta_url="http://x/")
    assert [r.title for r in card.recommendations] == ["Anchor", "Kept"]
