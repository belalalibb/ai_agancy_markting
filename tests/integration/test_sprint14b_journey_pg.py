"""SPRINT-14-B §8 — the browser journey on a *real* PostgreSQL, through the HTML forms only.

register ⇒ Arabic brand ⇒ profile ⇒ competitor ⇒ evidence ⇒ Facebook connect ⇒ plan drafts ⇒
create ⇒ submit ⇒ approve ⇒ schedule. Graph and the AI model are faked at the network /
gateway edge; every service and repository is the real one. The journey never calls
``/api/v1`` itself — it posts the forms the screens render (cookie jar + CSRF field), and SQL
checks the result: the job is ``queued`` with ``attempt_count = 0`` and nothing was published
(D-055)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.modules.ai_gateway.contract import Usage
from src.modules.ai_gateway.gateway import CompletionResult
from src.modules.brand_brain.router import get_brain_gateway
from tests.integration.test_postgres_persistence import _mock_graph, _rows

pytestmark = pytest.mark.integration

PASSWORD = "Correct-Horse-9-battery"  # noqa: S105 - throwaway test credential
BRAND_NAME = "مقهى النيل"


def _csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)  # type: ignore[union-attr]


def _status_of(item_id: object) -> str:
    return _rows("SELECT status FROM content_items WHERE id = :i", {"i": item_id})[0][0]


def test_journey_register_to_schedule_on_pg(
    integration_app: FastAPI, graph_mock: respx.MockRouter
) -> None:
    _mock_graph(graph_mock, pages=2)
    plan: dict = {}
    calls: list = []

    class Scripted:
        name, model = "scripted", "scripted-journey"

        async def complete(self, req):  # noqa: ANN001, ANN202
            calls.append(req)
            return CompletionResult(
                text=json.dumps(plan, ensure_ascii=False),
                provider="scripted",
                model="scripted-journey",
                usage=Usage(prompt_tokens=100, completion_tokens=50),
                credential_mode=req.credential_mode,
                latency_ms=3,
            )

    integration_app.dependency_overrides[get_brain_gateway] = Scripted

    with TestClient(integration_app, follow_redirects=False) as c:
        # 1. register: one POST, session cookies set ------------------------------------------
        res = c.post(
            "/app/register",
            data={
                "email": "journey@example.com",
                "password": PASSWORD,
                "password_confirm": PASSWORD,
                "tenant_name": "Journey Agency",
                "tenant_slug": "journey-agency",
            },
        )
        assert res.status_code == 303, res.text

        # 2. Arabic brand name + sector (the first-brand form) ---------------------------------
        brands_page = c.get(res.headers["location"]).text
        res = c.post(
            "/app/brands",
            data={"csrf": _csrf(brands_page), "name": BRAND_NAME, "sector": "restaurants"},
        )
        assert res.status_code == 303, res.text
        brand_id = _rows("SELECT id FROM brands WHERE name = :n", {"n": BRAND_NAME})[0][0]
        assert _rows("SELECT sector FROM brand_profiles")[0][0] == "restaurants"
        brain = f"/app/b/{brand_id}/brain"

        # 3. profile from the form; a stale ``version`` ⇒ 412 and the user's text is kept ----
        page = c.get(brain).text
        version = re.search(r'name="version" value="(\d+)"', page).group(1)  # type: ignore[union-attr]
        fields = {
            "csrf": _csrf(page),
            "version": version,
            "sector": "restaurants",
            "p_name": "قهوة مختصة",
            "p_desc": "حبوب محمصة طازجة",
            "p_price": "",  # a browser always posts all four product inputs
            "p_url": "",
            "t_voice": "ودود",
            "t_formality": "casual",
            "t_emoji": "light",
            "g_kind": "awareness",
            "g_target": "زيادة المتابعين",
            "g_days": "30",
        }
        saved = c.post(brain, data=fields)
        assert saved.status_code == 303, saved.text
        stale = c.post(brain, data={**fields, "p_name": "نص محفوظ"})
        assert stale.status_code == 412 and "نص محفوظ" in stale.text
        assert _rows("SELECT version FROM brand_profiles")[0][0] == 2

        # 4. competitor + measured evidence -------------------------------------------------------
        page = c.get(brain).text
        res = c.post(f"{brain}/competitors", data={"csrf": _csrf(page), "c_name": "منافس الحي"})
        assert res.status_code in (200, 303), res.text
        assert _rows("SELECT COUNT(*) FROM brand_competitors")[0][0] == 1
        res = c.post(f"{brain}/evidence/refresh", data={"csrf": _csrf(c.get(brain).text)})
        assert res.status_code in (200, 303), res.text
        evidence = [
            str(r[0])
            for r in _rows("SELECT id FROM brand_evidence WHERE brand_id = :b", {"b": brand_id})
        ]
        assert evidence

        # 5. Facebook connect: start ⇒ Meta's URL ⇒ callback after login ⇒ choose a Page -----
        settings_url = f"/app/b/{brand_id}/settings"
        res = c.post(
            f"{settings_url}/connections/start", data={"csrf": _csrf(c.get(settings_url).text)}
        )
        assert res.status_code == 303, res.text
        state = parse_qs(urlparse(res.headers["location"]).query)["state"][0]
        res = c.get("/app/social/meta/callback", params={"code": "CODE", "state": state})
        assert res.status_code == 303 and res.headers["location"].endswith("/pages"), res.text
        pages_url = res.headers["location"]
        res = c.post(pages_url, data={"csrf": _csrf(c.get(pages_url).text), "page_ids": ["1001"]})
        assert res.status_code == 303, res.text

        # 6. plan ⇒ drafts only (the model is scripted; ids are the real evidence rows) ------
        plan.update(
            {
                "items": [
                    {
                        "title": "منشور [مُقاس]",
                        "channel": "facebook_page",
                        "body": "نص المنشور [estimated] مع دعوة إلى فعل.",
                        "rationale": "لأن الجمهور يتفاعل صباحاً",
                        "evidence_ids": evidence[:1],
                    }
                ]
            }
        )
        diagnosis = f"/app/b/{brand_id}/diagnosis"
        res = c.post(f"{diagnosis}/content-plan", data={"csrf": _csrf(c.get(diagnosis).text)})
        assert res.status_code == 200, res.text
        assert calls, "the scripted gateway was never reached"
        assert _rows("SELECT status FROM content_items") == [("draft",)]
        assert _rows("SELECT COUNT(*) FROM publish_jobs")[0][0] == 0

        # 7. create: a second draft the human writes ------------------------------------------
        content = f"/app/b/{brand_id}/content"
        listing = c.get(content, params={"status": "draft"}).text
        res = c.post(
            f"{content}/new",
            data={
                "csrf": _csrf(listing),
                "channel": "facebook_page",
                "body": "عرض نهاية الأسبوع",
                "target_page_id": "1001",
            },
        )
        assert res.status_code == 303, res.text
        assert _rows("SELECT COUNT(*) FROM content_items")[0][0] == 2
        item_id = _rows("SELECT item_id FROM content_revisions WHERE body LIKE :b", {"b": "عرض%"})[
            0
        ][0]

        # 8. submit ⇒ approve ⇒ schedule: three separate human actions (D-055) ----------------
        def act_on(action: str, **extra: str) -> None:
            html = c.get(content, params={"status": _status_of(item_id)}).text
            res = c.post(
                f"{content}/{item_id}/{action}", data={"csrf": _csrf(html), "status": "", **extra}
            )
            assert res.status_code == 200, (action, res.text)

        act_on("submit")
        assert _status_of(item_id) == "pending_approval"
        assert _rows("SELECT COUNT(*) FROM publish_jobs")[0][0] == 0
        act_on("approve")
        assert _status_of(item_id) == "approved"
        assert _rows("SELECT COUNT(*) FROM publish_jobs")[0][0] == 0
        when = (datetime.now(tz=UTC) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
        act_on("schedule", scheduled_for=when, target_page_id="1001")

    # 9. SQL: queued, never attempted, nothing published ---------------------------------------
    assert _status_of(item_id) == "scheduled"
    assert _rows("SELECT status, attempt_count FROM publish_jobs") == [("queued", 0)]
    assert _rows("SELECT COUNT(*) FROM publish_attempts")[0][0] == 0
    assert not [r for r in graph_mock.calls if "/feed" in str(r.request.url)]
