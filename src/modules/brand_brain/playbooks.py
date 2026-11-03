"""Sector playbooks (SPRINT-12.1-B D-4, POST_M7 §1-ج): seed JSON per sector — content
pillars, Pulse thresholds, first-reply templates, metrics, tone, calendar, daily tips.

Loaded **once at import/startup** and validated with Pydantic: a malformed file fails the
process with a clear message rather than serving a half-catalog. Read-only; nothing is
written to ``brand_profiles`` (applying a playbook as a profile seed ⇒ SPRINT-13).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from src.modules.auth.dependencies import CurrentUser

PLAYBOOKS_DIR = Path(__file__).resolve().parent / "playbooks"
SECTORS: tuple[str, ...] = ("restaurants", "clinics")
TIPS_PER_DAY = 3


class CalendarEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: int = Field(ge=1, le=12)
    label: str = Field(min_length=1, max_length=120)


class PulseThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wa_first_response_minutes: int = Field(ge=1, le=1440)
    posts_per_week_min: int = Field(ge=0, le=70)
    wa_window_closing_hours: int = Field(ge=1, le=24)


class Playbook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sector: str = Field(pattern=r"^[a-z_]+$", max_length=32)
    title: str = Field(min_length=1, max_length=80)
    tone: Literal["professional", "friendly", "promotional"]
    content_pillars: list[str] = Field(min_length=1, max_length=8)
    pulse_thresholds: PulseThresholds
    reply_templates: list[str] = Field(min_length=1, max_length=8)
    metrics: list[str] = Field(min_length=1, max_length=12)
    calendar: list[CalendarEntry] = Field(max_length=24)
    tips: list[str] = Field(min_length=TIPS_PER_DAY, max_length=31)

    def tips_for(self, day: datetime | None = None) -> list[str]:
        """Three tips rotating by day-of-year — the same three all day, new ones tomorrow."""
        index = (day or datetime.now(UTC)).timetuple().tm_yday
        n = len(self.tips)
        return [self.tips[(index + i) % n] for i in range(TIPS_PER_DAY)]


class PlaybookLoadError(RuntimeError):
    """Raised at startup when a seed file is missing or malformed (the app must not boot)."""


def load_playbooks(directory: Path = PLAYBOOKS_DIR) -> dict[str, Playbook]:
    books: dict[str, Playbook] = {}
    for sector in SECTORS:
        path = directory / f"{sector}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            book = Playbook.model_validate(raw)
        except (OSError, ValueError) as exc:  # ValidationError is a ValueError
            msg = f"playbook {path.name!r} is missing or malformed: {exc}"
            raise PlaybookLoadError(msg) from exc
        if book.sector != sector:
            msg = f"playbook {path.name!r} declares sector {book.sector!r}"
            raise PlaybookLoadError(msg)
        books[sector] = book
    return books


PLAYBOOKS: dict[str, Playbook] = load_playbooks()

router = APIRouter(tags=["Brand Brain (M7)"])


class PlaybookPublic(Playbook):
    today_tips: list[str]


@router.get(
    "/playbooks",
    response_model=list[PlaybookPublic],
    summary="Sector playbooks (seed knowledge) — any authenticated user",
)
async def list_playbooks(_: CurrentUser) -> list[PlaybookPublic]:
    return [
        PlaybookPublic(**book.model_dump(), today_tips=book.tips_for())
        for book in PLAYBOOKS.values()
    ]
