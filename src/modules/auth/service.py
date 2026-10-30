"""Application service for auth (SPRINT-04 A4–A7).

Refresh cookie value = ``"<session_id>.<secret>"``; only ``sha256(secret)`` is persisted.
Everything here is framework-free; HTTP concerns (cookies, headers) live in ``router.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from src.modules.auth.domain.hashing import PasswordHasher
from src.modules.auth.domain.models import AuthenticatedActor, RefreshSession, User
from src.modules.auth.domain.repository import RefreshSessionRepository, UserRepository
from src.modules.auth.exceptions import (
    EmailTakenError,
    InvalidCredentialsError,
    RefreshExpiredError,
    RefreshInvalidError,
    RefreshReuseDetectedError,
    UserInactiveError,
)
from src.modules.auth.jwt import AccessTokenCodec
from src.modules.tenancy.domain.models import Tenant
from src.modules.tenancy.service import TenancyService

REFRESH_SECRET_BYTES = 32


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    expires_in: int
    refresh_cookie_value: str
    user: User


@dataclass(frozen=True, slots=True)
class ClientInfo:
    user_agent: str | None = None
    ip: str | None = None


NO_CLIENT = ClientInfo()


def hash_refresh_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def parse_refresh_cookie(value: str) -> tuple[UUID, str] | None:
    """``"<sid>.<secret>"`` → ``(sid, secret)`` or ``None`` when malformed."""
    sid_text, sep, secret = value.partition(".")
    if not sep or not secret:
        return None
    try:
        return UUID(sid_text), secret
    except ValueError:
        return None


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class AuthService:
    def __init__(
        self,
        users: UserRepository,
        sessions: RefreshSessionRepository,
        hasher: PasswordHasher,
        codec: AccessTokenCodec,
        tenancy: TenancyService,
        refresh_ttl_seconds: int,
        dummy_hash: str,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._hasher = hasher
        self._codec = codec
        self._tenancy = tenancy
        self._refresh_ttl = refresh_ttl_seconds
        self._dummy_hash = dummy_hash

    # ---------------------------------------------------------------- register --
    async def register(
        self,
        email: str,
        password: str,
        tenant_slug: str,
        tenant_name: str,
        client: ClientInfo = NO_CLIENT,
    ) -> IssuedTokens:
        """Create tenant + user in the caller's transaction (A2). Email is case-insensitive."""
        email = email.strip()
        if await self._users.get_by_email(email) is not None:
            raise EmailTakenError("email is already registered")
        # Raises TenantSlugTakenError *before* any user row exists ⇒ no orphan user (A-U1).
        tenant = await self._tenancy.create_tenant(tenant_slug, tenant_name)
        user = await self._users.add(
            User(tenant_id=tenant.id, email=email, password_hash=self._hasher.hash(password))
        )
        return await self._issue(user, client)

    # ------------------------------------------------------------------- login --
    async def login(self, email: str, password: str, client: ClientInfo = NO_CLIENT) -> User:
        """Verify credentials with constant work (A4); returns the user, no tokens yet."""
        user = await self._users.get_by_email(email.strip())
        candidate_hash = user.password_hash if user is not None else self._dummy_hash
        verified = self._hasher.verify(candidate_hash, password)
        if user is None or not verified:
            raise InvalidCredentialsError("invalid email or password")
        if not user.is_active:
            raise UserInactiveError("user account is inactive")
        if self._hasher.needs_rehash(user.password_hash):
            await self._users.update_password_hash(user.id, self._hasher.hash(password))
        await self._users.touch_last_login(user.id, _utcnow())
        return user

    async def issue_for(self, user: User, client: ClientInfo = NO_CLIENT) -> IssuedTokens:
        return await self._issue(user, client)

    # ----------------------------------------------------------------- refresh --
    async def refresh(self, cookie_value: str, client: ClientInfo = NO_CLIENT) -> IssuedTokens:
        session = await self._load_session(cookie_value)
        now = _utcnow()
        if session.is_revoked:
            # Token reuse after rotation ⇒ assume theft ⇒ kill every session of the user (A6).
            await self._sessions.revoke_all_for_user(session.user_id, now)
            raise RefreshReuseDetectedError("refresh token reuse detected; all sessions revoked")
        if session.is_expired(now):
            raise RefreshExpiredError("refresh token expired")
        user = await self._users.get(session.user_id)
        if user is None:
            raise RefreshInvalidError("refresh token invalid")
        if not user.is_active:
            raise UserInactiveError("user account is inactive")
        issued = await self._issue(user, client)
        new_sid = UUID(issued.refresh_cookie_value.partition(".")[0])
        await self._sessions.revoke(session.id, now, replaced_by=new_sid)
        return issued

    # ------------------------------------------------------------------ logout --
    async def logout(self, cookie_value: str | None, actor_sid: UUID | None = None) -> None:
        """Revoke the session named by the cookie (or the Bearer ``sid``). Idempotent."""
        now = _utcnow()
        if cookie_value is not None:
            parsed = parse_refresh_cookie(cookie_value)
            if parsed is not None:
                session = await self._sessions.get(parsed[0])
                if session is not None and _hash_matches(session, parsed[1]):
                    await self._sessions.revoke(session.id, now)
                    return
        if actor_sid is not None:
            await self._sessions.revoke(actor_sid, now)

    async def logout_all(self, actor: AuthenticatedActor) -> int:
        return await self._sessions.revoke_all_for_user(actor.user_id, _utcnow())

    # ---------------------------------------------------------------------- me --
    async def me(self, actor: AuthenticatedActor) -> tuple[User, Tenant]:
        user = await self._users.get(actor.user_id)
        if user is None or not user.is_active:
            raise UserInactiveError("user account is inactive")
        tenant = await self._tenancy.get_own_tenant(actor.tenant_id, user.tenant_id)
        return user, tenant

    # ----------------------------------------------------------------- helpers --
    async def _load_session(self, cookie_value: str) -> RefreshSession:
        parsed = parse_refresh_cookie(cookie_value)
        if parsed is None:
            raise RefreshInvalidError("refresh token malformed")
        session = await self._sessions.get(parsed[0])
        if session is None or not _hash_matches(session, parsed[1]):
            raise RefreshInvalidError("refresh token invalid")
        return session

    async def _issue(self, user: User, client: ClientInfo) -> IssuedTokens:
        secret = secrets.token_urlsafe(REFRESH_SECRET_BYTES)
        now = _utcnow()
        session = await self._sessions.add(
            RefreshSession(
                user_id=user.id,
                token_hash=hash_refresh_secret(secret),
                expires_at=now + timedelta(seconds=self._refresh_ttl),
                user_agent=client.user_agent[:255] if client.user_agent else None,
                ip=client.ip,
            )
        )
        actor = AuthenticatedActor(user_id=user.id, tenant_id=user.tenant_id, sid=session.id)
        return IssuedTokens(
            access_token=self._codec.issue(actor, now=now),
            expires_in=self._codec.ttl_seconds,
            refresh_cookie_value=f"{session.id}.{secret}",
            user=user,
        )


def _hash_matches(session: RefreshSession, secret: str) -> bool:
    return hmac.compare_digest(session.token_hash, hash_refresh_secret(secret))
