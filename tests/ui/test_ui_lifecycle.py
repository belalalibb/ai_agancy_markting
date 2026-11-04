"""SPRINT-13 Part C — the settings danger zone (D-081): export ZIP + delete brand, both
through the ApiClient (D-068); delete ⇒ ``HX-Redirect: /app`` (htmx) or 303 ``/app``."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.ui.router import I18N_PATH

from tests.conftest import BRAND_ID
from tests.fakes import FakeStore
from tests.ui.conftest import SETTINGS, VIEWER_LOGIN, csrf_of, login

MESSAGES = json.loads(I18N_PATH.read_text(encoding="utf-8"))


def test_danger_zone_renders_for_owner_only(ui_client: TestClient) -> None:
    login(ui_client)
    html = ui_client.get(SETTINGS).text
    assert 'data-card="danger"' in html
    assert 'data-form="export"' in html and 'data-form="delete"' in html
    assert MESSAGES["settings.danger_title"] in html and "settings.danger_" not in html
    assert "«nile-coffee»" in html  # the hint names the slug to type
    ui_client.cookies.clear()
    login(ui_client, VIEWER_LOGIN)
    html = ui_client.get(SETTINGS).text
    assert 'data-card="danger"' not in html


def test_export_streams_zip_through_api_client(ui_client: TestClient) -> None:
    login(ui_client)
    csrf = csrf_of(ui_client.get(SETTINGS).text)
    res = ui_client.post(f"{SETTINGS}/export", data={"csrf": csrf})
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/zip"
    assert "brand-export-" in res.headers["content-disposition"]
    assert res.headers["x-export-sha256"] == hashlib.sha256(res.content).hexdigest()
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["counts"]["brand"] == 1


def test_delete_mismatch_is_translated_flash_and_keeps_brand(
    ui_client: TestClient, store: FakeStore
) -> None:
    login(ui_client)
    csrf = csrf_of(ui_client.get(SETTINGS).text)
    res = ui_client.post(f"{SETTINGS}/delete", data={"csrf": csrf, "confirm": "wrong"})
    assert res.status_code == 200
    assert MESSAGES["error.confirmation_mismatch"] in res.text
    assert "confirmation_mismatch" not in res.text
    assert BRAND_ID in store.brands


def test_delete_redirects_to_app_browser_and_htmx(
    ui_client: TestClient, ui_app: FastAPI, store: FakeStore
) -> None:
    login(ui_client)
    csrf = csrf_of(ui_client.get(SETTINGS).text)
    res = ui_client.post(
        f"{SETTINGS}/delete",
        data={"csrf": csrf, "confirm": "nile-coffee"},
        headers={"HX-Request": "true"},
    )
    assert res.status_code == 200 and res.headers["HX-Redirect"] == "/app/"
    assert BRAND_ID not in store.brands
    assert ui_app.state.lifecycle_repo.deleted == [BRAND_ID]
    assert ui_client.get(SETTINGS).status_code == 404  # the brand is gone

    # browser path on the second brand: plain 303 to /app
    from tests.conftest import BRAND2_ID

    res = ui_client.post(
        f"/app/b/{BRAND2_ID}/settings/delete", data={"csrf": csrf, "confirm": "Cairo Dental"}
    )
    assert res.status_code == 303 and res.headers["location"] == "/app/"
    assert BRAND2_ID not in store.brands


def test_delete_without_csrf_is_403(ui_client: TestClient, store: FakeStore) -> None:
    login(ui_client)
    res = ui_client.post(f"{SETTINGS}/delete", data={"confirm": "nile-coffee"})
    assert res.status_code == 403
    assert BRAND_ID in store.brands
