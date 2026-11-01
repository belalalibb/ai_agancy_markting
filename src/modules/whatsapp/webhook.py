"""Signed WhatsApp Cloud API webhook (SPRINT-09 B-1, §2-0 ب).

Payload shape follows the official Cloud API **v26.0** webhook reference (read-only; no
PyWa code): ``entry[].changes[].value`` with ``messaging_product``, ``metadata``
(``phone_number_id``, ``display_phone_number``), ``contacts[]`` (``wa_id``,
``profile.name``), ``messages[]`` (``id`` = wamid, ``from``, ``timestamp`` (unix seconds as
a string), ``type`` and a typed sub-object such as ``text.body`` / ``image.id``) and
``statuses[]`` (``id`` = wamid of *our* message, ``status`` ∈ sent/delivered/read/failed,
``errors[].code``).

Rules
-----
* ``GET`` — Meta's subscription handshake: ``hub.mode=subscribe`` + ``hub.verify_token``
  matching an *active* account's ``verify_token_hash`` ⇒ ``200 text/plain hub.challenge``;
  otherwise 403.
* ``POST`` — SPRINT-10 A-5 order: body streamed with a **1 MiB cap** (over ⇒ 413
  ``payload_too_large`` before the rest is read) ⇒ ``X-Hub-Signature-256`` present and shaped
  ``^sha256=[0-9a-f]{64}$`` else 401 **before any ``json.loads`` or DB query** ⇒ narrow
  extraction of ``entry[*].changes[*].value.metadata.phone_number_id`` ⇒ accounts loaded ⇒
  ``hmac.compare_digest(sha256=HMAC_SHA256(app_secret, raw_body), header)``; unknown number /
  mismatch ⇒ 401 (``counters["wa_webhook_rejected"]``). Once verified the response is
  **always 200** — unparsed payloads only increment ``counters["wa_webhook_unparsed"]`` so
  Meta never retries a poison message forever.
* A-3 isolation: every status update is scoped to the ``brand_id`` of the account whose
  signature verified, and must move the delivery status **forward** (``queued < sent <
  delivered < read``); anything else is counted in ``ignored_status_updates``.
* Inbound ``messages[]`` ⇒ ``INSERT … ON CONFLICT (wamid) DO NOTHING`` (dup ⇒ 200,
  ``wa_duplicate``), conversation upsert (``last_inbound_at``, ``window_expires_at = ts +
  24h``, ``unread_count + 1``, archived ⇒ reopen), pending ``suggested`` drafts ⇒
  ``expired`` (+ learning signal, B-3). ``statuses[]`` ⇒ outbound ``delivery_status``.
* Message text is never logged (ids / lengths only).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.database import get_db_session
from src.core.metrics import counters
from src.core.security import (
    EncryptionKeyMissingError,
    TokenEncryptionService,
    get_token_encryption_service,
)
from src.modules.whatsapp.domain.models import (
    WINDOW,
    AccountStatus,
    DeliveryStatus,
    Direction,
    Message,
    MessageKind,
    WhatsAppAccount,
    message_kind_from_type,
)
from src.modules.whatsapp.domain.repository import WhatsAppRepository
from src.modules.whatsapp.infrastructure.postgres_repository import PostgresWhatsAppRepository
from src.modules.whatsapp.learning import record_expired
from src.modules.whatsapp.service import hash_verify_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["WhatsApp — webhook (public, signed)"])

SIGNATURE_HEADER = "X-Hub-Signature-256"
SIGNATURE_PATTERN = re.compile(r"^sha256=[0-9a-f]{64}$")
MAX_BODY_BYTES = 1024 * 1024  # A-5: 1 MiB — Meta's real payloads are a few KiB
_STATUS_MAP: dict[str, DeliveryStatus] = {
    "sent": DeliveryStatus.SENT,
    "delivered": DeliveryStatus.DELIVERED,
    "read": DeliveryStatus.READ,
    "failed": DeliveryStatus.FAILED,
}
_MEDIA_KINDS = frozenset(
    {
        MessageKind.IMAGE,
        MessageKind.AUDIO,
        MessageKind.VIDEO,
        MessageKind.DOCUMENT,
        MessageKind.STICKER,
    }
)


# ------------------------------------------------------------------ signature ---
def compute_signature(app_secret: str, raw_body: bytes) -> str:
    return "sha256=" + hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    return hmac.compare_digest(compute_signature(app_secret, raw_body), header)


def extract_phone_number_ids(payload: Any) -> list[str]:  # noqa: ANN401 - raw JSON
    """Every ``entry[].changes[].value.metadata.phone_number_id`` in the payload."""
    ids: list[str] = []
    if not isinstance(payload, dict):
        return ids
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata")
            if isinstance(metadata, dict):
                pid = metadata.get("phone_number_id")
                if isinstance(pid, str) and pid and pid not in ids:
                    ids.append(pid)
    return ids


# -------------------------------------------------------------------- outcome ---
@dataclass(slots=True)
class WebhookOutcome:
    inbound: int = 0
    duplicates: int = 0
    status_updates: int = 0
    ignored_status_updates: int = 0  # A-3: other brand / unknown wamid / backwards move
    unparsed: int = 0
    expired_drafts: list[UUID] = field(default_factory=list)


def _ts(value: Any) -> datetime:  # noqa: ANN401 - raw JSON
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return datetime.now(tz=UTC)


def _profile_names(value: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for contact in value.get("contacts") or []:
        if not isinstance(contact, dict):
            continue
        wa_id = contact.get("wa_id")
        profile = contact.get("profile")
        name = profile.get("name") if isinstance(profile, dict) else None
        if isinstance(wa_id, str) and isinstance(name, str) and name:
            names[wa_id] = name[:128]
    return names


def _message_fields(raw: dict[str, Any]) -> tuple[MessageKind, str | None, str | None]:
    kind = message_kind_from_type(raw.get("type"))
    body: str | None = None
    media_id: str | None = None
    if kind is MessageKind.TEXT:
        text = raw.get("text")
        candidate = text.get("body") if isinstance(text, dict) else None
        body = candidate[:4096] if isinstance(candidate, str) else None
    elif kind in _MEDIA_KINDS:
        media = raw.get(kind.value)
        if isinstance(media, dict):
            mid = media.get("id")
            media_id = mid[:128] if isinstance(mid, str) else None
            caption = media.get("caption")
            body = caption[:4096] if isinstance(caption, str) else None
    return kind, body, media_id


class WebhookProcessor:
    """Processes one verified payload inside the request transaction."""

    def __init__(self, repo: WhatsAppRepository) -> None:
        self._repo = repo

    async def handle_webhook_payload(
        self, payload: Any, *, accounts: dict[str, WhatsAppAccount]
    ) -> WebhookOutcome:  # noqa: ANN401 - raw JSON
        outcome = WebhookOutcome()
        if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
            outcome.unparsed += 1
            return outcome
        for entry in payload.get("entry") or []:
            if not isinstance(entry, dict):
                outcome.unparsed += 1
                continue
            for change in entry.get("changes") or []:
                value = change.get("value") if isinstance(change, dict) else None
                if not isinstance(value, dict):
                    outcome.unparsed += 1
                    continue
                metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
                account = accounts.get(str(metadata.get("phone_number_id", "")))
                if account is None:
                    outcome.unparsed += 1
                    continue
                await self._handle_value(value, account, outcome)
        return outcome

    async def _handle_value(
        self, value: dict[str, Any], account: WhatsAppAccount, outcome: WebhookOutcome
    ) -> None:
        names = _profile_names(value)
        for raw in value.get("messages") or []:
            if not isinstance(raw, dict):
                outcome.unparsed += 1
                continue
            await self._handle_inbound(raw, account, names, outcome)
        for raw in value.get("statuses") or []:
            if not isinstance(raw, dict):
                outcome.unparsed += 1
                continue
            await self._handle_status(raw, outcome, account=account)

    async def _handle_inbound(
        self,
        raw: dict[str, Any],
        account: WhatsAppAccount,
        names: dict[str, str],
        outcome: WebhookOutcome,
    ) -> None:
        wamid = raw.get("id")
        wa_id = raw.get("from")
        if not isinstance(wamid, str) or not wamid or not isinstance(wa_id, str) or not wa_id:
            outcome.unparsed += 1
            return
        at = _ts(raw.get("timestamp"))
        kind, body, media_id = _message_fields(raw)
        conversation = await self._repo.upsert_conversation_inbound(
            brand_id=account.brand_id,
            account_id=account.id,
            wa_id=wa_id[:32],
            profile_name=names.get(wa_id),
            at=at,
            window_expires_at=at + WINDOW,
        )
        inserted = await self._repo.insert_inbound_if_new(
            Message(
                conversation_id=conversation.id,
                brand_id=account.brand_id,
                direction=Direction.INBOUND,
                wamid=wamid[:128],
                kind=kind,
                body=body,
                media_id=media_id,
                delivery_status=DeliveryStatus.RECEIVED,
                platform_timestamp=at,
                created_at=datetime.now(tz=UTC),
            )
        )
        if not inserted:
            # Meta redelivered a message we already stored: undo the unread bump.
            await self._repo.mark_conversation_read_delta(conversation.id, -1)
            outcome.duplicates += 1
            counters.increment("wa_duplicate")
            logger.info("wa webhook duplicate: conv=%s", conversation.id)
            return
        outcome.inbound += 1
        counters.increment("wa_inbound")
        now = datetime.now(tz=UTC)
        expired = await self._repo.expire_pending_drafts(conversation.id, now=now)
        await record_expired(self._repo, expired, now=now)  # B-3: ``expired`` signals (D-069)
        outcome.expired_drafts.extend(d.id for d in expired)
        logger.info(
            "wa inbound: brand=%s conv=%s kind=%s len=%d",
            account.brand_id,
            conversation.id,
            kind.value,
            len(body or ""),
        )

    async def _handle_status(
        self, raw: dict[str, Any], outcome: WebhookOutcome, *, account: WhatsAppAccount
    ) -> None:
        wamid = raw.get("id")
        new = _STATUS_MAP.get(str(raw.get("status", "")))
        if not isinstance(wamid, str) or new is None:
            outcome.unparsed += 1
            return
        error_code: int | None = None
        errors = raw.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            code = errors[0].get("code")
            error_code = code if isinstance(code, int) else None
        if await self._repo.update_status_by_wamid(
            wamid, new, brand_id=account.brand_id, error_code=error_code
        ):
            outcome.status_updates += 1
            counters.increment("wa_status_updates")
        else:
            outcome.ignored_status_updates += 1
            counters.increment("wa_status_ignored")


# ----------------------------------------------------------------------- routes ---
async def read_body_capped(request: Request, *, limit: int = MAX_BODY_BYTES) -> bytes:
    """A-5: ``Content-Length`` over the cap ⇒ 413 immediately; otherwise the body is streamed
    with a byte counter and the read **stops** at the first chunk crossing the cap."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        counters.increment("wa_webhook_rejected")
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, detail={"error": "payload_too_large"}
        )
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            counters.increment("wa_webhook_rejected")
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE, detail={"error": "payload_too_large"}
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _repo(session: Annotated[AsyncSession, Depends(get_db_session)]) -> WhatsAppRepository:
    return PostgresWhatsAppRepository(session)


Repo = Annotated[WhatsAppRepository, Depends(_repo)]


def _encryption(settings: Annotated[Settings, Depends(get_settings)]) -> TokenEncryptionService:
    try:
        return get_token_encryption_service(settings)
    except EncryptionKeyMissingError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token encryption is not configured on the server.",
        ) from exc


Encryption = Annotated[TokenEncryptionService, Depends(_encryption)]

WEBHOOK_PATH = "/webhooks/whatsapp"


@router.get(
    WEBHOOK_PATH,
    summary="Meta subscription handshake (hub.challenge) — public",
    response_class=Response,
)
async def verify_webhook(
    repo: Repo,
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> Response:
    if hub_mode != "subscribe" or not hub_verify_token or hub_challenge is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"error": "webhook_forbidden"})
    digest = hash_verify_token(hub_verify_token)
    for account in await repo.list_active_accounts():
        if hmac.compare_digest(account.verify_token_hash, digest):
            return Response(content=hub_challenge, media_type="text/plain")
    counters.increment("wa_webhook_rejected")
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"error": "webhook_forbidden"})


@router.post(
    WEBHOOK_PATH,
    summary="Cloud API events — signature mandatory (X-Hub-Signature-256)",
    status_code=status.HTTP_200_OK,
)
async def receive_webhook(
    request: Request, repo: Repo, encryption: Encryption
) -> dict[str, int | list[str]]:
    raw_body = await read_body_capped(request)  # A-5 (1): 1 MiB cap, streamed
    header = request.headers.get(SIGNATURE_HEADER)
    if header is None or SIGNATURE_PATTERN.fullmatch(header) is None:
        # A-5 (2): shape check before any json.loads / DB round-trip.
        counters.increment("wa_webhook_rejected")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "webhook_unverified"})
    try:
        payload = json.loads(raw_body or b"null")
    except ValueError:
        payload = None
    phone_number_ids = extract_phone_number_ids(payload)
    accounts: dict[str, WhatsAppAccount] = {}
    for pid in phone_number_ids:
        account = await repo.get_account_by_phone_number_id(pid)
        if account is not None and account.status is AccountStatus.ACTIVE:
            accounts[pid] = account
    if not accounts:
        counters.increment("wa_webhook_rejected")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "webhook_unverified"})
    verified = False
    for account in accounts.values():
        app_secret = encryption.decrypt_token(account.encrypted_app_secret)
        try:
            verified = verify_signature(app_secret, raw_body, header)
        finally:
            del app_secret
        if not verified:
            break
    if not verified:
        counters.increment("wa_webhook_rejected")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"error": "webhook_unverified"})

    outcome = await WebhookProcessor(repo).handle_webhook_payload(payload, accounts=accounts)
    if outcome.unparsed:
        counters.increment("wa_webhook_unparsed", outcome.unparsed)
    return {
        "inbound": outcome.inbound,
        "duplicates": outcome.duplicates,
        "status_updates": outcome.status_updates,
        "ignored_status_updates": outcome.ignored_status_updates,  # A-3
        "unparsed": outcome.unparsed,
        "expired_drafts": [str(d) for d in outcome.expired_drafts],  # B-3
    }
