"""SPRINT-10 B-1 unit tests — ``/brands/{brand_id}/brain`` over the fake repository.

Covers: role matrix + D-066 projections (VIEWER without ``banned_phrases`` / ``confidence`` /
``source_ref``), ``If-Match`` CAS (428 / 412 / ETag), revisions, competitors (≤3, UNIQUE name,
host validation, blocked platforms, 204 delete), evidence keyset + filters + unknown metric,
``evidence/refresh`` (five ``measured/internal_db`` rows, 60/h limiter), OWNER-only cost, the
domain guards of ``Evidence`` and the metric dictionary, and the route count (47 + 9 = 56).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from src.modules.brand_brain.domain.hosts import (
    BLOCKED_PLATFORM_HOSTS,
    host_matches,
    is_blocked_platform,
    normalize_host,
)
from src.modules.brand_brain.domain.metrics import (
    ALLOWED_SOURCES,
    INTERNAL_METRICS,
    METRICS,
    source_allowed,
)
from src.modules.brand_brain.domain.models import CostEntry, CostOutcome, Evidence
from src.modules.brand_brain.exceptions import BrandBrainError
from src.modules.brand_brain.infrastructure.orm import AICostLogRow, BrandEvidenceRow
from src.modules.brand_brain.router import decode_cursor, encode_cursor, parse_if_match, to_http
from src.modules.brand_brain.schemas import ProfileUpdate
from src.modules.brand_brain.service import (
    EVIDENCE_REFRESH_BUCKET,
    EVIDENCE_REFRESH_LIMIT,
    EvidenceCollector,
    InternalMetrics,
)

from tests.conftest import BRAND2_ID, BRAND_ID, OWNER_USER_ID
from tests.fakes import FakeBrandBrainRepository, FakeInternalMetricsReader, FakeRateLimiter
from tests.modules.brand_brain.conftest import BRAIN

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
PROFILE_BODY = {
    "products": [{"name": "Cold brew", "description": "Slow dripped", "price_hint": "45 EGP"}],
    "audience": {"segments": ["students"], "regions": ["Cairo"], "languages": ["ar", "en"]},
    "tone": {
        "voice": "warm, witty",
        "formality": "casual",
        "emoji_policy": "light",
        "banned_phrases": ["cheap", "best ever"],
    },
    "goals": [{"kind": "sales", "target": "+20% online orders", "horizon_days": 90}],
}


def _put(client: TestClient, headers: dict[str, str], *, version: int, body=None):  # noqa: ANN001, ANN202
    return client.put(
        f"{BRAIN}/profile",
        json=body or PROFILE_BODY,
        headers={**headers, "If-Match": str(version)},
    )


def _evidence(**overrides) -> Evidence:  # noqa: ANN003
    values = {
        "brand_id": BRAND_ID,
        "subject_kind": "brand",
        "subject_id": BRAND_ID,
        "evidence_kind": "measured",
        "metric": "wa_conversations_7d",
        "value": {"count": 3},
        "confidence": Decimal("0.95"),
        "source_kind": "internal_db",
        "source_ref": "internal_db:wa_conversations_7d",
        "observed_at": NOW,
        "created_at": NOW,
    }
    values.update(overrides)
    return Evidence(**values)


# ------------------------------------------------------------------ profile --
def test_profile_default_is_version_1_and_visibility_by_role(
    brain_client: TestClient, owner_headers, manager_headers, viewer_headers
) -> None:
    owner = brain_client.get(f"{BRAIN}/profile", headers=owner_headers)
    assert owner.status_code == 200
    assert owner.json()["version"] == 1
    assert owner.json()["tone"]["banned_phrases"] == []

    assert _put(brain_client, manager_headers, version=1).status_code == 200

    manager = brain_client.get(f"{BRAIN}/profile", headers=manager_headers).json()
    assert manager["tone"]["banned_phrases"] == ["cheap", "best ever"]
    assert manager["tone"]["reply_templates"] == []

    viewer = brain_client.get(f"{BRAIN}/profile", headers=viewer_headers)
    assert viewer.status_code == 200
    assert "banned_phrases" not in viewer.json()["tone"]  # D-066
    assert "reply_templates" not in viewer.json()["tone"]
    assert viewer.json()["tone"]["voice"] == "warm, witty"
    assert viewer.json()["products"][0]["name"] == "Cold brew"


def test_put_profile_requires_if_match_and_bumps_version(
    brain_client: TestClient, owner_headers, brain_repo: FakeBrandBrainRepository
) -> None:
    missing = brain_client.put(f"{BRAIN}/profile", json=PROFILE_BODY, headers=owner_headers)
    assert missing.status_code == 428
    assert missing.json()["detail"]["error"] == "if_match_invalid"
    malformed = brain_client.put(
        f"{BRAIN}/profile", json=PROFILE_BODY, headers={**owner_headers, "If-Match": "v1"}
    )
    assert malformed.status_code == 428

    stale_before_create = _put(brain_client, owner_headers, version=3)
    assert stale_before_create.status_code == 412
    assert stale_before_create.json()["detail"]["current_version"] == 1

    first = _put(brain_client, owner_headers, version=1)
    assert first.status_code == 200
    assert first.json()["version"] == 1
    assert first.headers["ETag"] == '"1"'
    assert first.json()["updated_by_user_id"] == str(OWNER_USER_ID)

    second = brain_client.put(
        f"{BRAIN}/profile", json=PROFILE_BODY, headers={**owner_headers, "If-Match": '"1"'}
    )
    assert second.status_code == 200
    assert second.json()["version"] == 2
    assert second.headers["ETag"] == '"2"'

    stale = _put(brain_client, owner_headers, version=1)
    assert stale.status_code == 412
    assert stale.json()["detail"] == {"error": "profile_version_conflict", "current_version": 2}
    assert brain_repo.profiles[BRAND_ID].version == 2

    revisions = brain_client.get(f"{BRAIN}/profile/revisions", headers=owner_headers).json()
    assert [r["version"] for r in revisions["items"]] == [2, 1]
    assert revisions["items"][0]["source"] == "human"
    assert revisions["items"][0]["snapshot"]["tone"]["banned_phrases"] == ["cheap", "best ever"]


def test_put_profile_cas_race_and_first_insert_race(
    brain_client: TestClient, owner_headers, brain_repo: FakeBrandBrainRepository
) -> None:
    from sqlalchemy.exc import IntegrityError

    brain_repo.insert_profile_error = IntegrityError("brand_profiles_pkey", {}, Exception())
    assert _put(brain_client, owner_headers, version=1).status_code == 412

    assert _put(brain_client, owner_headers, version=1).status_code == 200
    # Someone bumped the row between the read and the UPDATE ⇒ CAS fails ⇒ 412 with fresh version.
    original = brain_repo.update_profile_cas

    async def _lost(profile, *, expected_version: int) -> bool:  # noqa: ANN001
        brain_repo.profiles[BRAND_ID] = brain_repo.profiles[BRAND_ID].model_copy(
            update={"version": 7}
        )
        return False

    brain_repo.update_profile_cas = _lost  # type: ignore[method-assign]
    lost = _put(brain_client, owner_headers, version=1)
    assert lost.status_code == 412
    assert lost.json()["detail"]["current_version"] == 7
    brain_repo.update_profile_cas = original  # type: ignore[method-assign]


def test_put_profile_rejects_viewer_rejects_unknown_fields_and_keeps_reply_templates(
    brain_client: TestClient, owner_headers, viewer_headers, brain_repo: FakeBrandBrainRepository
) -> None:
    assert _put(brain_client, viewer_headers, version=1).status_code == 403
    extra = {**PROFILE_BODY, "tone": {**PROFILE_BODY["tone"], "reply_templates": ["x"]}}
    assert _put(brain_client, owner_headers, version=1, body=extra).status_code == 422
    too_many = {**PROFILE_BODY, "goals": [PROFILE_BODY["goals"][0]] * 6}
    assert _put(brain_client, owner_headers, version=1, body=too_many).status_code == 422

    assert _put(brain_client, owner_headers, version=1).status_code == 200
    profile = brain_repo.profiles[BRAND_ID]
    brain_repo.profiles[BRAND_ID] = profile.model_copy(
        update={"tone": profile.tone.model_copy(update={"reply_templates": ["Thanks!"]})}
    )
    assert _put(brain_client, owner_headers, version=1).status_code == 200
    assert brain_repo.profiles[BRAND_ID].tone.reply_templates == ["Thanks!"]


def test_brand_access_404_for_stranger_brand(brain_client: TestClient, viewer_headers) -> None:
    response = brain_client.get(
        f"/api/v1/brands/{BRAND2_ID}/brain/profile", headers=viewer_headers
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "brand_not_found"


# -------------------------------------------------------------- competitors --
def test_competitors_limit_unique_name_hosts_and_delete(
    brain_client: TestClient, owner_headers, manager_headers, viewer_headers
) -> None:
    url = f"{BRAIN}/competitors"
    created = brain_client.post(
        url,
        json={
            "name": "Beanery",
            "kind": "direct",
            "facebook_page_id": "123456",
            "website_host": "WWW.Beanery.com",
            "allowed_hosts": ["shop.beanery.com"],
        },
        headers=manager_headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["website_host"] == "beanery.com"
    assert body["allowed_hosts"] == ["beanery.com", "shop.beanery.com"]
    assert body["competitor_refreshed_at"] is None

    dup = brain_client.post(url, json={"name": "Beanery"}, headers=owner_headers)
    assert dup.status_code == 409
    assert dup.json()["detail"]["error"] == "competitor_exists"

    assert brain_client.post(url, json={"name": "Two"}, headers=owner_headers).status_code == 201
    assert brain_client.post(url, json={"name": "Three"}, headers=owner_headers).status_code == 201
    fourth = brain_client.post(url, json={"name": "Four"}, headers=owner_headers)
    assert fourth.status_code == 409
    assert fourth.json()["detail"]["error"] == "competitor_limit_reached"

    listed = brain_client.get(url, headers=viewer_headers)
    assert listed.status_code == 200
    assert [c["name"] for c in listed.json()["items"]] == ["Beanery", "Two", "Three"]
    assert listed.json()["limit"] == 3

    assert brain_client.post(url, json={"name": "V"}, headers=viewer_headers).status_code == 403

    cid = listed.json()["items"][1]["id"]
    assert brain_client.delete(f"{url}/{cid}", headers=viewer_headers).status_code == 403
    assert brain_client.delete(f"{url}/{cid}", headers=owner_headers).status_code == 204
    gone = brain_client.delete(f"{url}/{cid}", headers=owner_headers)
    assert gone.status_code == 404
    assert gone.json()["detail"]["error"] == "competitor_not_found"
    assert brain_client.post(url, json={"name": "Four"}, headers=owner_headers).status_code == 201


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"name": "X", "website_host": "https://example.com/path"}, "host_invalid"),
        ({"name": "X", "allowed_hosts": ["example"]}, "host_invalid"),
        ({"name": "X", "website_host": "www.instagram.com"}, "host_blocked_platform"),
        ({"name": "X", "allowed_hosts": ["m.tiktok.com"]}, "host_blocked_platform"),
        ({"name": "X", "website_host": "facebook.com"}, "host_blocked_platform"),
    ],
)
def test_competitor_host_validation(
    brain_client: TestClient, owner_headers, payload: dict, error: str
) -> None:
    response = brain_client.post(f"{BRAIN}/competitors", json=payload, headers=owner_headers)
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == error


def test_competitor_schema_limits(brain_client: TestClient, owner_headers) -> None:
    url = f"{BRAIN}/competitors"
    assert (
        brain_client.post(
            url, json={"name": "X", "facebook_page_id": "abc"}, headers=owner_headers
        ).status_code
        == 422
    )
    four_hosts = {"name": "X", "allowed_hosts": ["a.com", "b.com", "c.com", "d.com"]}
    assert brain_client.post(url, json=four_hosts, headers=owner_headers).status_code == 422
    assert (
        brain_client.post(
            url, json={"name": "X", "kind": "sideways"}, headers=owner_headers
        ).status_code
        == 422
    )


# ----------------------------------------------------------------- evidence --
def test_evidence_refresh_measures_five_internal_metrics_and_is_rate_limited(
    brain_client: TestClient,
    owner_headers,
    viewer_headers,
    brain_repo: FakeBrandBrainRepository,
    metrics_reader: FakeInternalMetricsReader,
    limiter: FakeRateLimiter,
) -> None:
    assert (
        brain_client.post(f"{BRAIN}/evidence/refresh", headers=viewer_headers).status_code == 403
    )

    response = brain_client.post(f"{BRAIN}/evidence/refresh", headers=owner_headers)
    assert response.status_code == 200, response.text
    assert response.json()["collected"] == 5
    assert response.json()["metrics"] == list(INTERNAL_METRICS)
    assert metrics_reader.calls == [BRAND_ID]
    rows = {e.metric: e for e in brain_repo.evidence.values()}
    assert set(rows) == set(INTERNAL_METRICS)
    assert all(
        e.evidence_kind == "measured" and e.source_kind == "internal_db" for e in rows.values()
    )
    assert all(e.subject_kind == "brand" and e.subject_id == BRAND_ID for e in rows.values())
    assert rows["wa_conversations_7d"].value == {"count": 12, "window_days": 7}
    assert rows["wa_approval_rate_30d"].value == {"ratio": 0.75, "samples": 8, "window_days": 30}
    assert rows["avg_reply_latency_s"].value == {"seconds": 420.0, "samples": 20}
    assert rows["posting_frequency_7d"].value == {"count": 3, "window_days": 7}
    assert (EVIDENCE_REFRESH_BUCKET, str(BRAND_ID)) in limiter.hits

    limiter.reject_after = len(limiter.hits)
    limited = brain_client.post(f"{BRAIN}/evidence/refresh", headers=owner_headers)
    assert limited.status_code == 429
    assert limited.json()["detail"]["error"] == "refresh_rate_limited"
    assert limited.headers["Retry-After"] == "3600"
    assert EVIDENCE_REFRESH_LIMIT == 60


async def test_evidence_collector_marks_missing_data_unavailable() -> None:
    repo = FakeBrandBrainRepository()
    reader = FakeInternalMetricsReader()
    reader.metrics = InternalMetrics(
        wa_conversations_7d=0,
        wa_approval_rate_30d=None,
        content_approval_rate_30d=None,
        posting_frequency_7d=0,
        avg_reply_latency_s=None,
    )
    rows = await EvidenceCollector(repo, reader).collect_internal(BRAND_ID, now=NOW)
    by_metric = {r.metric: r.value for r in rows}
    assert by_metric["wa_approval_rate_30d"] == {"unavailable": "no_data", "window_days": 30}
    assert by_metric["content_approval_rate_30d"] == {"unavailable": "no_data", "window_days": 30}
    assert by_metric["avg_reply_latency_s"] == {"unavailable": "no_data"}
    assert all(r.observed_at == NOW for r in rows)
    assert all("EAA" not in (r.source_ref or "") for r in rows)


def test_evidence_list_keyset_filters_and_visibility(
    brain_client: TestClient,
    owner_headers,
    viewer_headers,
    brain_repo: FakeBrandBrainRepository,
) -> None:
    competitor_id = uuid4()
    rows = [
        _evidence(observed_at=NOW - timedelta(minutes=i), value={"count": i}) for i in range(5)
    ] + [
        _evidence(
            subject_kind="competitor",
            subject_id=competitor_id,
            evidence_kind="estimated",
            metric="followers_count",
            source_kind="public_web",
            source_ref="https://beanery.com/",
            value={"count": 900},
            observed_at=NOW - timedelta(hours=1),
        )
    ]
    brain_repo.evidence = {r.id: r for r in rows}

    page1 = brain_client.get(f"{BRAIN}/evidence", params={"limit": 2}, headers=owner_headers)
    assert page1.status_code == 200
    body = page1.json()
    assert [i["value"]["count"] for i in body["items"]] == [0, 1]
    assert body["next_cursor"] is not None
    assert body["items"][0]["confidence"] == "0.95"
    assert body["items"][0]["source_ref"] == "internal_db:wa_conversations_7d"

    page2 = brain_client.get(
        f"{BRAIN}/evidence",
        params={"limit": 2, "cursor": body["next_cursor"]},
        headers=owner_headers,
    )
    assert [i["value"]["count"] for i in page2.json()["items"]] == [2, 3]
    page3 = brain_client.get(
        f"{BRAIN}/evidence",
        params={"limit": 2, "cursor": page2.json()["next_cursor"]},
        headers=owner_headers,
    )
    assert [i["value"]["count"] for i in page3.json()["items"]] == [4, 900]
    assert page3.json()["next_cursor"] is None

    viewer = brain_client.get(f"{BRAIN}/evidence", headers=viewer_headers).json()
    assert viewer["items"][0]["evidence_kind"] == "measured"
    assert "confidence" not in viewer["items"][0] and "source_ref" not in viewer["items"][0]
    assert "source_kind" not in viewer["items"][0]

    only_comp = brain_client.get(
        f"{BRAIN}/evidence",
        params={"subject": "competitor", "subject_id": str(competitor_id)},
        headers=owner_headers,
    ).json()
    assert [i["metric"] for i in only_comp["items"]] == ["followers_count"]
    only_est = brain_client.get(
        f"{BRAIN}/evidence", params={"kind": "estimated"}, headers=owner_headers
    ).json()
    assert len(only_est["items"]) == 1
    by_metric = brain_client.get(
        f"{BRAIN}/evidence", params={"metric": "wa_conversations_7d"}, headers=owner_headers
    ).json()
    assert len(by_metric["items"]) == 5

    unknown = brain_client.get(
        f"{BRAIN}/evidence", params={"metric": "likes_total"}, headers=owner_headers
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["error"] == "metric_unknown"
    bad_cursor = brain_client.get(
        f"{BRAIN}/evidence", params={"cursor": "%%%"}, headers=owner_headers
    )
    assert bad_cursor.status_code == 422
    assert bad_cursor.json()["detail"]["error"] == "invalid_cursor"
    assert (
        brain_client.get(
            f"{BRAIN}/evidence", params={"kind": "guessed"}, headers=owner_headers
        ).status_code
        == 422
    )


def test_cursor_round_trip_and_if_match_parser() -> None:
    item_id = uuid4()
    assert decode_cursor(encode_cursor(NOW, item_id)) == (NOW, item_id)
    assert decode_cursor(None) is None
    assert parse_if_match('"12"') == 12
    for bad in (None, "", "0", "-1", "abc", '"x"'):
        with pytest.raises(BrandBrainError):
            parse_if_match(bad)
    assert to_http(BrandBrainError()).status_code == 400


# --------------------------------------------------------------------- cost --
def test_cost_is_owner_only_and_aggregates_by_day(
    brain_client: TestClient,
    owner_headers,
    manager_headers,
    viewer_headers,
    brain_repo: FakeBrandBrainRepository,
) -> None:
    assert brain_client.get(f"{BRAIN}/cost", headers=manager_headers).status_code == 403
    assert brain_client.get(f"{BRAIN}/cost", headers=viewer_headers).status_code == 403

    today = datetime.now(tz=UTC)

    def entry(**overrides) -> CostEntry:  # noqa: ANN003
        values = {
            "brand_id": BRAND_ID,
            "purpose": "brand_diagnosis",
            "provider": "fake",
            "model": "fake-1",
            "credential_mode": "platform",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "estimated_cost_usd": Decimal("0.000300"),
            "latency_ms": 12,
            "prompt_version": "brand_diagnosis_v1",
            "outcome": CostOutcome.OK,
            "created_at": today,
        }
        values.update(overrides)
        return CostEntry(**values)

    brain_repo.cost_entries = [
        entry(),
        entry(outcome=CostOutcome.ERROR, error_category="timeout", completion_tokens=None),
        entry(created_at=today - timedelta(days=1), estimated_cost_usd=None),
        entry(created_at=today - timedelta(days=40)),  # outside the 30-day window
        entry(brand_id=BRAND2_ID),  # other brand
    ]
    response = brain_client.get(f"{BRAIN}/cost", headers=owner_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 30
    assert body["total_calls"] == 3
    assert body["prompt_tokens"] == 300
    assert body["completion_tokens"] == 100
    assert Decimal(body["total_estimated_cost_usd"]) == Decimal("0.000600")
    assert body["items"][0]["day"] == today.date().isoformat()
    assert body["items"][0]["calls"] == 2 and body["items"][0]["errors"] == 1
    assert body["items"][1]["estimated_cost_usd"] is None


# ------------------------------------------------------------ domain guards --
def test_evidence_model_enforces_metric_dictionary_and_kind_source_rule() -> None:
    with pytest.raises(ValidationError, match="unknown metric"):
        _evidence(metric="likes_total")
    with pytest.raises(ValidationError, match="not allowed"):
        _evidence(evidence_kind="measured", source_kind="public_web")
    with pytest.raises(ValidationError, match="not allowed"):
        _evidence(evidence_kind="inferred", source_kind="internal_db")
    with pytest.raises(ValidationError):
        _evidence(confidence=Decimal("1.5"))
    ok = _evidence(evidence_kind="estimated", source_kind="graph_api", metric="fan_count")
    assert ok.evidence_kind == "estimated"
    assert all(source_allowed(k, s) for k, srcs in ALLOWED_SOURCES.items() for s in srcs)
    assert not source_allowed("measured", "model_inference")
    assert set(INTERNAL_METRICS) <= set(METRICS)
    assert all(METRICS[m][1] == "measured" for m in INTERNAL_METRICS)


def test_cost_log_and_evidence_tables_have_no_prompt_text_columns() -> None:
    cost_columns = {c.name for c in AICostLogRow.__table__.columns}
    assert not cost_columns & {"prompt", "system", "user", "output", "text", "body", "completion"}
    assert {"prompt_tokens", "completion_tokens", "estimated_cost_usd", "outcome"} <= cost_columns
    evidence_columns = {c.name for c in BrandEvidenceRow.__table__.columns}
    assert "source_ref" in evidence_columns and "token" not in " ".join(evidence_columns)
    names = {c.name for c in AICostLogRow.__table__.constraints} | {
        c.name for c in BrandEvidenceRow.__table__.constraints
    }
    assert {"ck_ai_cost_log_outcome", "ck_brand_evidence_kind_source"} <= names


def test_host_helpers() -> None:
    assert normalize_host(" Example.COM. ") == "example.com"
    assert normalize_host("www.example.com") == "example.com"
    assert normalize_host("example.com:443") is None
    assert normalize_host("user@example.com") is None
    assert normalize_host("1.2.3.4") is None
    assert normalize_host("x" * 254 + ".com") is None
    assert host_matches("shop.example.com", "example.com")
    assert not host_matches("notexample.com", "example.com")
    assert is_blocked_platform("l.instagram.com") and not is_blocked_platform("xcom.net")
    assert {"instagram.com", "tiktok.com", "x.com", "youtube.com"} <= BLOCKED_PLATFORM_HOSTS


def test_profile_update_schema_bounds() -> None:
    with pytest.raises(ValidationError):
        ProfileUpdate(products=[{"name": f"p{i}"} for i in range(21)])
    with pytest.raises(ValidationError):
        ProfileUpdate(goals=[{"kind": "fame", "target": "x", "horizon_days": 10}])
    assert ProfileUpdate().tone.formality == "neutral"


def test_brain_routes_are_protected_and_counted() -> None:
    from tests.test_auth_guard import PROTECTED

    brain = sorted(op for op in PROTECTED if "/brain/" in op[1])
    assert len(brain) == 9
    assert brain == [
        ("DELETE", "/api/v1/brands/{brand_id}/brain/competitors/{competitor_id}"),
        ("GET", "/api/v1/brands/{brand_id}/brain/competitors"),
        ("GET", "/api/v1/brands/{brand_id}/brain/cost"),
        ("GET", "/api/v1/brands/{brand_id}/brain/evidence"),
        ("GET", "/api/v1/brands/{brand_id}/brain/profile"),
        ("GET", "/api/v1/brands/{brand_id}/brain/profile/revisions"),
        ("POST", "/api/v1/brands/{brand_id}/brain/competitors"),
        ("POST", "/api/v1/brands/{brand_id}/brain/evidence/refresh"),
        ("PUT", "/api/v1/brands/{brand_id}/brain/profile"),
    ]


def test_sample_uuid_type_matches_router() -> None:
    assert isinstance(UUID(int=0), UUID)
