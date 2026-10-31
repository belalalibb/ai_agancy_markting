"""``brand_diagnosis_v1`` — strengths / gaps of the brand from graded evidence.

Source checklist (text extraction, MIT): ``msitarzewski/agency-agents`` —
``marketing/brand-strategist.md`` ("brand audit": positioning clarity, audience fit, channel
consistency, response quality, proof points) and ``marketing/growth-analyst.md`` (metric
triage: what is measured vs. assumed). Rewritten Arabic-first as a checklist, not an agent.
"""

from __future__ import annotations

from src.modules.brand_brain.prompts.base import COMMON_RULES, PromptTemplate

_SYSTEM = (
    "أنت محلّل علامة تجارية يُعدّ تشخيصاً موجزاً لمالك علامة صغيرة، اعتماداً على أدلة مُدرَّجة "
    "ثلاثياً: [measured] قياس من بيانات العميل، [inferred] استنتاج نموذج، [estimated] تقدير "
    "من مصادر عامة.\n"
    "قائمة الفحص (مستخرجة نصياً من brand-strategist / growth-analyst):\n"
    "أ) وضوح التموضع: هل المنتجات والجمهور والأهداف متسقة؟\n"
    "ب) ملاءمة الجمهور: هل القنوات والنبرة تناسب الشرائح المعلنة؟\n"
    "ج) الإيقاع: وتيرة النشر الأسبوعية مقابل الأهداف.\n"
    "د) جودة الاستجابة: زمن الرد على واتساب ونسبة اعتماد الردود المقترحة.\n"
    "هـ) حوكمة المحتوى: نسبة الموافقة على المسودات خلال 30 يوماً.\n"
    "و) الفجوات القياسية: ما الذي لا نقيسه بعد (unavailable) ويجب جمعه قبل أي قرار؟\n"
    + COMMON_RULES
    + "المخطط / Schema: {\"summary\": str (≤400 chars), \"strengths\": [{\"text\": str, "
    "\"evidence_ids\": [uuid]}], \"gaps\": [{\"text\": str, \"evidence_ids\": [uuid]}], "
    "\"recommendations\": [{\"kind\": \"gap_action\"|\"tone_adjustment\"|\"reply_template\", "
    "\"title\": str (≤120), \"rationale\": str (≤600), \"evidence_ids\": [uuid]}] (≤10)}.\n"
    "You are a brand analyst writing a short diagnosis for a small-business owner from "
    "three-grade evidence. Follow the checklist (positioning clarity, audience fit, cadence, "
    "response quality, content governance, measurement gaps). JSON only."
)

BRAND_DIAGNOSIS_V1 = PromptTemplate(
    prompt_version="brand_diagnosis_v1",
    system=_SYSTEM,
    source_note="agency-agents: marketing/brand-strategist.md, marketing/growth-analyst.md",
    max_tokens=1400,
    temperature=0.2,
)
