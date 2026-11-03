"""Consumer-side feature-flag read (SPRINT-13 A-2): DB value first, ``Settings`` fallback.

``app.state.flag_source`` (installed by ``src/main.py`` from the ``ops`` module, or an
in-memory fake in tests) exposes ``async flags() -> dict[str, bool]``. Without a source the
``Settings`` field decides — the pre-SPRINT-13 behaviour, literally. Duck-typed on purpose:
``pulse`` and ``whatsapp`` never import ``ops`` (D-076 isolation).
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.config import Settings

logger = logging.getLogger(__name__)


async def effective_flag(app: Any, settings: Settings, key: str) -> bool:
    source = getattr(getattr(app, "state", None), "flag_source", None)
    if source is not None:
        try:
            stored = await source.flags()
        except Exception:  # noqa: BLE001 - a flag read must never break a request
            logger.exception("flags: source failed — falling back to settings for %s", key)
        else:
            if key in stored:
                return bool(stored[key])
    return bool(getattr(settings, key, False))


__all__ = ["effective_flag"]
