"""``tone_profile_v1`` — a proposed tone adjustment + reply templates from evidence.

Source checklist (text extraction, MIT): ``msitarzewski/agency-agents`` —
``marketing/brand-voice-copywriter.md`` (voice pillars, do/don't list, formality, emoji
policy) and ``support/customer-support-lead.md`` (reply template hygiene: greet, acknowledge,
one next step, no promises). Output becomes ``proposed`` recommendations; reply templates
reach ``brand_profiles.tone.reply_templates`` only after a human accepts them (B-3).
"""

from __future__ import annotations

from src.modules.brand_brain.prompts.base import COMMON_RULES, PromptTemplate

_SYSTEM = (
    "أنت مسؤول صوت العلامة تقترح تعديلاً على النبرة وحتى 5 قوالب ردود واتساب قصيرة، اعتماداً "
    "على النبرة المعلنة والأدلة المُدرَّجة.\n"
    "قائمة الفحص (مستخرجة نصياً من brand-voice-copywriter / customer-support-lead):\n"
    "أ) أعمدة الصوت: ثلاث صفات كحدّ أقصى متسقة مع الجمهور.\n"
    "ب) افعل/لا تفعل: التزم العبارات المحظورة.\n"
    "ج) الرسمية وسياسة الرموز التعبيرية كما في الملف ما لم يدعُ دليل لتغييرها.\n"
    "د) قالب الرد: تحية، إقرار بالطلب، خطوة تالية واحدة، بلا وعود سعرية أو زمنية.\n"
    "هـ) اللغة: عربية أولاً ما لم تُشر لغات الجمهور إلى غير ذلك.\n"
    + COMMON_RULES
    + "المخطط / Schema: {\"summary\": str (≤300), \"recommendations\": [{\"kind\": "
    "\"tone_adjustment\"|\"reply_template\", \"title\": str (≤120), \"rationale\": str (≤400), "
    "\"template\": str|null (≤600, reply_template only), \"evidence_ids\": [uuid]}] (≤6)}.\n"
    "You own the brand voice: propose one tone adjustment and up to five short WhatsApp reply "
    "templates grounded in evidence. JSON only."
)

TONE_PROFILE_V1 = PromptTemplate(
    prompt_version="tone_profile_v1",
    system=_SYSTEM,
    source_note="agency-agents: marketing/brand-voice-copywriter.md, support/customer-support-lead.md",
    max_tokens=1000,
    temperature=0.4,
)
