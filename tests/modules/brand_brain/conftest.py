"""Fixtures for the ``brand_brain`` unit tests — the fake-wired app with the Brand Brain
repository / metrics reader overridden."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.modules.tenancy.domain.models import GrantRole

from tests.conftest import AGENCY_ID, BRAND_ID, OWNER_ID, STRANGER_ID, grant
from tests.fakes import (
    FakeBrandBrainRepository,
    FakeCostLogWriter,
    FakeInternalMetricsReader,
    FakeStore,
)

BRAIN = f"/api/v1/brands/{BRAND_ID}/brain"


@pytest.fixture
def brain_repo() -> FakeBrandBrainRepository:
    return FakeBrandBrainRepository()


@pytest.fixture
def metrics_reader() -> FakeInternalMetricsReader:
    return FakeInternalMetricsReader()


@pytest.fixture
def brain_app(
    app: FastAPI,
    store: FakeStore,
    brain_repo: FakeBrandBrainRepository,
    metrics_reader: FakeInternalMetricsReader,
) -> FastAPI:
    from src.modules.brand_brain import router

    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)
    app.state.brain_repo = brain_repo
    app.dependency_overrides[router.get_brain_repository] = lambda: brain_repo
    app.dependency_overrides[router.get_internal_metrics_reader] = lambda: metrics_reader
    # B-2: the unit app has no lifespan (no ``app.state.http_client``); competitor sources
    # are "not configured" unless a test overrides them (``test_b2_competitor_refresh``).
    app.dependency_overrides[router.get_graph_page_source] = lambda: None
    app.dependency_overrides[router.get_public_page_source] = lambda: None
    app.state.cost_writer = FakeCostLogWriter(brain_repo)
    app.dependency_overrides[router.get_cost_log_writer] = lambda: app.state.cost_writer
    return app


@pytest.fixture
def brain_client(brain_app: FastAPI) -> TestClient:
    return TestClient(brain_app)


@pytest.fixture
def owner_headers(auth_headers: Callable[[UUID], dict[str, str]]) -> dict[str, str]:
    return auth_headers(OWNER_ID)


@pytest.fixture
def manager_headers(auth_headers: Callable[[UUID], dict[str, str]]) -> dict[str, str]:
    return auth_headers(AGENCY_ID)


@pytest.fixture
def viewer_headers(auth_headers: Callable[[UUID], dict[str, str]]) -> dict[str, str]:
    return auth_headers(STRANGER_ID)
