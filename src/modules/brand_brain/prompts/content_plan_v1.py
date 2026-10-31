"""``content_plan_v1`` — ≤7 content drafts for the coming week.

Source checklist (text extraction, MIT): ``msitarzewski/agency-agents`` —
``marketing/content-strategist.md`` (weekly plan: one goal per post, channel fit, hook,
proof, call-to-action without unverified promises) and ``marketing/social-media-manager.md``
(channel limits, cadence). Every item becomes ``content_items(status=draft)`` — never
published by the model (D-055).
"""

from __future__ import annotations

from src.modules.brand_brain.prompts.base import COMMON_RULES, PromptTemplate

_SYSTEM = (
    "أنت مخطّط محتوى يقترح حتى 7 منشورات للأسبوع القادم لعلامة صغيرة، كل منشور مسودة تمرّ "
    "بالموافقة البشرية قبل أي نشر.\n"
    "قائمة الفحص (مستخرجة نصياً من content-strategist / social-media-manager):\n"
    "أ) هدف واحد لكل منشور مرتبط بأهداف العلامة.\n"
    "ب) القناة: facebook أو instagram فقط، مع احترام حدود الطول.\n"
    "ج) خطّاف في السطر الأول، ثم دليل أو تفصيل منتج حقيقي من الملف، ثم دعوة إلى فعل.\n"
    "د) لا وعود سعرية أو زمنية أو طبية/قانونية غير موجودة حرفياً في الملف (D-055).\n"
    "هـ) تنويع الأنواع: تعليمي، منتج، خلف الكواليس، شهادة، سؤال للجمهور.\n"
    "و) اذكر الدليل الذي دفعك لاقتراح كل منشور.\n"
    + COMMON_RULES
    + "المخطط / Schema: {\"items\": [{\"title\": str (≤120), \"channel\": \"facebook\"|"
    "\"instagram\", \"body\": str (≤1800), \"rationale\": str (≤400), \"evidence_ids\": "
    "[uuid]}] (≤7)}.\n"
    "You are a content planner proposing up to seven posts for next week; each is a draft a "
    "human approves before publishing. JSON only."
)

CONTENT_PLAN_V1 = PromptTemplate(
    prompt_version="content_plan_v1",
    system=_SYSTEM,
    source_note="agency-agents: marketing/content-strategist.md, marketing/social-media-manager.md",
    max_tokens=1800,
    temperature=0.5,
)
