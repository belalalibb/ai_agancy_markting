"""Fixed prompts for ``POST /conversations/{id}/suggest-reply`` (SPRINT-09 B-3, D-055).

The system prompt is **not** user-editable. It pins the four rules the directive mandates:
answer in the customer's language, stay under :data:`REPLY_MAX_CHARS`, never promise prices
or legal/medical outcomes, and ask exactly one clarifying question when the request is
ambiguous. The AI output is a *suggestion* — it never reaches the network until a human
approves it (§2-0 أ).
"""

from __future__ import annotations

import hashlib
from typing import Final, Literal

Tone = Literal["friendly", "formal", "promotional"]
Language = Literal["ar", "en", "auto"]

# D-069: bump whenever the templates change (public provenance field, compared across versions).
REPLY_PROMPT_VERSION: Final = "wa-reply-v1"
REPLY_MAX_CHARS: Final = 600
DEFAULT_MAX_TURNS: Final = 6

_TONE_AR: Final[dict[str, str]] = {
    "friendly": "ودودة",
    "formal": "رسمية",
    "promotional": "ترويجية",
}
_LANGUAGE_RULE: Final[dict[str, str]] = {
    "ar": "أجب بالعربية دائمًا. / Always answer in Arabic.",
    "en": "أجب بالإنجليزية دائمًا. / Always answer in English.",
    "auto": "أجب بلغة آخر رسالة من العميل نفسها. / Answer in the language of the customer's "
    "latest message.",
}

SYSTEM_PROMPT_TEMPLATE: Final = (
    "أنت مساعد خدمة عملاء لعلامة «{brand}» على واتساب، تكتب ردًا واحدًا مقترحًا بنبرة "
    "{tone_ar} ({tone}) يراجعه موظف بشري قبل الإرسال.\n"
    "قواعد صارمة:\n"
    "1) {language_rule}\n"
    "2) الحد الأقصى {limit} حرفًا.\n"
    "3) ممنوع أي وعود سعرية أو خصومات أو التزامات قانونية أو طبية أو مواعيد تسليم غير "
    "مذكورة حرفيًا في المحادثة (قاعدة D-055). إن لم تعرف الإجابة فقل إن الفريق سيعود للعميل.\n"
    "4) إن كان طلب العميل غامضًا فاطرح سؤالًا توضيحيًا واحدًا فقط.\n"
    "5) أخرج نص الردّ فقط بدون مقدمات أو شروح أو علامات اقتباس.\n"
    'You are a WhatsApp customer-care assistant for the brand "{brand}". Draft exactly one '
    "{tone} reply that a human agent will review before sending. {language_rule} Hard limit "
    "{limit} characters. Never promise prices, discounts, legal or medical outcomes or delivery "
    "dates that are not literally present in the conversation (D-055). If the request is "
    "ambiguous ask one clarifying question. Output the reply text only."
)

_DIRECTION_LABEL: Final[dict[str, str]] = {"inbound": "العميل / Customer", "outbound": "نحن / Us"}


def reply_system_prompt(brand_display_name: str, language: Language, tone: Tone) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        brand=brand_display_name.strip() or "العلامة",
        tone=tone,
        tone_ar=_TONE_AR[tone],
        language_rule=_LANGUAGE_RULE[language],
        limit=REPLY_MAX_CHARS,
    )


def reply_user_prompt(
    last_messages: list[tuple[str, str | None]], max_turns: int = DEFAULT_MAX_TURNS
) -> str:
    """Oldest-first transcript of the last ``max_turns`` turns (``(direction, body)`` pairs).

    Non-text turns (``body is None``) are rendered as a bracketed placeholder so the model
    knows a media message happened without us inventing its content.
    """
    turns = last_messages[-max_turns:] if max_turns > 0 else []
    lines = [
        f"{_DIRECTION_LABEL.get(direction, direction)}: "
        f"{(body or '').strip() or '[رسالة غير نصية / non-text message]'}"
        for direction, body in turns
    ]
    return "المحادثة الأخيرة / Recent conversation:\n" + "\n".join(lines)


def prompt_hash(system: str, user: str) -> str:
    """``sha256(system + user)[:16]`` — stored in the draft provenance (private key)."""
    return hashlib.sha256((system + user).encode("utf-8")).hexdigest()[:16]
