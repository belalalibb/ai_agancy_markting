"""SPRINT-12 B-2 — lead loop (D-079): owner-set funnel stage + measured attribution.

* ``set_stage`` writes the three ``wa_conversations`` columns (0016). A ``lost`` stage may carry
  an **objection code** — stored as a code in the conversation's lead signal, never as text
  (D-069). ``wa_learning_signals`` keeps its 0009 shape (one row per reply draft, CHECK on
  ``signal``); the stage itself *is* the owner's signal, so no extra row is written there.
* ``attribute_from_referral`` runs on the webhook: a Click-to-WhatsApp ``referral.source_id``
  that matches a **published** ``publish_jobs.external_post_id`` of the same brand ⇒ one
  ``lead_attributions(grade='measured', source_kind='publish_job')`` row. No inference in V1
  (``inferred`` ⇒ V1.1). Re-deliveries hit the unique key and are no-ops.

``FEATURE_LEAD_LOOP=false`` ⇒ the route answers 404 and the webhook writes nothing here —
see ``whatsapp/REMOVABILITY.md``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from src.core.metrics import counters
from src.modules.whatsapp.domain.models import (
    Conversation,
    LeadAttribution,
    LeadStage,
    ObjectionCode,
)
from src.modules.whatsapp.domain.repository import WhatsAppRepository

logger = logging.getLogger(__name__)

SOURCE_KIND_PUBLISH_JOB = "publish_job"
REFERRAL_SOURCE_KEY = "source_id"


class PublishedPostLookup(Protocol):
    """Consumer-owned port (D-016): *"did this brand publish a post with this external id?"*
    ``src/main.py`` binds the publishing adapter; ``None`` ⇒ no attribution (module removed)."""

    async def published_job_id(self, brand_id: UUID, external_post_id: str) -> UUID | None: ...


class LeadLoopService:
    def __init__(
        self,
        repo: WhatsAppRepository,
        *,
        posts: PublishedPostLookup | None = None,
        enabled: bool = True,
    ) -> None:
        self._repo = repo
        self._posts = posts
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def set_stage(
        self,
        conversation: Conversation,
        *,
        stage: LeadStage,
        objection_code: ObjectionCode | None,
        actor_user_id: UUID,
        now: datetime | None = None,
    ) -> tuple[Conversation, ObjectionCode | None]:
        now = now or datetime.now(tz=UTC)
        code = objection_code if stage is LeadStage.LOST else None  # a code only with ``lost``
        updated = await self._repo.set_lead_stage(
            conversation.id, stage=stage, actor_user_id=actor_user_id, now=now
        )
        if updated is None:  # the row vanished between load and update
            return conversation, code
        if code is not None:
            # D-069: the objection reaches ``lead_attributions.signals`` as a **code** on an
            # ``owner_input`` row keyed by the stage change, so it survives without any text.
            await self._repo.add_lead_attribution(
                LeadAttribution(
                    brand_id=conversation.brand_id,
                    conversation_id=conversation.id,
                    grade="owner_input",
                    source_kind="lead_stage",
                    source_ref=f"lost:{now.date().isoformat()}",
                    signals={"objection_code": code.value},
                    created_at=now,
                )
            )
        counters.increment("wa_lead_stage_set")
        logger.info(
            "wa lead stage: brand=%s conv=%s stage=%s objection=%s",
            conversation.brand_id,
            conversation.id,
            stage.value,
            code.value if code else None,
        )
        return updated, code

    async def attribute_from_referral(
        self,
        conversation: Conversation,
        referral: dict[str, object] | None,
        *,
        now: datetime | None = None,
    ) -> bool:
        """``True`` when a new ``measured`` attribution row was written."""
        if not self._enabled or self._posts is None or not referral:
            return False
        source_id = referral.get(REFERRAL_SOURCE_KEY)
        if not isinstance(source_id, str) or not source_id:
            return False
        job_id = await self._posts.published_job_id(conversation.brand_id, source_id[:128])
        if job_id is None:
            return False
        now = now or datetime.now(tz=UTC)
        inserted = await self._repo.add_lead_attribution(
            LeadAttribution(
                brand_id=conversation.brand_id,
                conversation_id=conversation.id,
                grade="measured",
                source_kind=SOURCE_KIND_PUBLISH_JOB,
                source_ref=str(job_id),
                signals={"referral_source_type": _code(referral.get("source_type"))},
                created_at=now,
            )
        )
        if inserted:
            counters.increment("wa_lead_attributed")
        return inserted


def _code(value: object) -> str | None:
    """Referral ``source_type`` is a Meta enum (``ad`` / ``post``) — keep it as a short code."""
    return value[:16] if isinstance(value, str) and value else None


def get_published_post_lookup() -> PublishedPostLookup | None:
    """FastAPI dependency — overridden from ``src/main.py``; ``None`` ⇒ no attribution."""
    return None


__all__ = [
    "LeadLoopService",
    "PublishedPostLookup",
    "get_published_post_lookup",
]
