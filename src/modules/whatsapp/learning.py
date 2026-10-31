"""Learning-signal persistence shared by the webhook, the inbox and the suggestion service
(SPRINT-09 B-3, D-069).

Kept apart from :mod:`suggestions` so :mod:`inbox` / :mod:`webhook` can record ``expired``
signals without importing the AI service (no import cycle). Signals carry **features only**
— :mod:`src.modules.whatsapp.signals` — never the draft text.
"""

from __future__ import annotations

from datetime import datetime

from src.core.metrics import counters
from src.modules.whatsapp import signals
from src.modules.whatsapp.domain.models import LearningSignal, ReplyDraft, Signal
from src.modules.whatsapp.domain.repository import WhatsAppRepository
from src.modules.whatsapp.prompts import REPLY_PROMPT_VERSION


def build_signal(
    draft: ReplyDraft,
    signal: Signal,
    *,
    now: datetime,
    edit_distance_ratio: float | None = None,
) -> LearningSignal:
    """Features only (D-069) — the draft text never enters the signal row."""
    prov = draft.provenance
    return LearningSignal(
        brand_id=draft.brand_id,
        reply_draft_id=draft.id,
        signal=signal,
        edit_distance_ratio=edit_distance_ratio,
        rejection_reason=draft.rejection_reason,
        prompt_version=str(prov.get("prompt_version") or REPLY_PROMPT_VERSION)[:16],
        model=str(prov.get("model") or "unknown")[:64],
        credential_mode=str(prov.get("credential_mode") or "platform")[:16],
        features=signals.features(draft.suggested_body, draft.final_body),
        created_at=now,
    )


async def record_expired(
    repo: WhatsAppRepository, expired: list[ReplyDraft], *, now: datetime
) -> int:
    """One ``expired`` signal per draft that just left ``suggested`` — called by the webhook
    (newer inbound arrived) and by the inbox (a human replied manually). D-069: a
    non-decision is a signal too. Returns the number of signals written."""
    for draft in expired:
        await repo.add_signal(build_signal(draft, Signal.EXPIRED, now=now))
    if expired:
        counters.increment("wa_draft_expired", len(expired))
    return len(expired)
