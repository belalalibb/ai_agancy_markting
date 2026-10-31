"""Learning-signal features for reply drafts (SPRINT-09 B-3, D-069).

Every function here is **pure** and returns numbers / short labels only. The resulting
``features`` dict is what lands in ``wa_learning_signals.features`` — never the text itself
(the B3-U4 guard serialises a signal row and checks no 12-char run of the draft survives).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Final, Literal

Language = Literal["ar", "en", "mixed", "other"]

_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
_LATIN = re.compile(r"[A-Za-z]")
_QUESTION_MARKS: Final = ("?", "؟")

# Fixed call-to-action verb list (Arabic + English). Matched on whole words, case-insensitive.
CTA_VERBS: Final[frozenset[str]] = frozenset(
    {
        # Arabic
        "اطلب",
        "اطلبي",
        "احجز",
        "احجزي",
        "تواصل",
        "تواصلي",
        "اتصل",
        "اتصلي",
        "زر",
        "زوري",
        "سجل",
        "سجلي",
        "اشترك",
        "اشتركي",
        "اضغط",
        "اضغطي",
        "راسلنا",
        "أرسل",
        "أرسلي",
        # English
        "order",
        "book",
        "call",
        "contact",
        "visit",
        "register",
        "subscribe",
        "click",
        "tap",
        "reply",
        "send",
        "shop",
        "buy",
    }
)
_WORD = re.compile(r"[\w\u0600-\u06FF]+", re.UNICODE)

# Language thresholds on the Arabic share of *letters* (digits/punctuation ignored).
_ARABIC_DOMINANT: Final = 0.8
_ARABIC_MINOR: Final = 0.2


def suggested_len(suggested_body: str) -> int:
    return len(suggested_body)


def final_len(final_body: str | None, suggested_body: str) -> int:
    return len(final_body if final_body is not None else suggested_body)


def language(text: str) -> Language:
    """``ar`` / ``en`` / ``mixed`` by the Arabic share of letters; ``other`` when no letters."""
    arabic = len(_ARABIC.findall(text))
    latin = len(_LATIN.findall(text))
    letters = arabic + latin
    if letters == 0:
        return "other"
    ratio = arabic / letters
    if ratio >= _ARABIC_DOMINANT:
        return "ar"
    if ratio <= _ARABIC_MINOR:
        return "en"
    return "mixed"


def has_question(text: str) -> bool:
    return any(mark in text for mark in _QUESTION_MARKS)


def has_cta(text: str) -> bool:
    return any(word.lower() in CTA_VERBS for word in _WORD.findall(text))


def edit_distance_ratio(suggested_body: str, final_body: str) -> float:
    """``1 - SequenceMatcher(None, a, b).ratio()`` rounded to 3 decimals (0 = identical)."""
    return round(1.0 - SequenceMatcher(None, suggested_body, final_body).ratio(), 3)


def features(suggested_body: str, final_body: str | None) -> dict[str, int | bool | str]:
    """The full D-069 feature vector for one draft decision (numbers and labels only)."""
    effective = final_body if final_body is not None else suggested_body
    return {
        "suggested_len": suggested_len(suggested_body),
        "final_len": final_len(final_body, suggested_body),
        "language": language(effective),
        "has_question": has_question(effective),
        "has_cta": has_cta(effective),
    }
