"""SPRINT-12 Part B — lead loop (D-079) and WhatsApp number caps (D-082) on the fakes.

Lead stage route (OWNER/MANAGER 200, VIEWER 403, kill switch 404, ``lost`` + objection code ⇒
``owner_input`` attribution with a **code** only — D-069), webhook ``referral.source_id`` ⇒
``measured`` attribution (fake post lookup; feature off / duplicate ⇒ none), caps guard
(``kill_switch`` ⇒ 423, cap reached ⇒ 429 + ``Retry-After``) and the admin wa-caps route.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.config import Settings, get_settings
from src.core.metrics import counters
from src.modules.brand_brain.domain.metrics import (
    METRICS,
    lead_close_rate,
    measured_leads_per_post,
)
from src.modules.tenancy.domain.models import GrantRole
from src.modules.whatsapp import lead_loop as lead_loop_module
from src.modules.whatsapp.domain.models import (
    DeliveryStatus,
    Direction,
    LeadStage,
    Message,
    MessageKind,
    NumberCap,
)
from src.modules.whatsapp.exceptions import DailyCapReachedError, NumberDisabledError
from src.modules.whatsapp.lead_loop import LeadLoopService
from src.modules.whatsapp.webhook import compute_signature

from tests.conftest import (
    AGENCY_ID,
    BRAND_ID,
    OWNER_ID,
    OWNER_USER_ID,
    STRANGER_ID,
    USER_BY_TENANT,
    grant,
)
from tests.fakes import FakeStore, FakeWhatsAppRepository
from tests.modules.whatsapp.conftest import (
    APP_SECRET,
    PHONE_NUMBER_ID,
    load_payload,
)
from tests.modules.whatsapp.test_b2_inbox import _ok, _seed_conversation

pytestmark = pytest.mark.unit

API = "/api/v1"
BASE = f"{API}/brands/{BRAND_ID}/whatsapp"
WEBHOOK = f"{API}/webhooks/whatsapp"
WA_CAPS = f"{API}/admin/entitlements/wa-caps"
MESSAGES = f"/{PHONE_NUMBER_ID}/messages"
JOB_ID = UUID("00000000-0000-4000-8000-00000000f00d")
POST_ID = "1234567890_987654321"


class FakePostLookup:
    def __init__(self, known: dict[tuple[UUID, str], UUID] | None = None) -> None:
        self.known = known or {}
        self.calls: list[tuple[UUID, str]] = []

    async def published_job_id(self, brand_id: UUID, external_post_id: str) -> UUID | None:
        self.calls.append((brand_id, external_post_id))
        return self.known.get((brand_id, external_post_id))


@pytest.fixture
def roles(store: FakeStore) -> None:
    grant(store, BRAND_ID, AGENCY_ID, GrantRole.MANAGER)
    grant(store, BRAND_ID, STRANGER_ID, GrantRole.VIEWER)


@pytest.fixture
def posts(wa_app: FastAPI) -> FakePostLookup:
    lookup = FakePostLookup({(BRAND_ID, POST_ID): JOB_ID})
    wa_app.dependency_overrides[lead_loop_module.get_published_post_lookup] = lambda: lookup
    return lookup


def _flags(app: FastAPI, settings: Settings, **flags: object) -> None:
    current = settings.model_copy(update=flags)
    app.dependency_overrides[get_settings] = lambda: current


def _webhook(client: TestClient, payload: dict) -> httpx.Response:
    raw = json.dumps(payload).encode("utf-8")
    return client.post(
        WEBHOOK,
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": compute_signature(APP_SECRET, raw),
        },
    )


def _referral_payload(source_id: str = POST_ID, source_type: str = "post") -> dict:
    payload = load_payload("inbound_text")
    message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
    message["id"] = f"wamid.REF.{uuid4()}"
    message["referral"] = {
        "source_url": "https://fb.me/x",
        "source_id": source_id,
        "source_type": source_type,
        "headline": "عرض",
        "body": "اكتب لنا",
    }
    return payload


# ----------------------------------------------------------------- lead stage route ---
async def test_lead_stage_owner_and_manager_200_viewer_403(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, auth_headers, roles: None, wa_account
) -> None:
    counters.reset()
    conv = await _seed_conversation(wa_repo, wa_account.id)
    url = f"{BASE}/conversations/{conv.id}/lead-stage"
    with TestClient(wa_app) as client:
        res = client.patch(url, json={"lead_stage": "qualified"}, headers=auth_headers(OWNER_ID))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["lead_stage"] == "qualified" and body["objection_code"] is None
        assert body["lead_stage_set_by_user_id"] == str(USER_BY_TENANT[OWNER_ID])
        assert body["lead_stage_at"] is not None

        res = client.patch(url, json={"lead_stage": "quoted"}, headers=auth_headers(AGENCY_ID))
        assert res.status_code == 200, res.text

        res = client.patch(url, json={"lead_stage": "won"}, headers=auth_headers(STRANGER_ID))
        assert res.status_code == 403, res.text

        # the listing exposes the stage (``ConversationPublic.lead_stage``)
        res = client.get(f"{BASE}/conversations", headers=auth_headers(OWNER_ID))
        assert res.status_code == 200
        assert res.json()["items"][0]["lead_stage"] == "quoted"
    assert wa_repo.conversations[conv.id].lead_stage is LeadStage.QUOTED
    assert counters["wa_lead_stage_set"] == 2
    assert wa_repo.attributions == {}  # no ``lost`` ⇒ no owner_input row


async def test_lead_stage_lost_with_objection_writes_owner_input_code_only(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, auth_headers, roles: None, wa_account
) -> None:
    conv = await _seed_conversation(wa_repo, wa_account.id)
    url = f"{BASE}/conversations/{conv.id}/lead-stage"
    with TestClient(wa_app) as client:
        res = client.patch(
            url,
            json={"lead_stage": "lost", "objection_code": "price"},
            headers=auth_headers(OWNER_ID),
        )
        assert res.status_code == 200, res.text
        assert res.json()["objection_code"] == "price"
        # a code with a non-``lost`` stage is ignored (not an error, not stored)
        res = client.patch(
            url,
            json={"lead_stage": "qualified", "objection_code": "timing"},
            headers=auth_headers(OWNER_ID),
        )
        assert res.status_code == 200 and res.json()["objection_code"] is None
        # unknown code / unknown stage / extra field ⇒ 422 (codes only, D-069)
        for bad in (
            {"lead_stage": "lost", "objection_code": "too expensive for me"},
            {"lead_stage": "maybe"},
            {"lead_stage": "lost", "note": "free text"},
        ):
            assert client.patch(url, json=bad, headers=auth_headers(OWNER_ID)).status_code == 422
    (attribution,) = wa_repo.attributions.values()
    assert attribution.grade == "owner_input" and attribution.source_kind == "lead_stage"
    assert attribution.signals == {"objection_code": "price"}
    assert attribution.conversation_id == conv.id and attribution.brand_id == BRAND_ID
    assert "expensive" not in json.dumps(attribution.model_dump(mode="json"))


async def test_lead_stage_unknown_conversation_404_and_feature_off_404(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    settings: Settings,
) -> None:
    conv = await _seed_conversation(wa_repo, wa_account.id)
    with TestClient(wa_app) as client:
        res = client.patch(
            f"{BASE}/conversations/{uuid4()}/lead-stage",
            json={"lead_stage": "won"},
            headers=auth_headers(OWNER_ID),
        )
        assert res.status_code == 404
        assert res.json()["detail"]["error"] == "whatsapp_conversation_not_found"
        _flags(wa_app, settings, feature_lead_loop=False)
        res = client.patch(
            f"{BASE}/conversations/{conv.id}/lead-stage",
            json={"lead_stage": "won"},
            headers=auth_headers(OWNER_ID),
        )
        assert res.status_code == 404
    assert wa_repo.conversations[conv.id].lead_stage is LeadStage.NEW


# ------------------------------------------------------------ webhook attribution ---
def test_webhook_referral_matching_published_post_creates_measured_attribution_once(
    wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, wa_account, posts: FakePostLookup
) -> None:
    counters.reset()
    with TestClient(wa_app) as client:
        first = _webhook(client, _referral_payload())
        assert first.status_code == 200, first.text
        assert len(first.json()["attributed"]) == 1
        again = _webhook(client, _referral_payload())  # second message, same post
        assert again.status_code == 200 and again.json()["attributed"] == []
        unknown = _webhook(client, _referral_payload(source_id="not-a-post"))
        assert unknown.status_code == 200 and unknown.json()["attributed"] == []
    (attribution,) = wa_repo.attributions.values()
    assert attribution.grade == "measured" and attribution.source_kind == "publish_job"
    assert attribution.source_ref == str(JOB_ID)
    assert attribution.signals == {"referral_source_type": "post"}
    assert "اكتب" not in json.dumps(attribution.model_dump(mode="json"), ensure_ascii=False)
    assert counters["wa_lead_attributed"] == 1
    assert posts.calls[0] == (BRAND_ID, POST_ID)


def test_webhook_attribution_disabled_or_without_lookup_writes_nothing(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    wa_account,
    posts: FakePostLookup,
    settings: Settings,
) -> None:
    _flags(wa_app, settings, feature_lead_loop=False)
    with TestClient(wa_app) as client:
        res = _webhook(client, _referral_payload())
        assert res.status_code == 200 and res.json()["attributed"] == []
        assert res.json()["inbound"] == 1  # the message itself is stored as before
    assert wa_repo.attributions == {} and posts.calls == []
    # no lookup bound (publishing removed) ⇒ port yields None ⇒ nothing
    wa_app.dependency_overrides[lead_loop_module.get_published_post_lookup] = lambda: None
    _flags(wa_app, settings, feature_lead_loop=True)
    with TestClient(wa_app) as client:
        res = _webhook(client, _referral_payload())
        assert res.status_code == 200 and res.json()["attributed"] == []
    assert wa_repo.attributions == {}


async def test_lead_loop_service_edge_cases(wa_repo: FakeWhatsAppRepository, wa_account) -> None:
    conv = await _seed_conversation(wa_repo, wa_account.id)
    lookup = FakePostLookup({(BRAND_ID, POST_ID): JOB_ID})
    service = LeadLoopService(wa_repo, posts=lookup)
    assert service.enabled
    assert await service.attribute_from_referral(conv, None) is False
    assert await service.attribute_from_referral(conv, {"source_type": "ad"}) is False
    assert await service.attribute_from_referral(conv, {"source_id": 42}) is False
    assert await service.attribute_from_referral(conv, {"source_id": ""}) is False
    assert lookup.calls == []
    assert await service.attribute_from_referral(conv, {"source_id": POST_ID}) is True
    (row,) = wa_repo.attributions.values()
    assert row.signals == {"referral_source_type": None}
    # set_stage on a vanished conversation returns the input unchanged
    ghost = conv.model_copy(update={"id": uuid4()})
    updated, code = await service.set_stage(
        ghost, stage=LeadStage.LOST, objection_code=None, actor_user_id=OWNER_USER_ID
    )
    assert updated.lead_stage is LeadStage.NEW and code is None
    assert lead_loop_module.get_published_post_lookup() is None


def test_lead_metrics_are_measured_and_pure() -> None:
    assert METRICS["measured_leads_per_post"] == ("leads/post", "measured")
    assert METRICS["lead_close_rate"] == ("ratio", "measured")
    assert lead_close_rate(0, 0) is None and lead_close_rate(3, 1) == 0.75
    assert measured_leads_per_post(5, 0) is None and measured_leads_per_post(5, 4) == 1.25


# ------------------------------------------------------------------- caps (D-082) ---
async def _seed_outbound(repo: FakeWhatsAppRepository, conv_id: UUID, count: int) -> None:
    now = datetime.now(tz=UTC)
    for _ in range(count):
        await repo.insert_inbound_if_new(
            Message(
                conversation_id=conv_id,
                brand_id=BRAND_ID,
                direction=Direction.OUTBOUND,
                wamid=f"wamid.OUT.{uuid4()}",
                kind=MessageKind.TEXT,
                body="رد",
                delivery_status=DeliveryStatus.SENT,
                platform_timestamp=now,
                created_at=now,
            )
        )


async def test_kill_switch_blocks_sends_with_423_and_no_network_call(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    graph_mock: respx.MockRouter,
) -> None:
    conv = await _seed_conversation(wa_repo, wa_account.id)
    await wa_repo.upsert_number_cap(
        NumberCap(account_id=wa_account.id, kill_switch=True, updated_at=datetime.now(tz=UTC))
    )
    route = graph_mock.post(MESSAGES).mock(return_value=_ok())
    with TestClient(wa_app) as client:
        res = client.post(
            f"{BASE}/conversations/{conv.id}/messages",
            json={"body": "hi"},
            headers=auth_headers(OWNER_ID),
        )
        assert res.status_code == 423, res.text
        assert res.json()["detail"]["error"] == "wa_number_disabled"
        res = client.post(
            f"{BASE}/conversations/{conv.id}/template",
            json={"name": "hello_world", "language_code": "en_US"},
            headers=auth_headers(OWNER_ID),
        )
        assert res.status_code == 423, res.text
    assert len(route.calls) == 0
    assert all(m.direction is Direction.INBOUND for m in wa_repo.messages.values())


async def test_daily_cap_reached_is_429_with_shape_and_retry_after(
    wa_app: FastAPI,
    wa_repo: FakeWhatsAppRepository,
    auth_headers,
    roles: None,
    wa_account,
    graph_mock: respx.MockRouter,
) -> None:
    conv = await _seed_conversation(wa_repo, wa_account.id)
    await wa_repo.upsert_number_cap(
        NumberCap(account_id=wa_account.id, daily_reply_cap=2, updated_at=datetime.now(tz=UTC))
    )
    route = graph_mock.post(MESSAGES).mock(return_value=_ok())
    with TestClient(wa_app) as client:
        url = f"{BASE}/conversations/{conv.id}/messages"
        assert (
            client.post(url, json={"body": "1"}, headers=auth_headers(OWNER_ID)).status_code == 201
        )
        assert (
            client.post(url, json={"body": "2"}, headers=auth_headers(OWNER_ID)).status_code == 201
        )
        res = client.post(url, json={"body": "3"}, headers=auth_headers(OWNER_ID))
        assert res.status_code == 429, res.text
        detail = res.json()["detail"]
        assert detail["error"] == "wa_daily_cap_reached"
        assert detail["cap"] == 2 and detail["used"] == 2
        assert 1 <= detail["resets_in_seconds"] <= 86_400
        assert res.headers["Retry-After"] == str(detail["resets_in_seconds"])
    assert len(route.calls) == 2


async def test_default_cap_applies_without_a_row(
    wa_repo: FakeWhatsAppRepository, wa_account
) -> None:
    from src.modules.whatsapp.inbox import DEFAULT_DAILY_REPLY_CAP, WhatsAppInboxService

    conv = await _seed_conversation(wa_repo, wa_account.id)
    await _seed_outbound(wa_repo, conv.id, DEFAULT_DAILY_REPLY_CAP)
    inbox = WhatsAppInboxService.__new__(WhatsAppInboxService)
    inbox._repo = wa_repo
    now = datetime(2026, 3, 1, 23, 59, 30, tzinfo=UTC)
    with pytest.raises(DailyCapReachedError) as exc:
        await inbox._ensure_number_cap(wa_account.id, now=now)
    assert exc.value.cap == DEFAULT_DAILY_REPLY_CAP
    assert exc.value.resets_in_seconds == 30
    await wa_repo.upsert_number_cap(
        NumberCap(account_id=wa_account.id, kill_switch=True, updated_at=now)
    )
    with pytest.raises(NumberDisabledError):
        await inbox._ensure_number_cap(wa_account.id, now=now)


# ---------------------------------------------------------------- admin wa-caps ---
@pytest.fixture
def admin_wa_app(wa_app: FastAPI, settings: Settings) -> FastAPI:
    _flags(wa_app, settings, platform_admin_user_ids=[OWNER_USER_ID])
    return wa_app


def test_wa_caps_requires_platform_admin(wa_app: FastAPI, auth_headers, wa_account) -> None:
    with TestClient(wa_app) as client:
        res = client.patch(
            f"{WA_CAPS}/{wa_account.id}",
            json={"kill_switch": True},
            headers=auth_headers(OWNER_ID),
        )
    assert res.status_code == 403 and res.json()["detail"]["error"] == "platform_admin_required"


async def test_wa_caps_admin_404_422_200_and_partial_updates(
    admin_wa_app: FastAPI, wa_repo: FakeWhatsAppRepository, auth_headers, wa_account
) -> None:
    conv = await _seed_conversation(wa_repo, wa_account.id)
    await _seed_outbound(wa_repo, conv.id, 3)
    headers = auth_headers(OWNER_ID)
    with TestClient(admin_wa_app) as client:
        res = client.patch(f"{WA_CAPS}/{uuid4()}", json={"kill_switch": True}, headers=headers)
        assert res.status_code == 404, res.text
        assert res.json()["detail"]["error"] == "whatsapp_account_not_found"
        res = client.patch(f"{WA_CAPS}/{wa_account.id}", json={}, headers=headers)
        assert (
            res.status_code == 422 and res.json()["detail"]["error"] == "wa_caps_nothing_to_change"
        )
        assert (
            client.patch(
                f"{WA_CAPS}/{wa_account.id}", json={"daily_reply_cap": 0}, headers=headers
            ).status_code
            == 422
        )
        res = client.patch(
            f"{WA_CAPS}/{wa_account.id}", json={"daily_reply_cap": 50}, headers=headers
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body == {
            "account_id": str(wa_account.id),
            "daily_reply_cap": 50,
            "kill_switch": False,
            "used_today": 3,
            "updated_at": body["updated_at"],
        }
        res = client.patch(
            f"{WA_CAPS}/{wa_account.id}", json={"kill_switch": True}, headers=headers
        )
        assert res.status_code == 200
        assert res.json()["daily_reply_cap"] == 50 and res.json()["kill_switch"] is True
    cap = wa_repo.number_caps[wa_account.id]
    assert cap.daily_reply_cap == 50 and cap.kill_switch is True


def test_cap_models_and_exceptions() -> None:
    cap = NumberCap(account_id=uuid4(), updated_at=datetime.now(tz=UTC))
    assert cap.daily_reply_cap == 300 and cap.kill_switch is False
    exc = DailyCapReachedError(cap=300, used=300, resets_in_seconds=10)
    assert exc.error == "wa_daily_cap_reached" and exc.resets_in_seconds == 10
    assert NumberDisabledError().error == "wa_number_disabled"
    assert timedelta(days=1).total_seconds() == 86_400
