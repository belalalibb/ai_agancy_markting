"""Process-wide logging hygiene (SPRINT-04.1 H2 — defence in depth).

1. ``httpx`` / ``httpcore`` are capped at ``WARNING``: their INFO line ``HTTP Request: GET
   <full url>`` would otherwise print whatever the URL carries.
2. A :class:`SecretRedactingFilter` is attached to the **root** logger so any record — from
   any library — has token-like material replaced by ``***`` before it reaches a handler.

The first line of defence is not putting secrets in URLs at all (``meta_client.py``); this
module only guarantees that a regression there cannot reach the logs verbatim.
"""

from __future__ import annotations

import logging
import re

NOISY_HTTP_LOGGERS: tuple[str, ...] = ("httpx", "httpcore")

# Order matters: the query-param pattern runs first so ``access_token=EAA…`` collapses to a
# single ``access_token=***`` instead of ``access_token=EAA***``.
_QUERY_SECRET = re.compile(
    r"(access_token|client_secret|input_token|fb_exchange_token|code)=[^&\s\"']+",
    re.IGNORECASE,
)
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=|-]+")
_META_TOKEN = re.compile(r"EAA[A-Za-z0-9]{20,}")

REDACTED = "***"


def redact(text: str) -> str:
    """Return ``text`` with every known secret shape replaced by ``***``."""
    text = _QUERY_SECRET.sub(rf"\1={REDACTED}", text)
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    return _META_TOKEN.sub(f"EAA{REDACTED}", text)


class SecretRedactingFilter(logging.Filter):
    """Rewrites ``record.msg``/``record.args`` in place so every handler sees redacted text.

    Filters on the root logger only see records emitted *directly* to it, therefore
    :func:`configure_logging` also attaches this filter to every root handler — handlers do
    run for propagated records from child loggers (``httpx``, ``src.…``).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed %-args; leave the record alone
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


_FILTER = SecretRedactingFilter(name="secret-redaction")


def configure_logging() -> None:
    """Idempotent: quiet the HTTP client loggers and install the redaction filter."""
    for name in NOISY_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    root = logging.getLogger()
    if _FILTER not in root.filters:
        root.addFilter(_FILTER)
    for handler in root.handlers:
        if _FILTER not in handler.filters:
            handler.addFilter(_FILTER)
    # Also guard the HTTP loggers themselves: a deployment that re-enables INFO on
    # ``httpx`` still gets redacted request lines.
    for name in NOISY_HTTP_LOGGERS:
        http_logger = logging.getLogger(name)
        if _FILTER not in http_logger.filters:
            http_logger.addFilter(_FILTER)
