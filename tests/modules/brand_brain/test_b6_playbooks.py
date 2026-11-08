"""SPRINT-14-B B-6 — Arabic active playbooks, English reference copies, ``lang`` field.

No network and no AI: the knowledge is static JSON, validated at import (a bad file stops
the process). Machine ids (sector, tone, metric keys, JSON keys) are never translated."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from src.modules.brand_brain import playbooks as pb

ARABIC = re.compile(r"[\u0600-\u06FF]")
ACTIVE = pb.SECTORS
EN_REFERENCED = ("restaurants", "clinics")  # the English originals kept before translation


def _read(directory: Path, name: str) -> dict:
    return json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))


def _human_texts(book: dict) -> list[str]:
    return [
        book["title"],
        *book["content_pillars"],
        *book["reply_templates"],
        *(c["label"] for c in book["calendar"]),
        *book["tips"],
    ]


def test_every_active_book_is_arabic_and_loads() -> None:
    books = pb.load_playbooks()
    assert tuple(books) == ACTIVE
    for sector, book in books.items():
        assert book.lang == "ar" and book.sector == sector
        raw = _read(pb.PLAYBOOKS_DIR, sector)
        assert raw["lang"] == "ar"
        assert all(ARABIC.search(text) for text in _human_texts(raw)), sector


@pytest.mark.parametrize("sector", EN_REFERENCED)
def test_english_reference_copy_exists_and_is_never_loaded(sector: str) -> None:
    en = _read(pb.PLAYBOOKS_DIR, f"{sector}_en")
    assert en["lang"] == "en" and en["sector"] == sector
    assert not any(ARABIC.search(text) for text in _human_texts(en))
    assert sector in pb.PLAYBOOKS and pb.PLAYBOOKS[sector].lang == "ar"
    assert f"{sector}_en" not in pb.PLAYBOOKS  # the loader follows SECTORS only, no glob


@pytest.mark.parametrize("sector", EN_REFERENCED)
def test_machine_ids_and_limits_match_the_english_reference(sector: str) -> None:
    ar, en = _read(pb.PLAYBOOKS_DIR, sector), _read(pb.PLAYBOOKS_DIR, f"{sector}_en")
    assert set(ar) == set(en)  # same JSON keys
    for key in ("sector", "tone", "metrics", "pulse_thresholds"):
        assert ar[key] == en[key], key  # ids / enums / numbers are not translated
    for key in ("content_pillars", "reply_templates", "tips"):
        assert len(ar[key]) == len(en[key]), key
    assert [c["month"] for c in ar["calendar"]] == [c["month"] for c in en["calendar"]]


def test_no_invented_performance_numbers_or_promises() -> None:
    for sector in ACTIVE:
        text = " ".join(_human_texts(_read(pb.PLAYBOOKS_DIR, sector)))
        assert "%" not in text and "ضمان" not in text.replace("ضماناً أبداً", "")
        assert "مضمون" not in text


def test_startup_fails_on_corruption_and_on_a_non_arabic_active_book(tmp_path: Path) -> None:
    for sector in ACTIVE:
        (tmp_path / f"{sector}.json").write_text(
            (pb.PLAYBOOKS_DIR / f"{sector}.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
    assert set(pb.load_playbooks(tmp_path)) == set(ACTIVE)
    english = _read(pb.PLAYBOOKS_DIR, "restaurants_en")
    (tmp_path / "restaurants.json").write_text(json.dumps(english), encoding="utf-8")
    with pytest.raises(pb.PlaybookLoadError, match="lang"):
        pb.load_playbooks(tmp_path)
    (tmp_path / "restaurants.json").write_text('{"sector": "restaurants", "tips": []}', "utf-8")
    with pytest.raises(pb.PlaybookLoadError, match="malformed"):
        pb.load_playbooks(tmp_path)


def test_unknown_lang_and_extra_keys_are_rejected() -> None:
    raw = _read(pb.PLAYBOOKS_DIR, "restaurants")
    with pytest.raises(ValueError):  # noqa: PT011 - pydantic ValidationError is a ValueError
        pb.Playbook.model_validate({**raw, "lang": "fr"})
    with pytest.raises(ValueError):  # noqa: PT011
        pb.Playbook.model_validate({**raw, "unexpected": 1})
