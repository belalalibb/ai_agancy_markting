"""Brand Brain advisor (SPRINT-10 B-3): diagnosis + content plan ⇒ *drafts under approval*.

What the model produces never becomes anything but a ``proposed`` recommendation or a
``content_items(status=draft)`` row (D-055). Two server-side rules the model cannot bypass:

* **grade tags** — every ``rationale`` / ``text`` ends with ``[مُقاس]`` / ``[مُستنتج]`` /
  ``[مُقدَّر]`` computed from the evidence rows the model *cited* (fake tags the model wrote
  itself are stripped first);
* **evidence or nothing** — a recommendation / item whose ``evidence_ids`` resolve to no row
  of this brand is dropped and counted (``recommendations_dropped`` / ``items_dropped``).

Learning signals (``learning_signals``, no text — D-069) are written on ``accept`` /
``dismiss`` / ``materialized`` with the prompt/model identity taken from the recommendation's
provenance.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import IntegrityError

from src.modules.ai_gateway.gateway import AIGateway, CompletionRequest, CompletionResult
from src.modules.auth.domain.rate_limit import RateLimiter
from src.modules.brand_brain.consent import BrandConsentReader, may_write_signal
from src.modules.brand_brain.domain.models import (
    MAX_CONTENT_PLAN_ITEMS,
    MAX_DIAGNOSIS_RECOMMENDATIONS,
    MAX_RATIONALE_CHARS,
    MAX_RECOMMENDATION_TITLE,
    MAX_REPLY_TEMPLATES,
    BrandProfile,
    BrandProfileRevision,
    Evidence,
    EvidenceKindsSummary,
    LearningSignal,
    Recommendation,
    Tone,
)
from src.modules.brand_brain.domain.repository import BrandBrainRepository
from src.modules.brand_brain.exceptions import (
    BrainRateLimitedError,
    InvalidModelOutputError,
    ProfileConflictError,
    RecommendationAlreadyDecidedError,
    RecommendationNotFoundError,
)
from src.modules.brand_brain.prompts import (
    BRAND_DIAGNOSIS_V1,
    CONTENT_PLAN_V1,
    PromptTemplate,
    evidence_lines,
    profile_block,
)
from src.modules.content.domain.models import Channel, ContentItem
from src.modules.content.service import ContentService

logger = logging.getLogger(__name__)

DIAGNOSE_BUCKET = "brain:diagnose"
DIAGNOSE_LIMIT = 5
DIAGNOSE_WINDOW_SECONDS = 3600
CONTENT_PLAN_BUCKET = "brain:content-plan"
CONTENT_PLAN_LIMIT = 5
CONTENT_PLAN_WINDOW_SECONDS = 3600
EVIDENCE_WINDOW_ROWS = 60
MAX_REPLY_TEMPLATE_LEN = 600  # A-6: a ``reply_template`` body beyond this is dropped at parse

# Server-side grade tags (Arabic, directive §2 B-3). Any bracketed tag the model wrote —
# Arabic or English — is removed before ours is appended.
GRADE_TAG: dict[str, str] = {
    "measured": "[مُقاس]",
    "inferred": "[مُستنتج]",
    "estimated": "[مُقدَّر]",
}
_GRADE_ORDER = ("measured", "inferred", "estimated")
_FAKE_TAG_RE = re.compile(
    r"\s*\[(?:مُ?قاس|مُ?ستنتج|مُ?قدَّ?ر|measured|inferred|estimated)\]", re.IGNORECASE
)
_CHANNEL_ALIASES: dict[str, Channel] = {
    "facebook": Channel.FACEBOOK_PAGE,
    "facebook_page": Channel.FACEBOOK_PAGE,
    "instagram": Channel.INSTAGRAM,
}


# ------------------------------------------------------------ model output --
class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Point(_Out):
    text: str = Field(min_length=1, max_length=600)
    evidence_ids: list[UUID] = Field(default_factory=list)


class _RecommendationOut(_Out):
    kind: Literal["gap_action", "tone_adjustment", "reply_template"]
    title: str = Field(min_length=1, max_length=MAX_RECOMMENDATION_TITLE)
    rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_CHARS)
    evidence_ids: list[UUID] = Field(default_factory=list)
    template: str | None = None  # A-6: length policed in diagnose() (drop, not 502)


class DiagnosisOutput(_Out):
    """Strict shape of ``brand_diagnosis_v1`` — anything else is ``invalid_output``."""

    summary: str = Field(min_length=1, max_length=400)
    strengths: list[_Point] = Field(default_factory=list, max_length=20)
    gaps: list[_Point] = Field(default_factory=list, max_length=20)
    # A-7: raw rows — validated one by one in ``diagnose`` so an unknown ``kind`` drops that
    # recommendation only (WARNING + ``dropped``) instead of failing the whole diagnosis.
    recommendations: list[Any] = Field(default_factory=list, max_length=20)


def parse_recommendations(raw: list[Any]) -> tuple[list[_RecommendationOut], int]:
    """Per-recommendation validation (A-7): ``(valid, dropped)``."""
    valid: list[_RecommendationOut] = []
    dropped = 0
    for row in raw:
        try:
            valid.append(_RecommendationOut.model_validate(row))
        except (ValidationError, TypeError):
            kind = row.get("kind") if isinstance(row, dict) else type(row).__name__
            logger.warning("advisor: dropped recommendation (invalid shape) kind=%r", kind)
            dropped += 1
    return valid, dropped


class _PlanItemOut(_Out):
    title: str = Field(min_length=1, max_length=MAX_RECOMMENDATION_TITLE)
    channel: str = Field(min_length=1, max_length=32)
    body: str = Field(min_length=1, max_length=1800)
    rationale: str = Field(default="", max_length=400)
    evidence_ids: list[UUID] = Field(default_factory=list)


class ContentPlanOutput(_Out):
    items: list[_PlanItemOut] = Field(default_factory=list, max_length=20)


def parse_output[T: BaseModel](text: str, model: type[T]) -> T:
    """``json_mode`` already guarantees a JSON object; the *schema* is checked here."""
    try:
        return model.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise InvalidModelOutputError() from exc


# ---------------------------------------------------------- grading helper --
def strip_fake_tags(text: str) -> str:
    return _FAKE_TAG_RE.sub("", text).strip()


def grade_tag(rows: list[Evidence]) -> str:
    """The **weakest** grade among the cited rows decides the tag: a sentence that leans on
    one estimate is an estimate, whatever else it cites."""
    kinds = {r.evidence_kind for r in rows}
    for kind in reversed(_GRADE_ORDER):  # estimated ⇒ inferred ⇒ measured
        if kind in kinds:
            return GRADE_TAG[kind]
    return GRADE_TAG["measured"]


def tag_text(text: str, rows: list[Evidence], *, limit: int) -> str:
    tag = grade_tag(rows)
    cleaned = strip_fake_tags(text)
    room = limit - len(tag) - 1
    return f"{cleaned[:room].rstrip()} {tag}"


# ------------------------------------------------------------------ results --
@dataclass(frozen=True)
class DiagnosisResult:
    summary: str
    strengths: list[tuple[str, list[UUID]]]
    gaps: list[tuple[str, list[UUID]]]
    evidence_used: list[Evidence]
    recommendations: list[Recommendation]
    recommendations_dropped: int
    prompt_version: str


@dataclass(frozen=True)
class ContentPlanResult:
    items: list[tuple[Recommendation, ContentItem]]
    items_dropped: int
    prompt_version: str


@dataclass(frozen=True)
class DecisionResult:
    recommendation: Recommendation
    profile_revision: BrandProfileRevision | None = None
    signals: list[LearningSignal] = field(default_factory=list)


# ------------------------------------------------------------------ service --
class BrandAdvisor:
    def __init__(
        self,
        repo: BrandBrainRepository,
        *,
        gateway: AIGateway,
        limiter: RateLimiter,
        content: ContentService | None = None,
        consent: BrandConsentReader | None = None,
    ) -> None:
        self._repo = repo
        self._consent = consent  # SPRINT-11 C-6 (D-080): ``none`` ⇒ no ``learning_signals`` row
        # SPRINT-11 B-4: ``gateway`` is the ``BudgetGuard`` (daily tokens, platform cap,
        # ``ai_cost_log`` reservation — D-075); the advisor only rate-limits.
        self._gateway = gateway
        self._limiter = limiter
        self._content = content

    # ------------------------------------------------------------ guards ----
    async def _guard(self, brand_id: UUID, bucket: str, limit: int, window: int) -> None:
        retry = self._limiter.hit(bucket, str(brand_id), limit, window)
        if retry is not None:
            raise BrainRateLimitedError(retry)

    async def _context(self, brand_id: UUID) -> tuple[BrandProfile, list[Evidence]]:
        profile = await self._repo.get_profile(brand_id)
        if profile is None:
            profile = BrandProfile(brand_id=brand_id, version=1, updated_at=datetime.now(tz=UTC))
        rows = await self._repo.latest_evidence(brand_id, limit=EVIDENCE_WINDOW_ROWS)
        return profile, rows

    async def _complete(
        self,
        template: PromptTemplate,
        *,
        purpose: str,
        brand_id: UUID,
        tenant_id: UUID,
        profile: BrandProfile,
        rows: list[Evidence],
        credential_mode: str,
    ) -> CompletionResult:
        user = (
            f"PROFILE:\n{profile_block(profile)}\n\nEVIDENCE ({len(rows)} rows):\n"
            f"{evidence_lines(rows)}"
        )
        req = CompletionRequest(
            purpose=purpose,  # type: ignore[arg-type]
            system=template.system,
            user=user,
            max_tokens=template.max_tokens,
            temperature=template.temperature,
            brand_id=brand_id,
            tenant_id=tenant_id,
            credential_mode=credential_mode,  # type: ignore[arg-type]
            json_mode=True,
            prompt_version=template.prompt_version,
        )
        return await self._gateway.complete(req)

    @staticmethod
    def _provenance(
        template: PromptTemplate, result: CompletionResult, now: datetime
    ) -> dict[str, Any]:
        return {
            # public (D-066)
            "kind": "ai_generated",
            "generated_at": now.isoformat(),
            "prompt_version": template.prompt_version,
            # private — OWNER/MANAGER only
            "prompt_hash": template.prompt_hash,
            "provider": result.provider,
            "model": result.model,
            "credential_mode": result.credential_mode,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "latency_ms": result.latency_ms,
        }

    # ---------------------------------------------------------- diagnose ----
    async def diagnose(
        self, brand_id: UUID, *, tenant_id: UUID, credential_mode: str = "platform"
    ) -> DiagnosisResult:
        await self._guard(brand_id, DIAGNOSE_BUCKET, DIAGNOSE_LIMIT, DIAGNOSE_WINDOW_SECONDS)
        profile, rows = await self._context(brand_id)
        by_id = {r.id: r for r in rows}
        result = await self._complete(
            BRAND_DIAGNOSIS_V1,
            purpose="brand_diagnosis",
            brand_id=brand_id,
            tenant_id=tenant_id,
            profile=profile,
            rows=rows,
            credential_mode=credential_mode,
        )
        out = parse_output(result.text, DiagnosisOutput)
        now = datetime.now(tz=UTC)
        provenance = self._provenance(BRAND_DIAGNOSIS_V1, result, now)

        used: dict[UUID, Evidence] = {}

        def _cited(ids: list[UUID]) -> list[Evidence]:
            found = [by_id[i] for i in dict.fromkeys(ids) if i in by_id]
            for ev in found:
                used.setdefault(ev.id, ev)
            return found

        def _points(points: list[_Point]) -> list[tuple[str, list[UUID]]]:
            tagged = []
            for p in points:
                cited = _cited(p.evidence_ids)
                if not cited:
                    continue  # a claim without evidence is not shown (§2-0 rule 1)
                tagged.append((tag_text(p.text, cited, limit=600), [e.id for e in cited]))
            return tagged

        strengths = _points(out.strengths)
        gaps = _points(out.gaps)
        recommendations: list[Recommendation] = []
        parsed, dropped = parse_recommendations(out.recommendations)  # A-7
        for rec in parsed:
            cited = _cited(rec.evidence_ids)
            if not cited or len(recommendations) >= MAX_DIAGNOSIS_RECOMMENDATIONS:
                dropped += 1
                continue
            rec_provenance = dict(provenance)
            if rec.kind == "reply_template":
                template = strip_fake_tags(rec.template or "")
                if not template or len(template) > MAX_REPLY_TEMPLATE_LEN:
                    # A-6: never store an empty / oversized template body.
                    logger.warning("advisor: dropped reply_template (empty)")
                    dropped += 1
                    continue
                rec_provenance["template"] = template
            recommendations.append(
                Recommendation(
                    brand_id=brand_id,
                    kind=rec.kind,
                    title=strip_fake_tags(rec.title)[:MAX_RECOMMENDATION_TITLE] or rec.kind,
                    rationale=tag_text(rec.rationale, cited, limit=MAX_RATIONALE_CHARS),
                    evidence_ids=[e.id for e in cited],
                    evidence_kinds_summary=EvidenceKindsSummary.of(cited),
                    provenance=rec_provenance,
                    created_at=now,
                )
            )
        saved = await self._repo.add_recommendations(recommendations)
        logger.info(
            "brain diagnose: brand=%s evidence=%s recommendations=%s dropped=%s model=%s",
            brand_id,
            len(used),
            len(saved),
            dropped,
            result.model,
        )
        return DiagnosisResult(
            summary=strip_fake_tags(out.summary)[:400],
            strengths=strengths,
            gaps=gaps,
            evidence_used=list(used.values()),
            recommendations=saved,
            recommendations_dropped=dropped,
            prompt_version=BRAND_DIAGNOSIS_V1.prompt_version,
        )

    # ------------------------------------------------------ content plan ----
    async def content_plan(
        self,
        brand_id: UUID,
        *,
        tenant_id: UUID,
        actor_user_id: UUID,
        credential_mode: str = "platform",
    ) -> ContentPlanResult:
        if self._content is None:  # pragma: no cover - wiring guard
            raise InvalidModelOutputError()
        await self._guard(
            brand_id, CONTENT_PLAN_BUCKET, CONTENT_PLAN_LIMIT, CONTENT_PLAN_WINDOW_SECONDS
        )
        profile, rows = await self._context(brand_id)
        by_id = {r.id: r for r in rows}
        result = await self._complete(
            CONTENT_PLAN_V1,
            purpose="content_plan",
            brand_id=brand_id,
            tenant_id=tenant_id,
            profile=profile,
            rows=rows,
            credential_mode=credential_mode,
        )
        out = parse_output(result.text, ContentPlanOutput)
        now = datetime.now(tz=UTC)
        provenance = self._provenance(CONTENT_PLAN_V1, result, now)
        items: list[tuple[Recommendation, ContentItem]] = []
        dropped = 0
        for raw in out.items:
            cited = [by_id[i] for i in dict.fromkeys(raw.evidence_ids) if i in by_id]
            channel = _CHANNEL_ALIASES.get(raw.channel.strip().lower())
            if not cited or channel is None or len(items) >= MAX_CONTENT_PLAN_ITEMS:
                dropped += 1
                continue
            recommendation = Recommendation(
                brand_id=brand_id,
                kind="content_plan_item",
                title=strip_fake_tags(raw.title)[:MAX_RECOMMENDATION_TITLE] or "content",
                rationale=tag_text(raw.rationale or raw.title, cited, limit=MAX_RATIONALE_CHARS),
                evidence_ids=[e.id for e in cited],
                evidence_kinds_summary=EvidenceKindsSummary.of(cited),
                provenance={**provenance, "channel": channel.value},
                created_at=now,
            )
            # The draft is created through the existing content service (status=draft,
            # revision 1) and *then* overwritten with the AI provenance — never
            # ``pending_approval``, zero approval events.
            item = await self._content.create_item(
                brand_id,
                actor_tenant_id=tenant_id,
                channel=channel,
                body=strip_fake_tags(raw.body),
                target_page_id=None,
            )
            item = await self._content.update_body(
                brand_id,
                item.id,
                body=strip_fake_tags(raw.body),
                actor_tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                provenance={**provenance, "recommendation_id": str(recommendation.id)},
            )
            recommendation = recommendation.model_copy(
                update={
                    "status": "materialized",
                    "decided_by_user_id": actor_user_id,
                    "decided_at": now,
                    "materialized_item_id": item.id,
                }
            )
            items.append((recommendation, item))
        await self._repo.add_recommendations([rec for rec, _ in items])
        # SPRINT-11.1 A-1 (E1, D-080): ``learning_consent='none'`` ⇒ the plan is still
        # materialised, but no ``learning_signals`` row is written for any of its items.
        if await may_write_signal(self._consent, brand_id):
            for rec, _ in items:
                await self._repo.add_learning_signal(
                    self._signal(rec, "materialized", now, {"channel": rec.provenance["channel"]})
                )
        logger.info(
            "brain content plan: brand=%s items=%s dropped=%s model=%s",
            brand_id,
            len(items),
            dropped,
            result.model,
        )
        return ContentPlanResult(
            items=items, items_dropped=dropped, prompt_version=CONTENT_PLAN_V1.prompt_version
        )

    # ---------------------------------------------------------- decisions ----
    async def list_recommendations(
        self, brand_id: UUID, *, status: str | None, limit: int
    ) -> list[Recommendation]:
        return await self._repo.list_recommendations(brand_id, status=status, limit=limit)

    async def accept(
        self, brand_id: UUID, recommendation_id: UUID, *, actor_user_id: UUID
    ) -> DecisionResult:
        rec = await self._locked_proposed(brand_id, recommendation_id)
        now = datetime.now(tz=UTC)
        revision: BrandProfileRevision | None = None
        if rec.kind == "reply_template":
            revision = await self._store_reply_template(rec, actor_user_id, now)
        moved = await self._repo.decide_recommendation(
            rec.id,
            expected_status="proposed",
            new_status="accepted",
            actor_user_id=actor_user_id,
            decided_at=now,
        )
        if not moved:
            raise RecommendationAlreadyDecidedError(rec.status)
        decided = rec.model_copy(
            update={"status": "accepted", "decided_by_user_id": actor_user_id, "decided_at": now}
        )
        signals = await self._record_signal(decided, "accepted", now)
        logger.info(
            "brain recommendation accepted: %s kind=%s by=%s", rec.id, rec.kind, actor_user_id
        )
        return DecisionResult(recommendation=decided, profile_revision=revision, signals=signals)

    async def dismiss(
        self, brand_id: UUID, recommendation_id: UUID, *, actor_user_id: UUID
    ) -> DecisionResult:
        rec = await self._locked_proposed(brand_id, recommendation_id)
        now = datetime.now(tz=UTC)
        moved = await self._repo.decide_recommendation(
            rec.id,
            expected_status="proposed",
            new_status="dismissed",
            actor_user_id=actor_user_id,
            decided_at=now,
        )
        if not moved:
            raise RecommendationAlreadyDecidedError(rec.status)
        decided = rec.model_copy(
            update={"status": "dismissed", "decided_by_user_id": actor_user_id, "decided_at": now}
        )
        signals = await self._record_signal(decided, "dismissed", now)
        logger.info(
            "brain recommendation dismissed: %s kind=%s by=%s", rec.id, rec.kind, actor_user_id
        )
        return DecisionResult(recommendation=decided, signals=signals)

    async def _record_signal(
        self, rec: Recommendation, signal: str, now: datetime
    ) -> list[LearningSignal]:
        """D-080: ``learning_consent='none'`` ⇒ the decision stands, no signal row."""
        if not await may_write_signal(self._consent, rec.brand_id):
            return []
        return [await self._repo.add_learning_signal(self._signal(rec, signal, now))]

    async def _locked_proposed(self, brand_id: UUID, recommendation_id: UUID) -> Recommendation:
        rec = await self._repo.get_recommendation_for_update(brand_id, recommendation_id)
        if rec is None:
            raise RecommendationNotFoundError()
        if rec.status != "proposed":
            raise RecommendationAlreadyDecidedError(rec.status)
        return rec

    async def _store_reply_template(
        self, rec: Recommendation, actor_user_id: UUID, now: datetime
    ) -> BrandProfileRevision | None:
        """``reply_template`` accepted ⇒ ``brand_profiles.tone.reply_templates[]`` (≤10,
        newest kept) with an ``ai_suggested`` revision — **after accept only**."""
        template = str(rec.provenance.get("template") or "").strip() or rec.title
        profile = await self._repo.get_profile(rec.brand_id)
        if profile is None:
            profile = BrandProfile(brand_id=rec.brand_id, version=1, updated_at=now)
            templates = [template]
            updated = profile.model_copy(
                update={
                    "tone": Tone(**{**profile.tone.model_dump(), "reply_templates": templates}),
                    "updated_by_user_id": actor_user_id,
                    "updated_at": now,
                }
            )
            try:
                await self._repo.insert_profile(updated)
            except IntegrityError as exc:
                # A-6: a concurrent accept created the profile a moment ago ⇒ 409, not 500.
                raise ProfileConflictError() from exc
        else:
            templates = [t for t in profile.tone.reply_templates if t != template]
            templates = ([*templates, template])[-MAX_REPLY_TEMPLATES:]
            updated = profile.model_copy(
                update={
                    "tone": Tone(**{**profile.tone.model_dump(), "reply_templates": templates}),
                    "version": profile.version + 1,
                    "updated_by_user_id": actor_user_id,
                    "updated_at": now,
                }
            )
            if not await self._repo.update_profile_cas(updated, expected_version=profile.version):
                raise RecommendationAlreadyDecidedError("proposed")
        revision = BrandProfileRevision(
            brand_id=rec.brand_id,
            version=updated.version,
            snapshot=updated.snapshot(),
            changed_by_user_id=actor_user_id,
            source="ai_suggested",
            created_at=now,
        )
        return await self._repo.add_revision(revision)

    @staticmethod
    def _signal(
        rec: Recommendation,
        signal: str,
        now: datetime,
        extra: dict[str, Any] | None = None,
    ) -> LearningSignal:
        summary = rec.evidence_kinds_summary
        features: dict[str, Any] = {
            "kind": rec.kind,
            "evidence_count": len(rec.evidence_ids),
            "measured": summary.measured,
            "inferred": summary.inferred,
            "estimated": summary.estimated,
            "rationale_chars": len(rec.rationale),
            **(extra or {}),
        }
        purpose = "content_plan" if rec.kind == "content_plan_item" else "brand_diagnosis"
        return LearningSignal(
            brand_id=rec.brand_id,
            purpose=purpose,
            subject_id=rec.id,
            signal=signal,  # type: ignore[arg-type]
            features=features,
            prompt_version=str(rec.provenance.get("prompt_version") or purpose)[:32],
            model=str(rec.provenance.get("model") or "unknown")[:64],
            credential_mode=(
                "user_key" if rec.provenance.get("credential_mode") == "user_key" else "platform"
            ),
            created_at=now,
        )
