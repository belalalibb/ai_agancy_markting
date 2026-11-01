"""Versioned prompt templates for Brand Brain (SPRINT-10 B-2, D-069 / D-074).

Every template is a :class:`PromptTemplate` that declares ``prompt_version`` and a
``prompt_hash`` (sha256 of the *system* text) — both are stored in ``ai_cost_log`` /
``learning_signals`` / recommendation provenance, never the text itself.

The checklists are **text extractions** (Hub `REPOSITORY_EVALUATIONS.md` §5-أ, verdict
EXTRACT) of the role files in ``msitarzewski/agency-agents`` (MIT): no file is imported, no
agent acts on its own. Source role per template is named in each module's comment.

Hard rules shared by all four (directive §2-0 د/هـ):
* the model only sees graded evidence (``[measured]`` / ``[inferred]`` / ``[estimated]``)
  and must cite evidence ids — the server re-tags every sentence anyway (B-3);
* the output is JSON only (``json_mode=True`` ⇒ ``response_format=json_object``);
* nothing the model produces is published: every item becomes a *draft* or a *proposed*
  recommendation under human approval (D-055).
"""

from __future__ import annotations

from src.modules.brand_brain.prompts.base import PromptTemplate, evidence_lines, profile_block
from src.modules.brand_brain.prompts.brand_diagnosis_v1 import BRAND_DIAGNOSIS_V1
from src.modules.brand_brain.prompts.competitor_gap_v1 import COMPETITOR_GAP_V1
from src.modules.brand_brain.prompts.content_plan_v1 import CONTENT_PLAN_V1
from src.modules.brand_brain.prompts.tone_profile_v1 import TONE_PROFILE_V1

TEMPLATES: dict[str, PromptTemplate] = {
    t.prompt_version: t
    for t in (BRAND_DIAGNOSIS_V1, COMPETITOR_GAP_V1, CONTENT_PLAN_V1, TONE_PROFILE_V1)
}

__all__ = [
    "BRAND_DIAGNOSIS_V1",
    "COMPETITOR_GAP_V1",
    "CONTENT_PLAN_V1",
    "TEMPLATES",
    "TONE_PROFILE_V1",
    "PromptTemplate",
    "evidence_lines",
    "profile_block",
]
