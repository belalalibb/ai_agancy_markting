"""WhatsApp Cloud API client — hand-written on the shared ``httpx`` pool (D-071).

Reference (read-only, never copied): Meta *Cloud API* docs, Graph v26.0 —
``POST /{phone_number_id}/messages`` (text / template / ``status: read``) and
``GET /{phone_number_id}`` (``display_phone_number``, ``verified_name``).

Rules
-----
* D-064 — the system token travels **only** as ``Authorization: Bearer``; it is never put in
  the URL, never logged, and the caller ``del``-s its local copy in ``finally``.
* D-070 — every ``POST …/messages`` is sent **exactly once**: a failure before the request
  reached Meta (``ConnectError`` / ``ConnectTimeout``) ⇒ :class:`WhatsAppTransientError`;
  any failure after (read/write timeout, broken connection, HTTP 5xx) ⇒
  :class:`WhatsAppAmbiguousError`. Rate limits (130429 / 80007 / HTTP 429) are **never**
  retried. Only ``GET`` (``describe_phone_number``) is retried.
* §2-0 ج — message text is never logged (ids / lengths only); the recipient phone is
  masked to its last four digits.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from src.core.config import Settings, get_settings
from src.modules.whatsapp.exceptions import (
    WhatsAppAmbiguousError,
    WhatsAppAuthError,
    WhatsAppClientError,
    WhatsAppPermanentError,
    WhatsAppRateLimitedError,
    WhatsAppRecipientError,
    WhatsAppTransientError,
)

logger = logging.getLogger(__name__)

SleepFn = Callable[[float], Awaitable[None]]

_RATE_LIMIT_CODES: frozenset[int] = frozenset({130429, 80007, 4, 80004})
_RECIPIENT_CODES: frozenset[int] = frozenset({131026, 131047, 131051})
_AUTH_CODES: frozenset[int] = frozenset({190, 10})
_TRANSIENT_CODES: frozenset[int] = frozenset({1, 2, 131000})


@dataclass(frozen=True, slots=True)
class SentMessage:
    wamid: str


@dataclass(frozen=True, slots=True)
class PhoneNumberInfo:
    display_phone_number: str
    verified_name: str | None


def mask_phone(value: str | None) -> str:
    """``201234567890`` ⇒ ``…7890`` (what every log line shows instead of the number)."""
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    return f"…{digits[-4:]}" if digits else "…"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _retry_after(response: httpx.Response) -> float | None:
    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def map_cloud_error(payload: dict[str, Any], http_status: int, *, retry_after: float | None):  # noqa: ANN201
    """Graph ``{"error": {...}}`` ⇒ typed error (never forwards the provider message)."""
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = error.get("code") if isinstance(error.get("code"), int) else None
    subcode = error.get("error_subcode") if isinstance(error.get("error_subcode"), int) else None
    trace = error.get("fbtrace_id") if isinstance(error.get("fbtrace_id"), str) else None
    kwargs: dict[str, Any] = {
        "code": code,
        "subcode": subcode,
        "http_status": http_status,
        "fbtrace_id": trace,
    }
    if http_status == 429 or code in _RATE_LIMIT_CODES:
        return WhatsAppRateLimitedError(
            "WhatsApp rate limited", retry_after_seconds=retry_after, **kwargs
        )
    if code in _AUTH_CODES:
        return WhatsAppAuthError("WhatsApp credentials rejected", **kwargs)
    if code in _RECIPIENT_CODES:
        return WhatsAppRecipientError("WhatsApp recipient rejected", **kwargs)
    if http_status >= 500 or code in _TRANSIENT_CODES:
        return WhatsAppTransientError("WhatsApp upstream failure", **kwargs)
    return WhatsAppPermanentError("WhatsApp request rejected", **kwargs)


class WhatsAppCloudClient:
    """Thin Cloud API transport. Construct once per app with the shared pooled client."""

    def __init__(
        self,
        settings: Settings | None = None,
        http_client: httpx.AsyncClient | None = None,
        *,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self._settings = settings or get_settings()
        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(self._settings.meta_http_timeout_seconds)
        )
        self._sleep = sleep
        self._max_retries = self._settings.meta_http_max_retries

    @property
    def base_url(self) -> str:
        return self._settings.meta_graph_url

    # ----------------------------------------------------------------- reads ----
    async def describe_phone_number(self, token: str, phone_number_id: str) -> PhoneNumberInfo:
        """``GET /{phone_number_id}?fields=display_phone_number,verified_name`` — the one
        call used to verify a BYOK token at ``connect_account`` time (idempotent ⇒ retried)."""
        payload = await self._get(
            f"/{phone_number_id}",
            params={"fields": "display_phone_number,verified_name"},
            headers=_bearer(token),
        )
        display = payload.get("display_phone_number")
        if not isinstance(display, str) or not display:
            raise WhatsAppPermanentError("WhatsApp phone number payload malformed")
        name = payload.get("verified_name")
        return PhoneNumberInfo(display, name if isinstance(name, str) else None)

    # ---------------------------------------------------------------- writes ----
    async def send_text(
        self, token: str, phone_number_id: str, to_wa_id: str, body: str
    ) -> SentMessage:
        """``POST /{phone_number_id}/messages`` type ``text`` — exactly one attempt."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_wa_id,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        return await self._send(token, phone_number_id, payload, kind="text", length=len(body))

    async def send_template(
        self,
        token: str,
        phone_number_id: str,
        to_wa_id: str,
        name: str,
        language_code: str,
        components: list[dict[str, Any]] | None = None,
    ) -> SentMessage:
        """``POST /{phone_number_id}/messages`` type ``template`` — exactly one attempt."""
        template: dict[str, Any] = {"name": name, "language": {"code": language_code}}
        if components:
            template["components"] = components
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_wa_id,
            "type": "template",
            "template": template,
        }
        return await self._send(token, phone_number_id, payload, kind="template", length=0)

    async def mark_read(self, token: str, phone_number_id: str, wamid: str) -> None:
        """``POST /{phone_number_id}/messages`` ``{"status": "read"}`` — one attempt; the
        caller treats any failure as non-fatal."""
        payload = {"messaging_product": "whatsapp", "status": "read", "message_id": wamid}
        await self._post_once(f"/{phone_number_id}/messages", payload, headers=_bearer(token))

    # -------------------------------------------------------------- transport ----
    async def _send(
        self,
        token: str,
        phone_number_id: str,
        payload: dict[str, Any],
        *,
        kind: str,
        length: int,
    ) -> SentMessage:
        to = payload.get("to")
        try:
            data = await self._post_once(
                f"/{phone_number_id}/messages", payload, headers=_bearer(token)
            )
        except WhatsAppClientError as exc:
            logger.info(
                "wa send: to=%s kind=%s len=%d status=%s code=%s",
                mask_phone(to if isinstance(to, str) else None),
                kind,
                length,
                type(exc).__name__,
                exc.code,
            )
            raise
        messages = data.get("messages")
        wamid = (
            messages[0].get("id")
            if isinstance(messages, list) and messages and isinstance(messages[0], dict)
            else None
        )
        if not isinstance(wamid, str) or not wamid:
            # The POST succeeded but the body is unusable: the message *was* accepted.
            raise WhatsAppAmbiguousError("WhatsApp send response malformed", http_status=200)
        logger.info(
            "wa send: to=%s kind=%s len=%d status=sent",
            mask_phone(to if isinstance(to, str) else None),
            kind,
            length,
        )
        return SentMessage(wamid=wamid)

    async def _post_once(
        self, path: str, json: dict[str, Any], *, headers: dict[str, str]
    ) -> dict[str, Any]:
        """D-070: one network attempt; classified *not sent* vs *maybe sent*."""
        url = f"{self.base_url}{path}"
        try:
            response = await self._http.post(url, json=json, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            logger.warning("WhatsApp connect failure: %s", type(exc).__name__)
            raise WhatsAppTransientError("WhatsApp connect failure (request not sent)") from exc
        except httpx.HTTPError as exc:
            logger.warning("WhatsApp ambiguous transport failure: %s", type(exc).__name__)
            raise WhatsAppAmbiguousError("WhatsApp send outcome unknown") from exc
        payload = _safe_json(response)
        if response.is_success and "error" not in payload:
            return payload
        error = map_cloud_error(payload, response.status_code, retry_after=_retry_after(response))
        if isinstance(error, WhatsAppTransientError):
            # A 5xx *after* a POST: Meta may have delivered the message.
            raise WhatsAppAmbiguousError(
                error.message,
                code=error.code,
                subcode=error.subcode,
                http_status=error.http_status,
                fbtrace_id=error.fbtrace_id,
            ) from None
        raise error

    async def _get(
        self, path: str, *, params: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        attempt = 0
        while True:
            try:
                response = await self._http.get(url, params=params, headers=headers)
            except httpx.TimeoutException as exc:
                if attempt < self._max_retries:
                    await self._sleep(min(0.5 * (2**attempt), 8.0))
                    attempt += 1
                    continue
                raise WhatsAppTransientError("WhatsApp read timeout") from exc
            except httpx.HTTPError as exc:
                raise WhatsAppTransientError("WhatsApp network error") from exc
            payload = _safe_json(response)
            if response.is_success and "error" not in payload:
                return payload
            error = map_cloud_error(
                payload, response.status_code, retry_after=_retry_after(response)
            )
            if isinstance(error, WhatsAppTransientError) and attempt < self._max_retries:
                await self._sleep(min(0.5 * (2**attempt), 8.0))
                attempt += 1
                continue
            raise error
