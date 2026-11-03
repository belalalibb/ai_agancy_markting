"""SPRINT-12.1-B C-3 — the settings screen: connections, WhatsApp, plan, consent, members."""

from __future__ import annotations

import json
from uuid import UUID

from fastapi.testclient import TestClient
from src.modules.tenancy.domain.models import BrandAccessGrant, GrantRole, Tenant
from src.ui.router import I18N_PATH

from tests.conftest import AGENCY_ID, BRAND_ID, OWNER_ID, STRANGER_ID
from tests.fakes import FakeStore
from tests.ui.conftest import SETTINGS, VIEWER_LOGIN, csrf_of, login

MESSAGES = json.loads(I18N_PATH.read_text(encoding="utf-8"))
NEW_TENANT = "00000000-0000-4000-8000-0000000000ee"


def _grants(store: FakeStore) -> list[BrandAccessGrant]:
    return [g for g in store.grants.values() if g.brand_id == BRAND_ID and g.revoked_at is None]


def _new_tenant(store: FakeStore) -> None:
    """The API refuses unknown grantees (``tenant_not_found``) — seed a real one."""
    store.tenants[UUID(NEW_TENANT)] = Tenant(id=UUID(NEW_TENANT), slug="newbie", name="Newbie")


def test_settings_renders_all_sections_for_owner(
    ui_client: TestClient,
    store: FakeStore,
    wa_account,  # noqa: ANN001
) -> None:
    login(ui_client)
    res = ui_client.get(SETTINGS)
    assert res.status_code == 200, res.text
    html = res.text
    for card in ("connections", "whatsapp", "plan", "consent", "members"):
        assert f'data-card="{card}"' in html
    assert "data-wa-phone" in html and wa_account.display_phone in html
    assert MESSAGES["wa_status.active"] in html and "wa_status.active" not in html
    for limit in ("daily_tokens", "monthly_posts", "monthly_wa_conversations", "max_brands"):
        assert f'data-limit="{limit}"' in html and f"limit.{limit}" not in html
    assert 'data-form="grant"' in html and 'data-form="consent"' in html
    assert html.count('data-form="revoke"') == len(_grants(store)) == 2
    assert '<option value="signals_only" selected>' in html
    assert 'data-form="connect"' in html and 'target="_blank"' in html


def test_settings_consent_grant_and_revoke_success(
    ui_client: TestClient, store: FakeStore
) -> None:
    _new_tenant(store)
    login(ui_client)
    csrf = csrf_of(ui_client.get(SETTINGS).text)
    res = ui_client.post(f"{SETTINGS}/consent", data={"csrf": csrf, "learning_consent": "none"})
    assert res.status_code == 200 and MESSAGES["settings.saved"] in res.text
    assert store.brands[BRAND_ID].learning_consent == "none"
    assert '<option value="none" selected>' in res.text
    res = ui_client.post(
        f"{SETTINGS}/grants",
        data={"csrf": csrf, "grantee_tenant_id": NEW_TENANT, "role": "viewer"},
    )
    assert res.status_code == 200 and MESSAGES["settings.granted"] in res.text
    new = next(g for g in _grants(store) if str(g.grantee_tenant_id) == NEW_TENANT)
    assert new.role is GrantRole.VIEWER and f'data-grant="{new.id}"' in res.text
    res = ui_client.post(f"{SETTINGS}/grants/{new.id}/revoke", data={"csrf": csrf})
    assert res.status_code == 200 and MESSAGES["settings.revoked"] in res.text
    assert f'data-grant="{new.id}"' not in res.text
    assert store.grants[new.id].revoked_at is not None


def test_settings_errors_are_translated_flash(ui_client: TestClient, store: FakeStore) -> None:
    login(ui_client)
    csrf = csrf_of(ui_client.get(SETTINGS).text)
    # granting the owner itself ⇒ grantee_is_owner
    res = ui_client.post(
        f"{SETTINGS}/grants",
        data={"csrf": csrf, "grantee_tenant_id": str(OWNER_ID), "role": "manager"},
    )
    assert res.status_code == 200 and MESSAGES["error.grantee_is_owner"] in res.text
    # duplicate ⇒ grant_already_active
    res = ui_client.post(
        f"{SETTINGS}/grants",
        data={"csrf": csrf, "grantee_tenant_id": str(AGENCY_ID), "role": "manager"},
    )
    assert res.status_code == 200 and MESSAGES["error.grant_already_active"] in res.text
    # unknown grantee ⇒ tenant_not_found
    res = ui_client.post(
        f"{SETTINGS}/grants",
        data={"csrf": csrf, "grantee_tenant_id": NEW_TENANT, "role": "viewer"},
    )
    assert res.status_code == 200 and MESSAGES["error.tenant_not_found"] in res.text
    # bad consent value ⇒ 422 ⇒ translated
    res = ui_client.post(f"{SETTINGS}/consent", data={"csrf": csrf, "learning_consent": "maybe"})
    assert res.status_code == 200 and MESSAGES["error.http_422"] in res.text
    assert store.brands[BRAND_ID].learning_consent == "signals_only"
    # resync of an unknown connection ⇒ 404 ⇒ translated, no 500
    res = ui_client.post(f"{SETTINGS}/connections/{NEW_TENANT}/resync", data={"csrf": csrf})
    assert res.status_code == 200 and "data-flash" in res.text and "detail" not in res.text
    # missing CSRF ⇒ 403, nothing changed
    assert (
        ui_client.post(f"{SETTINGS}/consent", data={"learning_consent": "none"}).status_code == 403
    )


def test_settings_viewer_sees_no_grant_or_consent_buttons(
    ui_client: TestClient, store: FakeStore
) -> None:
    _new_tenant(store)
    login(ui_client, VIEWER_LOGIN)
    res = ui_client.get(SETTINGS)
    assert res.status_code == 200, res.text
    html = res.text
    assert 'data-card="members"' not in html and 'data-form="grant"' not in html
    assert 'data-form="revoke"' not in html and 'data-form="connect"' not in html
    assert '<select name="learning_consent" disabled>' in html
    assert MESSAGES["settings.save"] not in html.split('data-card="consent"', 1)[1]
    assert 'data-card="whatsapp"' in html and "data-wa-phone" not in html  # VIEWER ⇒ 403 ⇒ empty
    # the API still enforces: a forged POST is a translated 403 flash, no change
    csrf = csrf_of(html)
    res = ui_client.post(
        f"{SETTINGS}/grants",
        data={"csrf": csrf, "grantee_tenant_id": NEW_TENANT, "role": "viewer"},
    )
    assert res.status_code == 200 and MESSAGES["error.insufficient_role"] in res.text
    assert all(str(g.grantee_tenant_id) != NEW_TENANT for g in _grants(store))
    assert STRANGER_ID in {g.grantee_tenant_id for g in _grants(store)}
