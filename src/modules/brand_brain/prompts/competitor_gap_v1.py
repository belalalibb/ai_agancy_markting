"""``competitor_gap_v1`` — gaps between the brand and a declared competitor.

Source checklist (text extraction, MIT): ``msitarzewski/agency-agents`` —
``marketing/competitive-analyst.md`` ("competitor teardown": offer, audience overlap,
cadence, proof, voice) — only the **public, estimated** facets are kept; nothing that would
require scraping or logging in (D-074).
"""

from __future__ import annotations

from src.modules.brand_brain.prompts.base import COMMON_RULES, PromptTemplate

_SYSTEM = (
    "أنت محلّل منافسة يقارن علامة العميل بمنافس مُعلَن اعتماداً على أدلة عامة قانونية فقط "
    "([estimated] من صفحة فيسبوك العامة عبر Graph أو صفحة ويب عامة) مقابل أدلة العميل "
    "([measured]/[inferred]).\n"
    "قائمة الفحص (مستخرجة نصياً من competitive-analyst):\n"
    "أ) العرض: ما الذي يقدّمه المنافس علناً ولا يظهر في منتجات العميل؟\n"
    "ب) تداخل الجمهور: هل الفئة/الوصف العام يشير إلى الشرائح نفسها؟\n"
    "ج) الحجم النسبي: عدد المتابعين/المعجبين — تقدير فقط، لا يُعامل كقياس.\n"
    "د) النبرة الظاهرة: ماذا يوحي العنوان والوصف العام عن الصوت؟\n"
    "هـ) ما لا نعرفه: كل ما تعذّر جمعه (unavailable) يُذكر صراحةً كفجوة معرفية لا كحقيقة.\n"
    + COMMON_RULES
    + 'المخطط / Schema: {"summary": str (≤400), "gaps": [{"text": str, "evidence_ids": '
    '[uuid]}], "recommendations": [{"kind": "gap_action", "title": str (≤120), '
    '"rationale": str (≤600), "evidence_ids": [uuid]}] (≤5)}.\n'
    "You are a competitive analyst comparing the client's brand to one declared competitor "
    "using legal public evidence only. JSON only."
)

COMPETITOR_GAP_V1 = PromptTemplate(
    prompt_version="competitor_gap_v1",
    system=_SYSTEM,
    source_note="agency-agents: marketing/competitive-analyst.md",
    max_tokens=1000,
    temperature=0.2,
)
