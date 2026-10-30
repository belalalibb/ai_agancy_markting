"""Stateless CSRF protection for the OAuth ``state`` parameter (RFC 6749 §10.12).

``state = base64url(payload) + "." + base64url(hmac_sha256(secret, payload))``

The payload is ``{"t": tenant_id, "b": brand_id, "n": nonce, "iat": unix_ts}`` (SPRINT-03:
both UUIDs). Verification checks the signature in constant time, rejects expired /
future-dated states and returns ``(tenant_id, brand_id)`` so the callback knows which
acting tenant started the flow and for which brand.

Deliberately separate from :mod:`src.core.security` (Fernet token encryption): the two
mechanisms protect different things and must never share a key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

from src.core.config import Settings, get_settings

# Replay within the TTL is bounded by Meta's single-use ``code``; a server-side nonce store
# (Redis) is deferred (owner decision, see Hub deferred list).

_MAX_FUTURE_SKEW_SECONDS = 60


class InvalidOAuthStateError(ValueError):
    """The ``state`` is malformed, tampered with, expired or signed with another key."""


class OAuthStateSecretMissingError(RuntimeError):
    """``OAUTH_STATE_SECRET`` is not configured."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


class OAuthStateSigner:
    """Issue and verify HMAC-SHA256 signed, time-limited OAuth ``state`` values."""

    def __init__(
        self,
        secret: str,
        ttl_seconds: int = 600,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not secret:
            raise OAuthStateSecretMissingError("OAUTH_STATE_SECRET must not be empty.")
        self._key = secret.encode("utf-8")
        self._ttl = ttl_seconds
        self._clock = clock

    # ------------------------------------------------------------------ API --
    def issue(self, tenant_id: UUID, brand_id: UUID, user_id: UUID | None = None) -> str:
        """Sign ``{t, b, u?, n, iat}``; ``u`` (SPRINT-04 B3) records *which user* started the
        flow — audit only, never used for authorisation."""
        payload: dict[str, Any] = {
            "t": str(tenant_id),
            "b": str(brand_id),
            "n": secrets.token_urlsafe(16),
            "iat": int(self._clock()),
        }
        if user_id is not None:
            payload["u"] = str(user_id)
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return f"{_b64encode(body)}.{_b64encode(self._sign(body))}"

    def verify(self, state: str) -> tuple[UUID, UUID]:
        """Return ``(tenant_id, brand_id)`` embedded in a valid ``state``; raise otherwise."""
        payload = self.decode_payload(state)
        return payload["t"], payload["b"]

    def decode_payload(self, state: str) -> dict[str, Any]:
        """Verify signature + TTL and return the full claims (``t``, ``b``, optional ``u``,
        ``n``, ``iat``) with ids as ``UUID``. Used by ``verify`` and by audit/tests."""
        if not isinstance(state, str) or state.count(".") != 1:
            raise InvalidOAuthStateError("Malformed OAuth state.")
        body_b64, sig_b64 = state.split(".", 1)
        try:
            body = _b64decode(body_b64)
            signature = _b64decode(sig_b64)
        except (ValueError, TypeError) as exc:
            raise InvalidOAuthStateError("Malformed OAuth state encoding.") from exc

        if not hmac.compare_digest(signature, self._sign(body)):
            raise InvalidOAuthStateError("OAuth state signature mismatch.")

        payload = self._decode_payload(body)
        now = self._clock()
        issued_at = payload["iat"]
        if issued_at > now + _MAX_FUTURE_SKEW_SECONDS:
            raise InvalidOAuthStateError("OAuth state issued in the future.")
        if now - issued_at > self._ttl:
            raise InvalidOAuthStateError("OAuth state has expired.")
        return payload

    # -------------------------------------------------------------- helpers --
    def _sign(self, body: bytes) -> bytes:
        return hmac.new(self._key, body, hashlib.sha256).digest()

    @staticmethod
    def _decode_payload(body: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise InvalidOAuthStateError("OAuth state payload is not valid JSON.") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("iat"), int):
            raise InvalidOAuthStateError("OAuth state payload is missing required fields.")
        for key in ("t", "b", "u"):
            value = payload.get(key)
            if value is None and key == "u":  # optional audit claim
                continue
            if not isinstance(value, str) or not value:
                raise InvalidOAuthStateError("OAuth state payload is missing required fields.")
            try:
                payload[key] = UUID(value)
            except ValueError as exc:
                raise InvalidOAuthStateError("OAuth state payload has a non-UUID id.") from exc
        return payload


def get_oauth_state_signer(settings: Settings | None = None) -> OAuthStateSigner:
    """Build a signer from settings; raises if ``OAUTH_STATE_SECRET`` is unset."""
    settings = settings or get_settings()
    if settings.oauth_state_secret is None or not settings.oauth_state_secret.get_secret_value():
        raise OAuthStateSecretMissingError(
            "OAUTH_STATE_SECRET is not set. Generate one with "
            '`python -c "import secrets; print(secrets.token_urlsafe(32))"`.'
        )
    return OAuthStateSigner(
        settings.oauth_state_secret.get_secret_value(),
        ttl_seconds=settings.oauth_state_ttl_seconds,
    )
