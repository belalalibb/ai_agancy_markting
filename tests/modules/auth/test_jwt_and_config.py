"""A-U5 (access JWT) and A-U12 (settings) unit tests."""

from __future__ import annotations

import warnings
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from pydantic import SecretStr, ValidationError
from src.core.config import Settings
from src.modules.auth.domain.models import AuthenticatedActor
from src.modules.auth.jwt import ALGORITHM, AUDIENCE, AccessTokenCodec

from tests.conftest import FAKE_JWT_SECRET

pytestmark = pytest.mark.unit

ISSUER = "AI Growth OS"
ACTOR = AuthenticatedActor(user_id=uuid4(), tenant_id=uuid4(), sid=uuid4())


@pytest.fixture
def codec() -> AccessTokenCodec:
    return AccessTokenCodec(SecretStr(FAKE_JWT_SECRET), issuer=ISSUER, ttl_seconds=900)


def _raw(**overrides: object) -> dict[str, object]:
    now = int(datetime.now(tz=UTC).timestamp())
    claims: dict[str, object] = {
        "sub": str(ACTOR.user_id),
        "tid": str(ACTOR.tenant_id),
        "sid": str(ACTOR.sid),
        "iat": now,
        "exp": now + 900,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "jti": "x",
    }
    claims.update(overrides)
    return claims


# ----------------------------------------------------------------- A-U5 --
def test_issue_sets_all_claims_and_exp_is_iat_plus_ttl(codec: AccessTokenCodec) -> None:
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    token = codec.issue(ACTOR, now=now)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == ALGORITHM
    claims = jwt.decode(token, options={"verify_signature": False}, algorithms=[ALGORITHM])
    assert claims["sub"] == str(ACTOR.user_id)
    assert claims["tid"] == str(ACTOR.tenant_id)
    assert claims["sid"] == str(ACTOR.sid)
    assert claims["iss"] == ISSUER
    assert claims["aud"] == AUDIENCE
    assert claims["iat"] == int(now.timestamp())
    assert claims["exp"] == claims["iat"] + 900
    assert len(claims["jti"]) >= 16
    # Round-trip with a *current* token (the fixed ``now`` above is in the past).
    assert codec.decode(codec.issue(ACTOR)) == ACTOR
    assert codec.ttl_seconds == 900


def test_two_tokens_have_distinct_jti(codec: AccessTokenCodec) -> None:
    a = jwt.decode(codec.issue(ACTOR), options={"verify_signature": False}, algorithms=["HS256"])
    b = jwt.decode(codec.issue(ACTOR), options={"verify_signature": False}, algorithms=["HS256"])
    assert a["jti"] != b["jti"]


def test_wrong_audience_rejected(codec: AccessTokenCodec) -> None:
    token = jwt.encode(_raw(aud="other"), FAKE_JWT_SECRET, algorithm=ALGORITHM)
    with pytest.raises(jwt.InvalidAudienceError):
        codec.decode(token)


def test_wrong_issuer_rejected(codec: AccessTokenCodec) -> None:
    token = jwt.encode(_raw(iss="someone-else"), FAKE_JWT_SECRET, algorithm=ALGORITHM)
    with pytest.raises(jwt.InvalidIssuerError):
        codec.decode(token)


def test_alg_none_rejected(codec: AccessTokenCodec) -> None:
    token = jwt.encode(_raw(), None, algorithm="none")  # type: ignore[arg-type]
    with pytest.raises(jwt.InvalidTokenError):
        codec.decode(token)


def test_expired_token_rejected(codec: AccessTokenCodec) -> None:
    past = int((datetime.now(tz=UTC) - timedelta(hours=1)).timestamp())
    token = jwt.encode(_raw(iat=past, exp=past + 900), FAKE_JWT_SECRET, algorithm=ALGORITHM)
    with pytest.raises(jwt.ExpiredSignatureError):
        codec.decode(token)
    # Issuing with a backdated ``now`` produces an already-expired token too.
    with pytest.raises(jwt.ExpiredSignatureError):
        codec.decode(codec.issue(ACTOR, now=datetime.now(tz=UTC) - timedelta(seconds=901)))


def test_other_secret_rejected(codec: AccessTokenCodec) -> None:
    token = jwt.encode(_raw(), "another-secret-that-is-long-enough-00000", algorithm=ALGORITHM)
    with pytest.raises(jwt.InvalidSignatureError):
        codec.decode(token)


def test_hs512_signed_token_rejected(codec: AccessTokenCodec) -> None:
    """Algorithm pinning: anything but HS256 is refused even with the right key."""
    token = jwt.encode(_raw(), FAKE_JWT_SECRET, algorithm="HS512")
    with pytest.raises(jwt.InvalidAlgorithmError):
        codec.decode(token)


@pytest.mark.parametrize("missing", ["sub", "tid", "sid", "iat", "exp", "iss", "aud"])
def test_missing_required_claim_rejected(codec: AccessTokenCodec, missing: str) -> None:
    claims = _raw()
    del claims[missing]
    token = jwt.encode(claims, FAKE_JWT_SECRET, algorithm=ALGORITHM)
    with pytest.raises(jwt.InvalidTokenError):
        codec.decode(token)


def test_malformed_identity_claims_rejected(codec: AccessTokenCodec) -> None:
    token = jwt.encode(_raw(sub="not-a-uuid"), FAKE_JWT_SECRET, algorithm=ALGORITHM)
    with pytest.raises(jwt.InvalidTokenError, match="malformed identity claims"):
        codec.decode(token)


def test_garbage_rejected(codec: AccessTokenCodec) -> None:
    with pytest.raises(jwt.InvalidTokenError):
        codec.decode("not.a.jwt")


def test_actor_is_frozen() -> None:
    with pytest.raises(ValidationError):
        ACTOR.user_id = uuid4()  # type: ignore[misc]


# ---------------------------------------------------------------- A-U12 --
@pytest.mark.parametrize("env", ["test", "staging", "production"])
def test_missing_jwt_secret_outside_development_fails_boot(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
    with pytest.raises(ValidationError, match="AUTH_JWT_SECRET is required outside development"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_empty_jwt_secret_in_production_fails_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_JWT_SECRET", "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_short_jwt_secret_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", "too-short")
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_development_without_secret_warns_once_and_generates_random(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        first = Settings(_env_file=None)  # type: ignore[call-arg]
    assert [str(w.message) for w in caught if "AUTH_JWT_SECRET" in str(w.message)] == [
        "AUTH_JWT_SECRET not set — using a random per-process key (development only); "
        "all access tokens are invalidated on restart."
    ]
    assert first.auth_jwt_secret is not None
    assert len(first.auth_jwt_secret.get_secret_value()) >= 32
    second = Settings(_env_file=None)  # type: ignore[call-arg]
    assert second.auth_jwt_secret is not None
    assert first.auth_jwt_secret.get_secret_value() != second.auth_jwt_secret.get_secret_value()
    assert first.auth_jwt_secret.get_secret_value() not in repr(first)


def test_auth_defaults_and_cookie_flags(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert settings.auth_access_ttl_seconds == 900
    assert settings.auth_refresh_ttl_seconds == 2_592_000
    assert settings.auth_cookie_secure is False  # forced by the test env
    assert settings.auth_refresh_cookie_name == "refresh"
    assert settings.auth_allowed_origins_list == []

    monkeypatch.delenv("AUTH_COOKIE_SECURE")
    monkeypatch.setenv("APP_ENV", "production")
    prod = Settings(_env_file=None)  # type: ignore[call-arg]
    assert prod.auth_cookie_secure is True
    assert prod.auth_refresh_cookie_name == "__Secure-refresh"  # H5: never __Host- with a Path

    monkeypatch.setenv("APP_ENV", "development")
    dev = Settings(_env_file=None)  # type: ignore[call-arg]
    assert dev.auth_cookie_secure is False


def test_allowed_origins_parsed_and_normalised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ALLOWED_ORIGINS", " https://a.example/ ,, https://b.example ")
    assert Settings(_env_file=None).auth_allowed_origins_list == [  # type: ignore[call-arg]
        "https://a.example",
        "https://b.example",
    ]
