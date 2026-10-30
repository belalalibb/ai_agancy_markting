"""Fixed prompts for ``POST /items/{id}/generate`` (SPRINT-06 B5, D-055).

The system prompt is **not** user-editable: it pins the channel, the hard length limits
(Instagram 2200 / Facebook 5000 characters) and the D-055 rule that the model must not invent
numeric claims (prices, percentages, dates, counts) that are absent from the brief.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from src.modules.content.domain.models import Channel

Tone = Literal["professional", "friendly", "promotional"]
Language = Literal["ar", "en"]

CHANNEL_LIMITS: dict[Channel, int] = {Channel.INSTAGRAM: 2200, Channel.FACEBOOK_PAGE: 5000}
_CHANNEL_LABEL = {Channel.INSTAGRAM: "Instagram", Channel.FACEBOOK_PAGE: "Facebook Page"}
_TONE_AR = {"professional": "مهنية", "friendly": "ودودة", "promotional": "ترويجية"}

SYSTEM_PROMPT_TEMPLATE = (
    "أنت كاتب محتوى تسويقي لمنصة {channel}. اكتب منشورًا واحدًا فقط بلغة {language} "
    "وبنبرة {tone_ar} ({tone}).\n"
    "قواعد صارمة:\n"
    "1) الحد الأقصى {limit} حرفًا لقناة {channel} (إنستغرام 2200 / فيسبوك 5000).\n"
    "2) ممنوع اختراع أي أرقام أو نسب أو أسعار أو تواريخ أو إحصاءات غير مذكورة حرفيًا في "
    "الموجز (قاعدة D-055). إن لم يذكر الموجز رقمًا فلا تكتب رقمًا.\n"
    "3) لا وعود طبية أو مالية ولا ادعاءات لا يمكن التحقق منها.\n"
    "4) أخرج نص المنشور فقط بدون مقدمات أو شروح أو علامات اقتباس.\n"
    "You are a marketing copywriter for {channel}. Write exactly one post in {language_en} "
    "with a {tone} tone. Hard limit {limit} characters. Never invent numbers, percentages, "
    "prices, dates or statistics that are not literally present in the brief (D-055). "
    "Output the post text only."
)

USER_PROMPT_TEMPLATE = "الموجز / Brief:\n{brief}"


def system_prompt(channel: Channel, *, tone: Tone, language: Language) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        channel=_CHANNEL_LABEL[channel],
        language="العربية" if language == "ar" else "الإنجليزية",
        language_en="Arabic" if language == "ar" else "English",
        tone=tone,
        tone_ar=_TONE_AR[tone],
        limit=CHANNEL_LIMITS[channel],
    )


def user_prompt(brief: str) -> str:
    return USER_PROMPT_TEMPLATE.format(brief=brief.strip())


def prompt_hash(system: str, user: str) -> str:
    """``sha256(system + user)[:16]`` — stored in the revision provenance."""
    return hashlib.sha256((system + user).encode()).hexdigest()[:16]
