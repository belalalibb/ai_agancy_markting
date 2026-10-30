"""Unit tests — HMAC-signed OAuth ``state`` (SPRINT-02 A5 / C2, SPRINT-03 B-U14).

SPRINT-03: the payload carries ``t`` (tenant) **and** ``b`` (brand), both UUIDs.
"""

from __future__ import annotations

import base64
import json
from uuid import UUID, uuid4

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
TENANT_A = UUID("11111111-1111-4111-8111-111111111111")
TENANT_B = UUID("22222222-2222-4222-8222-222222222222")
BRAND = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


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
    state = signer.issue(TENANT_A, BRAND)
    assert state.count(".") == 1
    assert signer.verify(state) == (TENANT_A, BRAND)
    payload = json.loads(base64.urlsafe_b64decode(state.split(".")[0] + "=="))
    assert set(payload) == {"t", "b", "n", "iat"}


def test_oauth_state_is_unique_per_issue(signer: OAuthStateSigner) -> None:
    assert signer.issue(TENANT_A, BRAND) != signer.issue(TENANT_A, BRAND)


def test_oauth_state_tampered_rejected(signer: OAuthStateSigner) -> None:
    state = signer.issue(TENANT_A, BRAND)
    body, sig = state.split(".")
    # Flip the tenant inside the payload while keeping the original signature.
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload["t"] = str(TENANT_B)
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
    state = signer.issue(TENANT_A, BRAND)
    clock.now += 599
    assert signer.verify(state) == (TENANT_A, BRAND)
    clock.now += 2
    with pytest.raises(InvalidOAuthStateError, match="expired"):
        signer.verify(state)


def test_oauth_state_future_rejected(signer: OAuthStateSigner, clock: FakeClock) -> None:
    state = signer.issue(TENANT_A, BRAND)
    clock.now -= 61
    with pytest.raises(InvalidOAuthStateError, match="future"):
        signer.verify(state)


def test_oauth_state_wrong_secret_rejected(signer: OAuthStateSigner, clock: FakeClock) -> None:
    other = OAuthStateSigner("another-secret", clock=clock)
    with pytest.raises(InvalidOAuthStateError):
        other.verify(signer.issue(TENANT_A, BRAND))


@pytest.mark.parametrize(
    "garbage",
    ["", "no-dot", "a.b.c", "!!!.???", "####.abcd", "e30.AAAA"],
)
def test_oauth_state_malformed_rejected(signer: OAuthStateSigner, garbage: str) -> None:
    with pytest.raises(InvalidOAuthStateError):
        signer.verify(garbage)


def _signed(signer: OAuthStateSigner, payload: bytes) -> str:
    body = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(signer._sign(payload)).rstrip(b"=").decode()
    return f"{body}.{sig}"


def test_oauth_state_payload_missing_fields_rejected(signer: OAuthStateSigner) -> None:
    t, b = str(TENANT_A), str(BRAND)
    for payload in (
        b"[]",
        b'{"t":"","iat":1}',
        b'{"t":"x"}',
        b"not-json",
        f'{{"t":"{t}","b":"{b}"}}'.encode(),  # no iat
        f'{{"t":"{t}","b":"","iat":1800000000}}'.encode(),  # empty b
        f'{{"t":"{t}","b":123,"iat":1800000000}}'.encode(),  # b not a string
    ):
        with pytest.raises(InvalidOAuthStateError):
            signer.verify(_signed(signer, payload))


# ----------------------------------------------------------------- B-U14 ---
def test_oauth_state_payload_without_brand_rejected(
    signer: OAuthStateSigner, clock: FakeClock
) -> None:
    """A legacy (SPRINT-02) state with only ``t`` is rejected even when correctly signed."""
    legacy = json.dumps(
        {"t": str(TENANT_A), "n": "nonce", "iat": int(clock.now)}, separators=(",", ":")
    ).encode()
    with pytest.raises(InvalidOAuthStateError, match="missing required fields"):
        signer.verify(_signed(signer, legacy))


@pytest.mark.parametrize("bad", ["demo", "not-a-uuid", "12345678-1234", " "])
def test_oauth_state_non_uuid_ids_rejected(
    signer: OAuthStateSigner, clock: FakeClock, bad: str
) -> None:
    for key in ("t", "b"):
        payload = {"t": str(TENANT_A), "b": str(BRAND), "n": "nonce", "iat": int(clock.now)}
        payload[key] = bad
        raw = json.dumps(payload, separators=(",", ":")).encode()
        with pytest.raises(InvalidOAuthStateError, match="non-UUID"):
            signer.verify(_signed(signer, raw))


def test_oauth_state_verify_returns_uuid_instances(signer: OAuthStateSigner) -> None:
    t, b = signer.verify(signer.issue(TENANT_A, BRAND))
    assert isinstance(t, UUID) and isinstance(b, UUID)
    other = uuid4()
    assert signer.verify(signer.issue(other, BRAND)) == (other, BRAND)


def test_empty_secret_rejected() -> None:
    with pytest.raises(OAuthStateSecretMissingError):
        OAuthStateSigner("")


def test_factory_reads_settings(settings: Settings) -> None:
    signer = get_oauth_state_signer(settings)
    assert signer.verify(signer.issue(TENANT_A, BRAND)) == (TENANT_A, BRAND)


def test_factory_requires_secret(settings: Settings) -> None:
    broken = settings.model_copy(update={"oauth_state_secret": None})
    with pytest.raises(OAuthStateSecretMissingError, match="OAUTH_STATE_SECRET"):
        get_oauth_state_signer(broken)
    empty = settings.model_copy(update={"oauth_state_secret": SecretStr("")})
    with pytest.raises(OAuthStateSecretMissingError):
        get_oauth_state_signer(empty)


# ------------------------------------------------------- SPRINT-04 B3: claim ``u`` ---
def test_oauth_state_carries_optional_user_claim(signer: OAuthStateSigner) -> None:
    user = uuid4()
    with_user = signer.issue(TENANT_A, BRAND, user)
    payload = signer.decode_payload(with_user)
    assert payload["t"] == TENANT_A and payload["b"] == BRAND and payload["u"] == user
    assert isinstance(payload["u"], UUID)
    # ``verify`` is unchanged: still the (tenant, brand) pair.
    assert signer.verify(with_user) == (TENANT_A, BRAND)
    # Without a user the claim is absent — older states remain valid.
    assert "u" not in signer.decode_payload(signer.issue(TENANT_A, BRAND))


def test_oauth_state_rejects_non_uuid_user_claim(signer: OAuthStateSigner) -> None:
    body = json.dumps(
        {"t": str(TENANT_A), "b": str(BRAND), "u": "not-a-uuid", "n": "x", "iat": 1_800_000_000},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    body_b64 = base64.urlsafe_b64encode(body).rstrip(b"=").decode()
    sig_b64 = base64.urlsafe_b64encode(signer._sign(body)).rstrip(b"=").decode()  # noqa: SLF001
    with pytest.raises(InvalidOAuthStateError, match="non-UUID"):
        signer.verify(f"{body_b64}.{sig_b64}")
