"""Unit tests — HMAC-signed OAuth ``state`` (SPRINT-02 A5 / C2)."""

from __future__ import annotations

import base64
import json

import pytest
from pydantic import SecretStr
from src.core.config import Settings
from src.core.oauth_state import (
    InvalidOAuthStateError,
    OAuthStateSecretMissingError,
    OAuthStateSigner,
    get_oauth_state_signer,
)

pytestmark = pytest.mark.unit

SECRET = "unit-test-state-secret"


class FakeClock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def signer(clock: FakeClock) -> OAuthStateSigner:
    return OAuthStateSigner(SECRET, ttl_seconds=600, clock=clock)


def test_oauth_state_roundtrip(signer: OAuthStateSigner) -> None:
    state = signer.issue("tenant-a")
    assert state.count(".") == 1
    assert signer.verify(state) == "tenant-a"


def test_oauth_state_is_unique_per_issue(signer: OAuthStateSigner) -> None:
    assert signer.issue("t") != signer.issue("t")


def test_oauth_state_tampered_rejected(signer: OAuthStateSigner) -> None:
    state = signer.issue("tenant-a")
    body, sig = state.split(".")
    # Flip the tenant inside the payload while keeping the original signature.
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload["t"] = "tenant-b"
    forged_body = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .rstrip(b"=")
        .decode()
    )
    with pytest.raises(InvalidOAuthStateError, match="signature"):
        signer.verify(f"{forged_body}.{sig}")
    # Flipping a signature *bit* must fail too (SPRINT-02.1 F1).
    # The 32-byte HMAC is 43 base64url chars; the last char only carries 2 significant
    # bits, so swapping that *character* could decode to the very same bytes and the
    # old "A"/"B" character swap was therefore flaky. Tamper at the byte level instead.
    sig_bytes = bytearray(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)))
    sig_bytes[0] ^= 0x01
    flipped = base64.urlsafe_b64encode(bytes(sig_bytes)).rstrip(b"=").decode()
    with pytest.raises(InvalidOAuthStateError, match="signature"):
        signer.verify(f"{body}.{flipped}")


def test_oauth_state_expired_rejected(signer: OAuthStateSigner, clock: FakeClock) -> None:
    state = signer.issue("tenant-a")
    clock.now += 599
    assert signer.verify(state) == "tenant-a"
    clock.now += 2
    with pytest.raises(InvalidOAuthStateError, match="expired"):
        signer.verify(state)


def test_oauth_state_future_rejected(signer: OAuthStateSigner, clock: FakeClock) -> None:
    state = signer.issue("tenant-a")
    clock.now -= 61
    with pytest.raises(InvalidOAuthStateError, match="future"):
        signer.verify(state)


def test_oauth_state_wrong_secret_rejected(signer: OAuthStateSigner, clock: FakeClock) -> None:
    other = OAuthStateSigner("another-secret", clock=clock)
    with pytest.raises(InvalidOAuthStateError):
        other.verify(signer.issue("tenant-a"))


@pytest.mark.parametrize(
    "garbage",
    ["", "no-dot", "a.b.c", "!!!.???", "####.abcd", "e30.AAAA"],
)
def test_oauth_state_malformed_rejected(signer: OAuthStateSigner, garbage: str) -> None:
    with pytest.raises(InvalidOAuthStateError):
        signer.verify(garbage)


def test_oauth_state_payload_missing_fields_rejected(signer: OAuthStateSigner) -> None:
    for payload in (b"[]", b'{"t":"","iat":1}', b'{"t":"x"}', b"not-json"):
        body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
        sig = base64.urlsafe_b64encode(signer._sign(payload)).rstrip(b"=").decode()
        with pytest.raises(InvalidOAuthStateError):
            signer.verify(f"{body}.{sig}")


def test_empty_secret_rejected() -> None:
    with pytest.raises(OAuthStateSecretMissingError):
        OAuthStateSigner("")


def test_factory_reads_settings(settings: Settings) -> None:
    signer = get_oauth_state_signer(settings)
    assert signer.verify(signer.issue("t")) == "t"


def test_factory_requires_secret(settings: Settings) -> None:
    broken = settings.model_copy(update={"oauth_state_secret": None})
    with pytest.raises(OAuthStateSecretMissingError, match="OAUTH_STATE_SECRET"):
        get_oauth_state_signer(broken)
    empty = settings.model_copy(update={"oauth_state_secret": SecretStr("")})
    with pytest.raises(OAuthStateSecretMissingError):
        get_oauth_state_signer(empty)
