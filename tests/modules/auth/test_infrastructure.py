"""Unit tests — Argon2 hasher, in-memory rate limiter and the auth Postgres repositories
(stub session; the integration suite runs the same code on PostgreSQL)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from src.modules.auth.domain.hashing import PasswordHasher
from src.modules.auth.domain.models import RefreshSession, User
from src.modules.auth.domain.rate_limit import RateLimiter
from src.modules.auth.domain.repository import RefreshSessionRepository, UserRepository
from src.modules.auth.exceptions import EmailTakenError
from src.modules.auth.infrastructure.argon2_hasher import DUMMY_HASH, Argon2PasswordHasher
from src.modules.auth.infrastructure.orm import RefreshSessionRow, UserRow
from src.modules.auth.infrastructure.postgres_repository import (
    PostgresRefreshSessionRepository,
    PostgresUserRepository,
)
from src.modules.auth.infrastructure.rate_limit import InMemoryRateLimiter

from tests.conftest import OWNER_ID, OWNER_USER_ID
from tests.fakes import (
    FakePasswordHasher,
    FakeRateLimiter,
    FakeRefreshSessionRepository,
    FakeStore,
    FakeUserRepository,
    StubResult,
    StubSession,
    compiled,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------- hasher --
def test_argon2_hasher_roundtrip_and_protocol() -> None:
    hasher = Argon2PasswordHasher()
    assert isinstance(hasher, PasswordHasher)
    assert isinstance(FakePasswordHasher(), PasswordHasher)
    digest = hasher.hash("correct-horse-battery")
    assert digest.startswith("$argon2id$")
    assert hasher.verify(digest, "correct-horse-battery") is True
    assert hasher.verify(digest, "wrong-horse-battery") is False
    assert hasher.verify("not-a-hash", "anything") is False
    assert hasher.needs_rehash(digest) is False
    assert hasher.needs_rehash("garbage") is True
    assert DUMMY_HASH.startswith("$argon2id$")
    assert hasher.verify(DUMMY_HASH, "dummy-password-never-matches-\x00") is True


def test_argon2_hasher_flags_weaker_parameters_for_rehash() -> None:
    from argon2 import PasswordHasher as Raw

    weak = Raw(time_cost=1, memory_cost=8, parallelism=1).hash("pw-long-enough")
    assert Argon2PasswordHasher().needs_rehash(weak) is True


# ----------------------------------------------------------- rate limiter --
def test_in_memory_rate_limiter_sliding_window() -> None:
    clock = [1000.0]
    limiter = InMemoryRateLimiter(clock=lambda: clock[0])
    assert isinstance(limiter, RateLimiter)
    assert isinstance(FakeRateLimiter(), RateLimiter)
    for _ in range(10):
        assert limiter.hit("ip", "1.2.3.4", 10, 60) is None
    retry = limiter.hit("ip", "1.2.3.4", 10, 60)
    assert retry is not None and 1 <= retry <= 61
    # Another key is independent.
    assert limiter.hit("ip", "5.6.7.8", 10, 60) is None
    # After the window slides past the oldest hit, the budget frees up.
    clock[0] += 61
    assert limiter.hit("ip", "1.2.3.4", 10, 60) is None


def test_in_memory_rate_limiter_failure_block_and_reset() -> None:
    clock = [0.0]
    limiter = InMemoryRateLimiter(clock=lambda: clock[0])
    assert limiter.blocked_for("login", "a@x") is None
    for _ in range(4):
        limiter.record_failure("login", "a@x", 5, 300)
    assert limiter.blocked_for("login", "a@x") is None
    limiter.record_failure("login", "a@x", 5, 300)
    blocked = limiter.blocked_for("login", "a@x")
    assert blocked is not None and 1 <= blocked <= 301
    assert limiter.blocked_for("login", "b@x") is None
    clock[0] += 150
    remaining = limiter.blocked_for("login", "a@x")
    assert remaining is not None and remaining <= 151
    clock[0] += 200
    assert limiter.blocked_for("login", "a@x") is None  # expired block is purged
    limiter.record_failure("login", "a@x", 1, 300)
    assert limiter.blocked_for("login", "a@x") is not None
    limiter.reset_failures("login", "a@x")
    assert limiter.blocked_for("login", "a@x") is None


# ----------------------------------------------------------- repositories --
def _user_row(**overrides: object) -> UserRow:
    values: dict[str, object] = {
        "id": OWNER_USER_ID,
        "tenant_id": OWNER_ID,
        "email": "Nile-Owner@example.com",
        "password_hash": "$argon2id$fake",
        "is_active": True,
        "created_at": NOW,
        "updated_at": NOW,
        "last_login_at": None,
    }
    values.update(overrides)
    return UserRow(**values)


def _session_row(**overrides: object) -> RefreshSessionRow:
    values: dict[str, object] = {
        "id": uuid4(),
        "user_id": OWNER_USER_ID,
        "token_hash": "a" * 64,
        "expires_at": NOW,
        "revoked_at": None,
        "replaced_by": None,
        "user_agent": "ua",
        "ip": "10.0.0.1",
        "created_at": NOW,
    }
    values.update(overrides)
    return RefreshSessionRow(**values)


def _patch_add_defaults(session: StubSession) -> None:
    original_add = session.add

    def _add(row: object) -> None:
        row.created_at = NOW  # type: ignore[attr-defined]
        if hasattr(row, "updated_at"):
            row.updated_at = NOW  # type: ignore[attr-defined]
        original_add(row)

    session.add = _add  # type: ignore[method-assign]


def test_protocol_conformance() -> None:
    store = FakeStore()
    assert isinstance(FakeUserRepository(store), UserRepository)
    assert isinstance(FakeRefreshSessionRepository(store), RefreshSessionRepository)
    stub = StubSession()
    assert isinstance(PostgresUserRepository(stub), UserRepository)  # type: ignore[arg-type]
    assert isinstance(PostgresRefreshSessionRepository(stub), RefreshSessionRepository)  # type: ignore[arg-type]


async def test_user_repository_add_get_lookup_and_updates() -> None:
    session = StubSession(
        [
            StubResult(scalar=OWNER_USER_ID),  # add ⇒ RETURNING id
            StubResult(scalar=None),  # add (conflict on lower(email) / tenant_id)
            StubResult(scalar=_user_row()),  # get_by_email
            StubResult(scalar=None),  # get_by_email (missing)
        ]
    )
    repo = PostgresUserRepository(session)  # type: ignore[arg-type]

    session.identity[(UserRow, OWNER_USER_ID)] = _user_row()
    stored = await repo.add(
        User(id=OWNER_USER_ID, tenant_id=OWNER_ID, email="x@y.z", password_hash="$argon2id$h")
    )
    assert stored.id == OWNER_USER_ID and stored.is_active is True
    sql = compiled(session.executed[0][0])
    assert sql.startswith("INSERT INTO users") and "ON CONFLICT DO NOTHING" in sql  # H4
    assert session.flush_count == 0 and session.added == []
    with pytest.raises(EmailTakenError):
        await repo.add(User(tenant_id=OWNER_ID, email="X@y.z", password_hash="$argon2id$h"))

    del session.identity[(UserRow, OWNER_USER_ID)]
    assert await repo.get(OWNER_USER_ID) is None
    session.identity[(UserRow, OWNER_USER_ID)] = _user_row()
    got = await repo.get(OWNER_USER_ID)
    assert got is not None and got.email == "Nile-Owner@example.com"

    found = await repo.get_by_email("  NILE-owner@EXAMPLE.com ")
    assert found is not None and found.id == OWNER_USER_ID
    sql = compiled(session.executed[2][0])
    assert "lower(users.email)" in sql
    assert await repo.get_by_email("missing@example.com") is None

    await repo.update_password_hash(OWNER_USER_ID, "$argon2id$new")
    sql = compiled(session.executed[-1][0])
    assert sql.startswith("UPDATE users SET") and "password_hash=" in sql
    await repo.touch_last_login(OWNER_USER_ID, NOW)
    sql = compiled(session.executed[-1][0])
    assert sql.startswith("UPDATE users SET") and "last_login_at=" in sql
    assert "updated_at=now()" in sql  # ORM onupdate keeps the audit column fresh


async def test_refresh_session_repository_statements_and_mapping() -> None:
    sid = uuid4()
    session = StubSession(
        [
            StubResult(scalar=sid),  # is_active ⇒ True
            StubResult(scalar=None),  # is_active ⇒ False
            StubResult(rowcount=1),  # revoke (won)
            StubResult(rowcount=0),  # revoke (lost / already revoked)
            StubResult(rowcount=1),  # link_replacement
            StubResult(rowcount=3),  # revoke_all_for_user
        ]
    )
    _patch_add_defaults(session)
    repo = PostgresRefreshSessionRepository(session)  # type: ignore[arg-type]

    stored = await repo.add(
        RefreshSession(
            id=sid, user_id=OWNER_USER_ID, token_hash="b" * 64, expires_at=NOW, ip="10.0.0.9"
        )
    )
    assert stored.id == sid and stored.ip == "10.0.0.9" and stored.user_agent is None

    assert await repo.get(sid) is None
    session.identity[(RefreshSessionRow, sid)] = _session_row(id=sid)
    got = await repo.get(sid)
    assert got is not None and got.ip == "10.0.0.1" and got.user_agent == "ua"
    session.identity[(RefreshSessionRow, sid)] = _session_row(id=sid, ip=None)
    got_no_ip = await repo.get(sid)
    assert got_no_ip is not None and got_no_ip.ip is None

    assert await repo.is_active(sid, NOW) is True
    active_sql = compiled(session.executed[0][0])
    # H6: one query, JOIN users, all three predicates.
    assert "JOIN users" in active_sql
    assert "revoked_at IS NULL" in active_sql
    assert "refresh_sessions.expires_at >" in active_sql
    assert "users.is_active IS true" in active_sql
    assert await repo.is_active(sid, NOW) is False

    replacement = uuid4()
    assert await repo.revoke(sid, NOW, replaced_by=replacement) == 1  # H7: rowcount
    revoke_sql = compiled(session.executed[2][0])
    assert "UPDATE refresh_sessions SET revoked_at" in revoke_sql
    assert "replaced_by" in revoke_sql and "revoked_at IS NULL" in revoke_sql
    assert await repo.revoke(sid, NOW) == 0

    await repo.link_replacement(sid, replacement)
    link_sql = compiled(session.executed[4][0])
    assert link_sql.startswith("UPDATE refresh_sessions SET replaced_by=")

    assert await repo.revoke_all_for_user(OWNER_USER_ID, NOW) == 3
    all_sql = compiled(session.executed[5][0])
    assert "refresh_sessions.user_id" in all_sql and "revoked_at IS NULL" in all_sql


async def test_revoke_all_handles_missing_rowcount() -> None:
    session = StubSession([StubResult(rowcount=None)])
    repo = PostgresRefreshSessionRepository(session)  # type: ignore[arg-type]
    assert await repo.revoke_all_for_user(OWNER_USER_ID, NOW) == 0


# ------------------------------------------------------------ domain model --
def test_refresh_session_helpers() -> None:
    session = RefreshSession(user_id=OWNER_USER_ID, token_hash="c" * 64, expires_at=NOW)
    assert session.is_revoked is False
    assert session.is_expired(NOW) is True
    assert session.is_expired(datetime(2025, 12, 31, tzinfo=UTC)) is False
    assert session.model_copy(update={"revoked_at": NOW}).is_revoked is True


# ============================================================ SPRINT-04.1 H8
def test_h8_u1_rate_limiter_is_bounded_by_max_keys() -> None:
    now = [1000.0]
    limiter = InMemoryRateLimiter(clock=lambda: now[0], max_keys=10_000)
    for i in range(20_000):
        limiter.hit("auth:ip", f"10.0.{i // 256}.{i % 256}", limit=10, window_seconds=60)
        if i % 3 == 0:
            limiter.record_failure("auth:login-failures", f"user{i}@example.com", 5, 300)
    assert limiter.tracked_keys <= 10_000
    # Least-recently-seen keys were evicted first; the newest is still tracked and still
    # subject to its window (a second hit does not reset anything).
    assert limiter.hit("auth:ip", "10.0.78.31", limit=1, window_seconds=60) is not None

    small = InMemoryRateLimiter(clock=lambda: now[0], max_keys=2)
    small.hit("b", "a", 10, 60)
    small.hit("b", "b", 10, 60)
    small.hit("b", "a", 10, 60)  # touch "a" ⇒ "b" is now the oldest
    small.hit("b", "c", 10, 60)  # overflow ⇒ "b" evicted
    assert small.tracked_keys == 2
    assert small.hit("b", "b", limit=1, window_seconds=60) is None  # fresh again


def test_h8_u2_idle_keys_are_swept_after_their_window_and_block_elapse() -> None:
    now = [0.0]
    limiter = InMemoryRateLimiter(clock=lambda: now[0])
    limiter.hit("auth:ip", "1.1.1.1", limit=10, window_seconds=60)
    limiter.record_failure("auth:login-failures", "x@example.com", limit=1, block_seconds=30)
    assert limiter.blocked_for("auth:login-failures", "x@example.com") is not None
    assert limiter.tracked_keys == 2

    limiter.sweep_now()
    assert limiter.tracked_keys == 2  # both still live

    now[0] = 31.0
    limiter.sweep_now()
    assert limiter.tracked_keys == 1  # block elapsed ⇒ failure key gone; hit window still open
    assert limiter.blocked_for("auth:login-failures", "x@example.com") is None

    now[0] = 61.0
    limiter.sweep_now()
    assert limiter.tracked_keys == 0

    # The lazy sweep also runs on its own every SWEEP_EVERY operations.
    from src.modules.auth.infrastructure.rate_limit import SWEEP_EVERY

    now[0] = 100.0
    limiter.hit("auth:ip", "stale", limit=10, window_seconds=1)
    now[0] = 200.0
    for i in range(SWEEP_EVERY):
        limiter.hit("auth:ip", f"fresh-{i}", limit=10, window_seconds=600)
    assert ("auth:ip", "stale") not in limiter._entries  # noqa: SLF001 - white-box check
    # A failure counter alone keeps the key alive (it is reset only on success or block).
    limiter.record_failure("auth:login-failures", "y@example.com", limit=5, block_seconds=1)
    limiter.sweep_now()
    assert ("auth:login-failures", "y@example.com") in limiter._entries  # noqa: SLF001
    limiter.reset_failures("auth:login-failures", "y@example.com")
    limiter.sweep_now()
    assert ("auth:login-failures", "y@example.com") not in limiter._entries  # noqa: SLF001
