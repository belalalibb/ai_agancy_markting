"""Shared building blocks of the Brand Brain prompt templates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from src.modules.brand_brain.domain.models import BrandProfile, Evidence

GRADE_LABEL = {"measured": "[measured]", "inferred": "[inferred]", "estimated": "[estimated]"}
MAX_EVIDENCE_LINES = 60


@dataclass(frozen=True)
class PromptTemplate:
    """``system`` is fixed (never user-editable); ``user`` is rendered per call from the
    brand profile + graded evidence. ``prompt_hash`` pins the system text (D-069)."""

    prompt_version: str
    system: str
    source_note: str = field(repr=False)  # which agency-agents role was extracted
    max_tokens: int = 1200
    temperature: float = 0.3

    @property
    def prompt_hash(self) -> str:
        return hashlib.sha256(self.system.encode("utf-8")).hexdigest()[:16]


def profile_block(profile: BrandProfile) -> str:
    """Compact JSON of the profile for the user message (banned phrases included — the model
    must respect them; they never reach a VIEWER through this path)."""
    payload: dict[str, Any] = {
        "products": [p.model_dump(exclude_none=True) for p in profile.products][:20],
        "audience": profile.audience.model_dump(),
        "tone": profile.tone.model_dump(exclude={"reply_templates"}),
        "goals": [g.model_dump() for g in profile.goals],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def evidence_lines(rows: list[Evidence]) -> str:
    """One line per evidence row: ``- <id> [grade] subject metric = value (observed)``.
    Confidence is shown; ``source_ref`` is **not** (it may name a host — irrelevant to the
    model and one less thing to leak)."""
    lines = []
    for ev in rows[:MAX_EVIDENCE_LINES]:
        value = json.dumps(ev.value, ensure_ascii=False, separators=(",", ":"))
        lines.append(
            f"- {ev.id} {GRADE_LABEL[ev.evidence_kind]} {ev.subject_kind}:{ev.subject_id} "
            f"{ev.metric}={value} confidence={ev.confidence} observed={ev.observed_at.date()}"
        )
    return "\n".join(lines) if lines else "- (no evidence collected yet)"


COMMON_RULES = (
    "قواعد صارمة / Hard rules:\n"
    "1) استند فقط إلى الأدلة المدرجة؛ كل جملة تُسند إلى معرّف دليل واحد على الأقل. "
    "Use only the listed evidence; cite at least one evidence id per claim.\n"
    "2) لا تخترع أرقاماً ولا تحوّل تقديراً إلى قياس. Never invent numbers; never upgrade an "
    "[estimated] or [inferred] fact to a measurement.\n"
    "3) الناتج JSON فقط بالمخطط المطلوب، بلا نص خارجه. Output JSON only, matching the schema.\n"
    "4) كل ما تقترحه مسودة يراجعها إنسان قبل أي نشر (D-055). Everything you propose is a draft "
    "a human reviews before anything is published.\n"
    "5) احترم العبارات المحظورة في النبرة. Respect banned_phrases.\n"
)
