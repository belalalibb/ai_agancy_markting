"""Access-token issuing / verification (A5) — HS256 via PyJWT, no database round-trip.

Claims: ``sub`` (user id), ``tid`` (tenant id), ``sid`` (refresh-session id), ``iat``,
``exp`` = iat + ``AUTH_ACCESS_TTL_SECONDS``, ``iss`` = ``settings.app_name``, ``aud`` =
``"api"``, ``jti`` (random). Decoding verifies signature, ``exp``, ``iss`` and ``aud`` and
pins ``algorithms=["HS256"]`` (``alg=none`` and key-confusion are rejected by PyJWT).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import UUID

import jwt
from pydantic import SecretStr

from src.modules.auth.domain.models import AuthenticatedActor

ALGORITHM = "HS256"
AUDIENCE = "api"


class AccessTokenCodec:
    def __init__(self, secret: SecretStr, issuer: str, ttl_seconds: int) -> None:
        self._secret = secret.get_secret_value()
        self._issuer = issuer
        self._ttl = ttl_seconds

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def issue(self, actor: AuthenticatedActor, now: datetime | None = None) -> str:
        iat = int((now or datetime.now(tz=UTC)).timestamp())
        claims = {
            "sub": str(actor.user_id),
            "tid": str(actor.tenant_id),
            "sid": str(actor.sid),
            "iat": iat,
            "exp": iat + self._ttl,
            "iss": self._issuer,
            "aud": AUDIENCE,
            "jti": secrets.token_urlsafe(16),
        }
        return jwt.encode(claims, self._secret, algorithm=ALGORITHM)

    def decode(self, token: str) -> AuthenticatedActor:
        """Return the verified actor or raise ``jwt.InvalidTokenError`` (any subclass)."""
        claims = jwt.decode(
            token,
            self._secret,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=self._issuer,
            options={"require": ["sub", "tid", "sid", "iat", "exp", "iss", "aud"]},
        )
        try:
            return AuthenticatedActor(
                user_id=UUID(claims["sub"]), tenant_id=UUID(claims["tid"]), sid=UUID(claims["sid"])
            )
        except (ValueError, TypeError) as exc:
            msg = "malformed identity claims"
            raise jwt.InvalidTokenError(msg) from exc
