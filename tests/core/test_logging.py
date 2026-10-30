"""SPRINT-04.1 H2 — process-wide log redaction (``src/core/logging.py``)."""

from __future__ import annotations

import logging

import pytest
from src.core.logging import (
    NOISY_HTTP_LOGGERS,
    SecretRedactingFilter,
    configure_logging,
    redact,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "GET https://g/x?client_secret=SUPERSECRET&fields=id",
            "GET https://g/x?client_secret=***&fields=id",
        ),
        (
            "…/me/accounts?access_token=USER_TOKEN_LEAK&limit=1",
            "…/me/accounts?access_token=***&limit=1",
        ),
        ("input_token=abc.def fb_exchange_token=xyz", "input_token=*** fb_exchange_token=***"),
        ("cb?code=AQB123&state=s", "cb?code=***&state=s"),
        ("Authorization: Bearer eyJhbGciOi.JIUzI1NiJ9.sig", "Authorization: Bearer ***"),
        ("Bearer 123|app-secret", "Bearer ***"),
        ("token EAAabcdefghijklmnopqrstuvwxyz0123456789 ok", "token EAA*** ok"),
        ("nothing to see", "nothing to see"),
        ("EAAshort", "EAAshort"),  # below the 20-char threshold ⇒ not a token
    ],
)
def test_h2_u4_redact_patterns(raw: str, expected: str) -> None:
    assert redact(raw) == expected


def test_h2_u4_filter_rewrites_record_message_and_args() -> None:
    flt = SecretRedactingFilter()
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        "HTTP Request: GET %s",
        ("https://g/?access_token=X",),
        None,
    )
    assert flt.filter(record) is True
    assert record.getMessage() == "HTTP Request: GET https://g/?access_token=***"
    assert record.args == ()

    clean = logging.LogRecord("x", logging.INFO, __file__, 1, "plain %d", (1,), None)
    assert flt.filter(clean) is True
    assert clean.args == (1,) and clean.getMessage() == "plain 1"


def test_configure_logging_quiets_http_loggers_and_is_idempotent() -> None:
    root = logging.getLogger()
    before_filters = len(root.filters)
    configure_logging()
    configure_logging()
    for name in NOISY_HTTP_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING
        assert any(isinstance(f, SecretRedactingFilter) for f in logging.getLogger(name).filters)
    redacting = [f for f in root.filters if isinstance(f, SecretRedactingFilter)]
    assert len(redacting) == 1  # installed once, not per call
    assert len(root.filters) <= before_filters + 1
    for handler in root.handlers:
        assert sum(isinstance(f, SecretRedactingFilter) for f in handler.filters) <= 1


def test_root_handler_sees_redacted_httpx_line_even_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Re-run of the planner's leak experiment: ``httpx`` forced back to INFO ⇒ still ``***``."""
    configure_logging()
    httpx_logger = logging.getLogger("httpx")
    previous = httpx_logger.level
    httpx_logger.setLevel(logging.INFO)
    try:
        with caplog.at_level(logging.INFO):
            httpx_logger.info(
                "HTTP Request: GET https://graph.facebook.com/v26.0/oauth/access_token"
                '?client_secret=SUPERSECRET&fb_exchange_token=TOK "HTTP/1.1 200 OK"'
            )
    finally:
        httpx_logger.setLevel(previous)
    assert "SUPERSECRET" not in caplog.text
    assert "client_secret=***" in caplog.text
    assert "fb_exchange_token=***" in caplog.text
