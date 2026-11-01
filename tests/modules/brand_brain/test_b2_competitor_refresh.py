"""SPRINT-10 B-2 — ``POST /brain/competitors/{id}/refresh``: Graph app token (public fields,
**no** ``/posts``) + the allow-listed public page ⇒ ``estimated`` evidence only; refusals and
Graph errors become ``{"unavailable": …}`` rows; ``competitor_refreshed_at`` is bumped; roles
and the 10/hour/brand limiter; ``source_ref`` never carries a token (secret-pattern guard)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.modules.brand_brain import router
from src.modules.brand_brain.domain.models import Competitor
from src.modules.brand_brain.public_fetch import PublicFetchRefused, PublicPage
from src.modules.brand_brain.service import (
    COMPETITOR_REFRESH_BUCKET,
    COMPETITOR_REFRESH_LIMIT,
    CompetitorRefresher,
)
from src.modules.social_connect.exceptions import MetaGraphError, MetaRateLimitError
from src.modules.social_connect.meta_client import PUBLIC_PAGE_FIELDS, MetaGraphClient
from src.modules.social_connect.schemas import MetaPublicPageInfo

from tests.conftest import BRAND_ID, GRAPH_BASE, OWNER_USER_ID
from tests.fakes import FakeBrandBrainRepository, FakeRateLimiter
from tests.modules.brand_brain.conftest import BRAIN

pytestmark = pytest.mark.unit

SECRET_PATTERN = re.compile(r"EAA|sk-|Bearer")
HTML = "<html><head><title>Beanery</title></head><body><p>Hand-roasted beans</p></body></html>"


class ScriptedGraph:
    def __init__(self, result: MetaPublicPageInfo | Exception) -> None:
        self.result = result
        self.calls: list[str] = []

    async def get_public_page(self, page_id: str) -> MetaPublicPageInfo:
        self.calls.append(page_id)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class ScriptedWeb:
    def __init__(self, result: PublicPage | Exception) -> None:
        self.result = result
        self.calls: list[tuple[str, list[str]]] = []

    async def fetch(self, url: str, *, allowed_hosts: list[str]) -> PublicPage:
        self.calls.append((url, list(allowed_hosts)))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _page(**overrides: object) -> MetaPublicPageInfo:
    base = {
        "id": "123456",
        "name": "Beanery",
        "about": "Coffee",
        "category": "Coffee shop",
        "fan_count": 1200,
        "followers_count": 1500,
        "website": "https://www.beanery.com/home",
        "link": "https://www.facebook.com/beanery",
    }
    base.update(overrides)
    return MetaPublicPageInfo(**base)


def _public_page() -> PublicPage:
    return PublicPage(
        host="beanery.com",
        status=200,
        title="Beanery",
        meta_description="Slow coffee",
        text_excerpt="Hand-roasted beans",
        fetched_at=datetime.now(tz=UTC),
    )


def _competitor(repo: FakeBrandBrainRepository, **overrides: object) -> Competitor:
    base = {
        "brand_id": BRAND_ID,
        "name": "Beanery",
        "facebook_page_id": "123456",
        "website_host": "beanery.com",
        "allowed_hosts": ["beanery.com"],
        "added_by_user_id": OWNER_USER_ID,
        "created_at": datetime.now(tz=UTC),
    }
    base.update(overrides)
    competitor = Competitor(**base)
    repo.competitors[competitor.id] = competitor
    return competitor


@pytest.fixture
def graph() -> ScriptedGraph:
    return ScriptedGraph(_page())


@pytest.fixture
def web() -> ScriptedWeb:
    return ScriptedWeb(_public_page())


@pytest.fixture
def refresh_client(brain_app: FastAPI, graph: ScriptedGraph, web: ScriptedWeb) -> TestClient:
    brain_app.dependency_overrides[router.get_graph_page_source] = lambda: graph
    brain_app.dependency_overrides[router.get_public_page_source] = lambda: web
    return TestClient(brain_app)


def _url(cid: UUID) -> str:
    return f"{BRAIN}/competitors/{cid}/refresh"


# ------------------------------------------------------------------ route --
def test_refresh_collects_graph_and_web_estimated_evidence(
    refresh_client: TestClient,
    brain_repo: FakeBrandBrainRepository,
    graph: ScriptedGraph,
    web: ScriptedWeb,
    owner_headers,
) -> None:
    competitor = _competitor(brain_repo)
    response = refresh_client.post(_url(competitor.id), headers=owner_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["collected"] == 5
    assert body["metrics"] == [
        "followers_count",
        "fan_count",
        "page_category",
        "website_host",
        "public_page_summary",
    ]
    assert graph.calls == ["123456"]
    assert web.calls == [("https://beanery.com/", ["beanery.com"])]

    rows = [e for e in brain_repo.evidence.values() if e.subject_id == competitor.id]
    assert len(rows) == 5
    assert {r.evidence_kind for r in rows} == {"estimated"}
    assert {r.subject_kind for r in rows} == {"competitor"}
    assert {r.brand_id for r in rows} == {BRAND_ID}
    by_metric = {r.metric: r for r in rows}
    assert by_metric["followers_count"].value == {"count": 1500}
    assert by_metric["followers_count"].source_kind == "graph_api"
    assert by_metric["fan_count"].value == {"count": 1200}
    assert by_metric["page_category"].value == {"label": "Coffee shop"}
    assert by_metric["website_host"].value == {"host": "beanery.com"}
    summary = by_metric["public_page_summary"]
    assert summary.source_kind == "public_web"
    assert summary.value["title"] == "Beanery"
    assert summary.value["excerpt"] == "Hand-roasted beans"
    assert summary.source_ref == "public_web:beanery.com"
    assert by_metric["followers_count"].source_ref == "graph_api:page:123456"
    for r in rows:
        assert not SECRET_PATTERN.search(r.source_ref or "")
        assert not SECRET_PATTERN.search(str(r.value))
    assert brain_repo.competitors[competitor.id].refreshed_at is not None

    listed = refresh_client.get(f"{BRAIN}/competitors", headers=owner_headers).json()["items"][0]
    assert listed["competitor_refreshed_at"] is not None


def test_refresh_records_graph_error_and_web_refusal_as_unavailable(
    brain_app: FastAPI, brain_repo: FakeBrandBrainRepository, owner_headers
) -> None:
    graph = ScriptedGraph(MetaRateLimitError("slow down", code=4, http_status=429))
    web = ScriptedWeb(PublicFetchRefused("robots_disallow", host="beanery.com"))
    brain_app.dependency_overrides[router.get_graph_page_source] = lambda: graph
    brain_app.dependency_overrides[router.get_public_page_source] = lambda: web
    competitor = _competitor(brain_repo)
    response = TestClient(brain_app).post(_url(competitor.id), headers=owner_headers)
    assert response.status_code == 200, response.text
    assert response.json()["metrics"] == ["followers_count", "public_page_summary"]
    rows = {e.metric: e for e in brain_repo.evidence.values() if e.subject_id == competitor.id}
    assert rows["followers_count"].value == {"unavailable": "graph_error", "graph_code": 4}
    assert rows["followers_count"].confidence == 0
    assert rows["public_page_summary"].value == {"unavailable": "robots_disallow"}
    # The Graph *message* never lands in evidence.
    assert "slow down" not in str(rows["followers_count"].value)


def test_refresh_without_sources_configured_still_bumps_timestamp(
    brain_app: FastAPI, brain_repo: FakeBrandBrainRepository, owner_headers
) -> None:
    """Meta not configured (``None`` graph source) + competitor without a website ⇒ no rows,
    200 with ``collected=0`` — never a 5xx."""
    brain_app.dependency_overrides[router.get_graph_page_source] = lambda: None
    brain_app.dependency_overrides[router.get_public_page_source] = lambda: ScriptedWeb(
        _public_page()
    )
    competitor = _competitor(brain_repo, website_host=None, allowed_hosts=[])
    response = TestClient(brain_app).post(_url(competitor.id), headers=owner_headers)
    assert response.status_code == 200
    assert response.json()["collected"] == 0
    assert brain_repo.competitors[competitor.id].refreshed_at is not None


def test_refresh_roles_404_and_rate_limit(
    refresh_client: TestClient,
    brain_repo: FakeBrandBrainRepository,
    limiter: FakeRateLimiter,
    owner_headers,
    manager_headers,
    viewer_headers,
) -> None:
    competitor = _competitor(brain_repo)
    assert refresh_client.post(_url(competitor.id), headers=viewer_headers).status_code == 403
    missing = refresh_client.post(_url(uuid4()), headers=owner_headers)
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"] == "competitor_not_found"
    assert limiter.hits == []  # a 404 does not burn the quota
    assert refresh_client.post(_url(competitor.id), headers=manager_headers).status_code == 200
    assert limiter.hits == [(COMPETITOR_REFRESH_BUCKET, str(BRAND_ID))]
    limiter.reject_after = 1
    limited = refresh_client.post(_url(competitor.id), headers=owner_headers)
    assert limited.status_code == 429
    assert limited.json()["detail"]["error"] == "refresh_rate_limited"
    assert limited.headers["Retry-After"] == "3600"
    assert COMPETITOR_REFRESH_LIMIT == 10


# ------------------------------------------------- refresher unit (edge cases) --
async def test_refresher_graph_not_public_fields_and_bad_website() -> None:
    repo = FakeBrandBrainRepository()
    competitor = _competitor(repo, website_host=None, allowed_hosts=[])
    graph = ScriptedGraph(
        _page(fan_count=None, followers_count=None, category=None, website="not a host")
    )
    rows = await CompetitorRefresher(repo, graph=graph, web=None).refresh(competitor)
    assert [r.metric for r in rows] == ["followers_count", "fan_count", "page_category"]
    assert all(r.value == {"unavailable": "not_public"} for r in rows)


async def test_refresher_graph_error_without_code() -> None:
    repo = FakeBrandBrainRepository()
    competitor = _competitor(repo, website_host=None, allowed_hosts=[])
    graph = ScriptedGraph(MetaGraphError("boom"))
    rows = await CompetitorRefresher(repo, graph=graph, web=None).refresh(competitor)
    assert rows[0].value == {"unavailable": "graph_error"}


# -------------------------------------------- MetaGraphClient.get_public_page --
async def test_get_public_page_uses_app_token_and_public_fields_only(settings) -> None:  # noqa: ANN001
    settings = settings.model_copy(
        update={
            "meta_app_id": "APP1",
            "meta_app_secret": settings.meta_app_secret.__class__("s3cr3t"),
        }
    )
    with respx.mock(base_url=GRAPH_BASE) as graph_mock:
        route = graph_mock.get("/123456").mock(
            return_value=httpx.Response(200, json=_page().model_dump())
        )
        async with httpx.AsyncClient() as http:
            client = MetaGraphClient(settings=settings, http_client=http)
            page = await client.get_public_page("123456")
    assert page.followers_count == 1500 and page.name == "Beanery"
    request = route.calls[0].request
    assert request.method == "GET"
    assert request.url.params["fields"] == PUBLIC_PAGE_FIELDS
    assert "posts" not in str(request.url)
    assert request.headers["Authorization"] == "Bearer APP1|s3cr3t"
    assert "access_token" not in str(request.url)  # H2: token never in the request line


async def test_get_public_page_rejects_non_numeric_and_classifies_errors(settings) -> None:  # noqa: ANN001
    settings = settings.model_copy(
        update={
            "meta_app_id": "APP1",
            "meta_app_secret": settings.meta_app_secret.__class__("s3cr3t"),
        }
    )
    async with httpx.AsyncClient() as http:
        client = MetaGraphClient(settings=settings, http_client=http)
        with pytest.raises(MetaGraphError):
            await client.get_public_page("../me")
        with respx.mock(base_url=GRAPH_BASE) as graph_mock:
            graph_mock.get("/999").mock(
                return_value=httpx.Response(
                    400, json={"error": {"code": 100, "message": "Unsupported get request"}}
                )
            )
            with pytest.raises(MetaGraphError) as info:
                await client.get_public_page("999")
    assert info.value.code == 100
