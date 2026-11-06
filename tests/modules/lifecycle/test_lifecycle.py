"""SPRINT-13 Part C — data lifecycle (D-081), DB-less.

* C-1: ``POST /brands/{id}/export`` ⇒ 202 + export_id; ``GET …/export/{id}`` ⇒ the ZIP
  (one JSON per entity + ``manifest.json`` with counts and sha256), ``?download=false`` ⇒
  JSON status; >5,000 rows in one entity ⇒ 413 ``export_too_large``; unknown ⇒ 404
  ``export_not_found``; no secret-shaped value ever reaches the archive (§3-12).
* C-2: ``DELETE /brands/{id}`` with the slug or name ⇒ 204 + one ``brand_deleted`` audit
  row keyed by ``sha256(brand_id)``; wrong confirm ⇒ 422 ``confirmation_mismatch``; a
  failing Meta unsubscribe is recorded (``unsubscribe=failed``) and never blocks.
* Both are OWNER-only (MANAGER ⇒ 403, stranger ⇒ 404) and need a fresh session.
* C-3: the sweep audits one ``retention_sweep`` row with ``actor=NULL``; the scheduler step
  honours ``RETENTION_INTERVAL_SECONDS`` and ``RETENTION_DRY_RUN``.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.config import Settings
from src.modules.lifecycle import scheduler_step
from src.modules.lifecycle.domain import EXPORT_ENTITIES, EXPORT_ROW_LIMIT
from src.modules.lifecycle.exceptions import ExportFailedError
from src.modules.lifecycle.router import get_unsubscriber
from src.modules.lifecycle.service import LifecycleService, brand_hash, build_zip
from src.modules.tenancy.domain.models import GrantRole

from tests.conftest import AGENCY_ID, BRAND_ID, OWNER_ID, OWNER_USER_ID, STRANGER_ID, grant
from tests.fakes import FakeLifecycleRepository, FakeOpsRepository, FakeStore

API = "/api/v1"
Headers = Callable[[UUID], dict[str, str]]


@pytest.fixture
def lifecycle_repo(app: FastAPI) -> FakeLifecycleRepository:
    return app.state.lifecycle_repo


@pytest.fixture
def ops_repo(app: FastAPI) -> FakeOpsRepository:
    return app.state.ops_repo


# ------------------------------------------------------------------ C-1 export ---
def test_export_roundtrip_zip_manifest_and_sha256(
    client: TestClient, auth_headers: Headers, lifecycle_repo: FakeLifecycleRepository
) -> None:
    lifecycle_repo.entities["messages"] = [{"id": str(uuid4()), "text": "مرحبا"} for _ in range(3)]
    res = client.post(f"{API}/brands/{BRAND_ID}/export", headers=auth_headers(OWNER_ID))
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "ready" and body["sha256"] and body["byte_size"] > 0
    export_id = body["export_id"]

    status = client.get(
        f"{API}/brands/{BRAND_ID}/export/{export_id}",
        params={"download": "false"},
        headers=auth_headers(OWNER_ID),
    )
    assert status.status_code == 200
    assert status.headers["content-type"].startswith("application/json")
    assert status.json()["export_id"] == export_id

    dl = client.get(f"{API}/brands/{BRAND_ID}/export/{export_id}", headers=auth_headers(OWNER_ID))
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/zip"
    assert f"brand-export-{export_id}.zip" in dl.headers["content-disposition"]
    assert (
        dl.headers["x-export-sha256"] == hashlib.sha256(dl.content).hexdigest() == body["sha256"]
    )

    with zipfile.ZipFile(io.BytesIO(dl.content)) as zf:
        names = set(zf.namelist())
        assert names == {f"{e}.json" for e in EXPORT_ENTITIES} | {"manifest.json"}
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["brand_hash"] == brand_hash(BRAND_ID)
        assert manifest["counts"]["messages"] == 3 and manifest["counts"]["brand"] == 1
        for entity in EXPORT_ENTITIES:
            data = zf.read(f"{entity}.json")
            assert manifest["sha256"][entity] == hashlib.sha256(data).hexdigest()
        assert json.loads(zf.read("messages.json"))[0]["text"] == "مرحبا"
        blob = b"".join(zf.read(n) for n in names)
    for fragment in (b"access_token", b"refresh_token", b"api_key", b"gAAAA", b"consent"):
        assert fragment not in blob


def test_export_too_large_is_413(
    client: TestClient, auth_headers: Headers, lifecycle_repo: FakeLifecycleRepository
) -> None:
    lifecycle_repo.entities["conversations"] = [{"id": i} for i in range(EXPORT_ROW_LIMIT + 1)]
    res = client.post(f"{API}/brands/{BRAND_ID}/export", headers=auth_headers(OWNER_ID))
    assert res.status_code == 413
    assert res.json()["detail"]["error"] == "export_too_large"
    assert lifecycle_repo.exports == {}


def test_export_unknown_or_expired_is_404(
    client: TestClient, auth_headers: Headers, lifecycle_repo: FakeLifecycleRepository
) -> None:
    res = client.get(f"{API}/brands/{BRAND_ID}/export/{uuid4()}", headers=auth_headers(OWNER_ID))
    assert res.status_code == 404
    assert res.json()["detail"]["error"] == "export_not_found"

    created = client.post(f"{API}/brands/{BRAND_ID}/export", headers=auth_headers(OWNER_ID))
    export_id = UUID(created.json()["export_id"])
    export, blob = lifecycle_repo.exports[export_id]
    stale = export.model_copy(update={"created_at": datetime.now(tz=UTC) - timedelta(hours=25)})
    lifecycle_repo.exports[export_id] = (stale, blob)
    res = client.get(f"{API}/brands/{BRAND_ID}/export/{export_id}", headers=auth_headers(OWNER_ID))
    assert res.status_code == 404  # 24h expiry (§3-11)


def test_build_zip_rejects_a_mixed_case_fernet_prefixed_key_name() -> None:
    """B-0.4: the guard compares ``k.lower()``, so the fragment is lower-case ``gaaaa`` —
    a column named with the mixed-case Fernet prefix is refused (literal, not a real secret)."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ExportFailedError):
        build_zip({"brand": [{"gAAAAB_cipher": "x"}]}, brand_id=BRAND_ID, now=now)
    body = "old key began with gAAAAB and our api_key was rotated"
    blob = build_zip({"messages": [{"id": 1, "body": body}]}, brand_id=BRAND_ID, now=now)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert body in json.loads(zf.read("messages.json"))[0]["body"]


def test_build_zip_checks_key_names_only_never_customer_text() -> None:
    """F-4: a secret-shaped *column name* ⇒ ``export_failed``; customer text that happens to
    contain "api_key" / "gAAAA" is their data and is exported verbatim."""
    now = datetime(2026, 11, 3, tzinfo=UTC)
    with pytest.raises(ExportFailedError, match="secret-shaped"):
        build_zip({"brand": [{"access_token": "x"}]}, brand_id=BRAND_ID, now=now)
    body = "please rotate our api_key — the old one started with gAAAA"
    blob = build_zip({"messages": [{"id": 1, "body": body}]}, brand_id=BRAND_ID, now=now)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert body in zf.read("messages.json").decode("utf-8")
        assert set(zf.namelist()) == {f"{e}.json" for e in EXPORT_ENTITIES} | {"manifest.json"}
    a = build_zip({"brand": [{"id": 1}]}, brand_id=BRAND_ID, now=now)
    b = build_zip({"brand": [{"id": 1}]}, brand_id=BRAND_ID, now=now)
    assert a == b  # deterministic ⇒ the recorded sha256 is reproducible


def test_export_unexpected_error_is_500_export_failed(
    client: TestClient, auth_headers: Headers, lifecycle_repo: FakeLifecycleRepository
) -> None:
    async def boom(entity: str, brand_id: object, *, limit: int) -> list[dict]:
        raise RuntimeError("column does not exist")

    lifecycle_repo.read_entity = boom  # type: ignore[method-assign]
    res = client.post(f"{API}/brands/{BRAND_ID}/export", headers=auth_headers(OWNER_ID))
    assert res.status_code == 500 and res.json()["detail"]["error"] == "export_failed"


def test_export_includes_revisions_and_competitors(
    client: TestClient, auth_headers: Headers, lifecycle_repo: FakeLifecycleRepository
) -> None:
    lifecycle_repo.entities["content_revisions"] = [
        {"id": "r1", "item_id": "i1", "revision_no": 1, "body": "نص المسودة api_key"}
    ]
    lifecycle_repo.entities["brand_competitors"] = [{"id": "c1", "name": "Rival", "kind": "page"}]
    h = auth_headers(OWNER_ID)
    res = client.post(f"{API}/brands/{BRAND_ID}/export", headers=h)
    assert res.status_code == 202, res.text
    zip_res = client.get(f"{API}/brands/{BRAND_ID}/export/{res.json()['export_id']}", headers=h)
    with zipfile.ZipFile(io.BytesIO(zip_res.content)) as zf:
        assert "نص المسودة api_key" in zf.read("content_revisions.json").decode("utf-8")
        assert "Rival" in zf.read("brand_competitors.json").decode("utf-8")
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["counts"]["content_revisions"] == 1
    assert manifest["counts"]["brand_competitors"] == 1


def test_get_unsubscriber_is_none_without_encryption_key(settings: Settings) -> None:
    """F-4 (S13-3): ``http_client`` present but no ``TOKEN_ENCRYPTION_KEY`` ⇒ ``None``, not 500."""
    from types import SimpleNamespace

    no_key = settings.model_copy(update={"token_encryption_key": None})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(http_client=object())))
    assert get_unsubscriber(request, no_key) is None  # type: ignore[arg-type]


async def test_unsubscriber_returns_false_on_undecryptable_token(settings: Settings) -> None:
    from types import SimpleNamespace

    import httpx

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(http_client=http)))
        unsubscribe = get_unsubscriber(request, settings)  # type: ignore[arg-type]
        assert unsubscribe is not None
        assert await unsubscribe("p1", "not-a-fernet-token") is False
    assert calls == []  # nothing left the process


# ------------------------------------------------------------------ C-2 delete ---
def test_delete_brand_by_slug_204_and_audit_row(
    client: TestClient,
    auth_headers: Headers,
    lifecycle_repo: FakeLifecycleRepository,
    ops_repo: FakeOpsRepository,
) -> None:
    res = client.request(
        "DELETE",
        f"{API}/brands/{BRAND_ID}",
        json={"confirm": "nile-coffee"},
        headers=auth_headers(OWNER_ID),
    )
    assert res.status_code == 204, res.text
    assert lifecycle_repo.deleted == [BRAND_ID]
    rows = [a for a in ops_repo.actions if a.action == "brand_deleted"]
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_user_id == OWNER_USER_ID
    assert row.target_kind == "brand"
    assert (
        row.target_id == brand_hash(BRAND_ID) == hashlib.sha256(str(BRAND_ID).encode()).hexdigest()
    )
    assert row.after == {"brand_hash": brand_hash(BRAND_ID), "unsubscribe": "skipped"}
    assert str(BRAND_ID) not in json.dumps(row.after)
    # the brand is gone from the tenant's view
    assert (
        client.get(f"{API}/brands/{BRAND_ID}", headers=auth_headers(OWNER_ID)).status_code == 404
    )


def test_delete_brand_by_name_and_confirmation_mismatch(
    client: TestClient, auth_headers: Headers, lifecycle_repo: FakeLifecycleRepository
) -> None:
    bad = client.request(
        "DELETE",
        f"{API}/brands/{BRAND_ID}",
        json={"confirm": "Nile Coffe"},
        headers=auth_headers(OWNER_ID),
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["error"] == "confirmation_mismatch"
    assert lifecycle_repo.deleted == []
    ok = client.request(
        "DELETE",
        f"{API}/brands/{BRAND_ID}",
        json={"confirm": " Nile Coffee "},
        headers=auth_headers(OWNER_ID),
    )
    assert ok.status_code == 204


def test_delete_records_failed_unsubscribe_but_still_deletes(
    app: FastAPI,
    client: TestClient,
    auth_headers: Headers,
    lifecycle_repo: FakeLifecycleRepository,
    ops_repo: FakeOpsRepository,
) -> None:
    lifecycle_repo.pages = [("p1", "enc-1"), ("p2", "enc-2")]
    calls: list[str] = []

    async def failing(page_id: str, token: str) -> bool:
        calls.append(page_id)
        if page_id == "p2":
            raise RuntimeError("graph down")
        return True

    app.dependency_overrides[get_unsubscriber] = lambda: failing
    res = client.request(
        "DELETE",
        f"{API}/brands/{BRAND_ID}",
        json={"confirm": "nile-coffee"},
        headers=auth_headers(OWNER_ID),
    )
    assert res.status_code == 204
    assert calls == ["p1", "p2"]
    row = next(a for a in ops_repo.actions if a.action == "brand_deleted")
    assert row.after["unsubscribe"] == "failed"


def test_delete_records_ok_unsubscribe(
    app: FastAPI,
    client: TestClient,
    auth_headers: Headers,
    lifecycle_repo: FakeLifecycleRepository,
    ops_repo: FakeOpsRepository,
) -> None:
    lifecycle_repo.pages = [("p1", "enc-1")]

    async def ok(page_id: str, token: str) -> bool:
        return True

    app.dependency_overrides[get_unsubscriber] = lambda: ok
    res = client.request(
        "DELETE",
        f"{API}/brands/{BRAND_ID}",
        json={"confirm": "Nile Coffee"},
        headers=auth_headers(OWNER_ID),
    )
    assert res.status_code == 204
    row = next(a for a in ops_repo.actions if a.action == "brand_deleted")
    assert row.after["unsubscribe"] == "ok"


# ------------------------------------------------------------------ access ---
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", f"{API}/brands/{BRAND_ID}/export", None),
        ("GET", f"{API}/brands/{BRAND_ID}/export/{uuid4()}", None),
        ("DELETE", f"{API}/brands/{BRAND_ID}", {"confirm": "nile-coffee"}),
    ],
)
def test_lifecycle_routes_are_owner_only(
    client: TestClient,
    auth_headers: Headers,
    store: FakeStore,
    method: str,
    path: str,
    body: dict | None,
) -> None:
    assert client.request(method, path, json=body).status_code == 401
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    manager = client.request(method, path, json=body, headers=auth_headers(AGENCY_ID))
    assert manager.status_code == 403, manager.text
    stranger = client.request(method, path, json=body, headers=auth_headers(STRANGER_ID))
    assert stranger.status_code == 404
    unknown = client.request(
        method,
        path.replace(str(BRAND_ID), str(uuid4())),
        json=body,
        headers=auth_headers(OWNER_ID),
    )
    assert unknown.status_code == 404


def test_lifecycle_requires_fresh_session(
    client: TestClient, auth_headers: Headers, store: FakeStore
) -> None:
    headers = auth_headers(OWNER_ID)
    store.sessions.clear()  # revoked ⇒ CurrentUserFresh refuses
    res = client.post(f"{API}/brands/{BRAND_ID}/export", headers=headers)
    assert res.status_code == 401


# ------------------------------------------------------------------ C-3 retention ---
async def test_retention_sweep_audits_platform_row_with_null_actor() -> None:
    repo = FakeLifecycleRepository()
    repo.sweep_counts = {"wa_messages": 7, "brand_exports": 2}
    recorded: list[dict] = []

    async def audit(**kwargs: object) -> None:
        recorded.append(kwargs)

    service = LifecycleService(repo, audit=audit)
    now = datetime(2026, 11, 3, 12, tzinfo=UTC)
    report = await service.retention_sweep(
        now=now, months=12, months_agency=24, export_hours=24, dry_run=True
    )
    assert report.dry_run is True and report.counts == {"wa_messages": 7, "brand_exports": 2}
    assert report.cutoff_default == now - timedelta(days=360)
    assert report.cutoff_agency == now - timedelta(days=720)
    assert report.export_cutoff == now - timedelta(hours=24)
    assert repo.sweeps[0]["dry_run"] is True
    assert recorded == [
        {
            "actor_user_id": None,
            "action": "retention_sweep",
            "target_kind": "platform",
            "target_id": "retention",
            "after": {"dry_run": True, "counts": {"wa_messages": 7, "brand_exports": 2}},
        }
    ]


async def test_retention_step_honours_interval_and_dry_run(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = FakeLifecycleRepository()
    ops = FakeOpsRepository()
    seen: list[str] = []

    class _Session:
        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

    def _independent(_factory: object) -> _Session:
        seen.append("session")
        return _Session()

    monkeypatch.setattr(scheduler_step, "independent_session", _independent)
    monkeypatch.setattr(scheduler_step, "PgLifecycleRepository", lambda _s: repo)
    monkeypatch.setattr(scheduler_step, "PgOpsRepository", lambda _s: ops)
    cfg = settings.model_copy(
        update={"retention_interval_seconds": 3600, "retention_dry_run": True}
    )
    step = scheduler_step.retention_step(cfg, session_factory=lambda: None)
    t0 = datetime(2026, 11, 3, tzinfo=UTC)
    first = await step(t0)
    assert first is not None and first.dry_run is True
    assert await step(t0 + timedelta(minutes=30)) is None  # inside the interval
    second = await step(t0 + timedelta(hours=1))
    assert second is not None
    assert len(repo.sweeps) == 2 and seen == ["session", "session"]
    assert [a.action for a in ops.actions] == ["retention_sweep", "retention_sweep"]
    assert all(a.actor_user_id is None for a in ops.actions)
    assert settings.retention_dry_run is True  # default stays dry (§3-15)
