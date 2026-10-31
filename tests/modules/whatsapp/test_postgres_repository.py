"""Unit tests — ``PostgresWhatsAppRepository`` / ``PgIndependentWrites`` / ``PgBudgetUsageReader``
against the scripted :class:`StubSession` (same pattern as ``social_connect`` / ``tenancy``).

SPRINT-10 Part A: the CI ``unit`` job runs without PostgreSQL, so statement composition
(brand scoping, lock modes, ``ON CONFLICT`` targets, keyset predicates, ``UNION ALL`` budget
sources) and row→domain mapping are asserted here; the integration suite runs the same
adapters on PostgreSQL 16/17.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from src.core import database
from src.core.database import independent_session
from src.modules.ai_gateway.infrastructure.pg_budget import PgBudgetUsageReader
from src.modules.social_connect.domain.models import AccountStatus as ConnectionStatus
from src.modules.social_connect.infrastructure.status_marker import PostgresStatusMarker
from src.modules.whatsapp.domain.models import (
    AccountStatus,
    Conversation,
    ConversationStatus,
    DeliveryStatus,
    Direction,
    DraftStatus,
    LearningSignal,
    Message,
    MessageKind,
    RejectionReason,
    ReplyDraft,
    Signal,
    WhatsAppAccount,
)
from src.modules.whatsapp.infrastructure import postgres_repository as pg_module
from src.modules.whatsapp.infrastructure.orm import (
    ConversationRow,
    LearningSignalRow,
    MessageRow,
    ReplyDraftRow,
    WhatsAppAccountRow,
)
from src.modules.whatsapp.infrastructure.postgres_repository import (
    PgIndependentWrites,
    PostgresWhatsAppRepository,
)

from tests.conftest import BRAND_ID, OWNER_USER_ID
from tests.fakes import StubResult, StubSession, compiled

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
ACCOUNT_ID = UUID("00000000-0000-4000-8000-0000000000a1")
CONV_ID = UUID("00000000-0000-4000-8000-0000000000c1")


# ----------------------------------------------------------------- builders --
def _account(status: AccountStatus = AccountStatus.ACTIVE) -> WhatsAppAccount:
    return WhatsAppAccount(
        id=ACCOUNT_ID,
        brand_id=BRAND_ID,
        phone_number_id="10001",
        waba_id="20001",
        display_phone="+20 100 000 0000",
        encrypted_system_token="gAAAAA-token",
        encrypted_app_secret="gAAAAA-secret",
        verify_token_hash="$argon2$hash",
        status=status,
        created_by_user_id=OWNER_USER_ID,
        created_at=NOW,
        updated_at=NOW,
    )


def _account_row(account: WhatsAppAccount) -> WhatsAppAccountRow:
    return WhatsAppAccountRow(
        id=account.id,
        brand_id=account.brand_id,
        phone_number_id=account.phone_number_id,
        waba_id=account.waba_id,
        display_phone=account.display_phone,
        encrypted_system_token=account.encrypted_system_token,
        encrypted_app_secret=account.encrypted_app_secret,
        verify_token_hash=account.verify_token_hash,
        status=account.status.value,
        created_by_user_id=account.created_by_user_id,
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


def _conversation_row(**overrides: Any) -> ConversationRow:
    values: dict[str, Any] = {
        "id": CONV_ID,
        "brand_id": BRAND_ID,
        "account_id": ACCOUNT_ID,
        "wa_id": "201000000000",
        "profile_name": "Mona",
        "last_inbound_at": NOW,
        "last_outbound_at": None,
        "window_expires_at": NOW + timedelta(hours=24),
        "unread_count": 2,
        "status": "open",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return ConversationRow(**values)


def _message(**overrides: Any) -> Message:
    values: dict[str, Any] = {
        "conversation_id": CONV_ID,
        "brand_id": BRAND_ID,
        "direction": Direction.OUTBOUND,
        "wamid": "wamid.OUT1",
        "client_message_id": uuid4(),
        "kind": MessageKind.TEXT,
        "body": "hello",
        "delivery_status": DeliveryStatus.QUEUED,
        "sent_by_user_id": OWNER_USER_ID,
        "created_at": NOW,
    }
    values.update(overrides)
    return Message(**values)


def _message_row(message: Message) -> MessageRow:
    return MessageRow(**PostgresWhatsAppRepository._row_values(message))


def _draft(**overrides: Any) -> ReplyDraft:
    values: dict[str, Any] = {
        "conversation_id": CONV_ID,
        "brand_id": BRAND_ID,
        "in_reply_to_message_id": uuid4(),
        "suggested_body": "Thanks for reaching out!",
        "provenance": {"kind": "ai_generated", "prompt_tokens": 10, "completion_tokens": 5},
        "created_at": NOW,
    }
    values.update(overrides)
    return ReplyDraft(**values)


def _draft_row(draft: ReplyDraft) -> ReplyDraftRow:
    return ReplyDraftRow(
        id=draft.id,
        conversation_id=draft.conversation_id,
        brand_id=draft.brand_id,
        in_reply_to_message_id=draft.in_reply_to_message_id,
        suggested_body=draft.suggested_body,
        final_body=draft.final_body,
        status=draft.status.value,
        provenance=dict(draft.provenance),
        decided_by_user_id=draft.decided_by_user_id,
        decided_at=draft.decided_at,
        rejection_reason=draft.rejection_reason.value if draft.rejection_reason else None,
        created_at=draft.created_at,
    )


def _repo(results: list[StubResult] | None = None) -> tuple[PostgresWhatsAppRepository, Any]:
    session = StubSession(results)
    return PostgresWhatsAppRepository(session), session  # type: ignore[arg-type]


# ----------------------------------------------------------------- accounts --
async def test_account_create_update_and_reads() -> None:
    account = _account()
    row = _account_row(account)
    repo, session = _repo(
        [
            StubResult(),  # update_account
            StubResult(scalar=row),  # get_account
            StubResult(scalar=None),  # get_account (missing)
            StubResult(scalar=row),  # get_account_by_phone_number_id
            StubResult(scalars=[row]),  # list_active_accounts
            StubResult(),  # set_account_status
        ]
    )
    session.identity[(WhatsAppAccountRow, ACCOUNT_ID)] = row

    assert await repo.create_account(account) is account
    assert isinstance(session.added[0], WhatsAppAccountRow)
    assert session.added[0].encrypted_system_token == "gAAAAA-token"
    assert session.flush_count == 1

    await repo.update_account(account.model_copy(update={"status": AccountStatus.ACTIVE}))
    update_sql = compiled(session.executed[0][0])
    assert update_sql.startswith("UPDATE whatsapp_accounts")
    assert "whatsapp_accounts.id =" in update_sql
    for column in ("phone_number_id", "encrypted_system_token", "encrypted_app_secret", "status"):
        assert f"{column}=" in update_sql, column
    assert "created_by_user_id" not in update_sql  # A-2: identity of the first connect stays

    stored = await repo.get_account(BRAND_ID)
    assert stored == account
    assert "whatsapp_accounts.brand_id =" in compiled(session.executed[1][0])
    assert await repo.get_account(BRAND_ID) is None
    assert await repo.get_account_by_id(ACCOUNT_ID) == account
    assert await repo.get_account_by_id(uuid4()) is None
    assert await repo.get_account_by_phone_number_id("10001") == account
    assert "whatsapp_accounts.phone_number_id =" in compiled(session.executed[3][0])
    assert [a.id for a in await repo.list_active_accounts()] == [ACCOUNT_ID]
    assert "whatsapp_accounts.status =" in compiled(session.executed[4][0])

    await repo.set_account_status(ACCOUNT_ID, AccountStatus.DISABLED, now=NOW)
    status_sql = compiled(session.executed[5][0])
    assert status_sql.startswith("UPDATE whatsapp_accounts")
    assert "status=" in status_sql and "updated_at=" in status_sql


# ------------------------------------------------------------ conversations --
async def test_conversation_reads_use_no_key_update_lock_and_brand_scope() -> None:
    row = _conversation_row()
    repo, session = _repo(
        [StubResult(scalar=row), StubResult(scalar=row), StubResult(scalar=None)]
    )

    conv = await repo.get_conversation(BRAND_ID, CONV_ID)
    assert isinstance(conv, Conversation)
    assert conv.profile_name == "Mona"
    assert conv.unread_count == 2
    plain_sql = compiled(session.executed[0][0])
    assert "wa_conversations.brand_id =" in plain_sql
    assert "FOR " not in plain_sql

    locked = await repo.get_conversation_for_update(BRAND_ID, CONV_ID)
    assert locked == conv
    lock_sql = compiled(session.executed[1][0])
    # A-1: FOR NO KEY UPDATE — never FOR UPDATE (it would block the independent insert).
    assert lock_sql.rstrip().endswith("FOR NO KEY UPDATE")
    assert "wa_conversations.brand_id =" in lock_sql
    assert await repo.get_conversation_for_update(BRAND_ID, CONV_ID) is None


async def test_upsert_conversation_inbound_targets_account_wa_id_constraint() -> None:
    row = _conversation_row(unread_count=3)
    repo, session = _repo([StubResult(scalar=row)])

    conv = await repo.upsert_conversation_inbound(
        brand_id=BRAND_ID,
        account_id=ACCOUNT_ID,
        wa_id="201000000000",
        profile_name="Mona",
        at=NOW,
        window_expires_at=NOW + timedelta(hours=24),
    )

    sql = compiled(session.executed[0][0])
    assert sql.startswith("INSERT INTO wa_conversations")
    assert "ON CONFLICT ON CONSTRAINT uq_wa_conversations_account_wa_id" in sql
    set_clause = sql.split("DO UPDATE SET", 1)[1]
    assert "greatest(wa_conversations.last_inbound_at" in set_clause
    assert "greatest(wa_conversations.window_expires_at" in set_clause
    assert "wa_conversations.unread_count + " in set_clause
    assert "coalesce(excluded.profile_name, wa_conversations.profile_name)" in set_clause
    assert "RETURNING wa_conversations.id" in sql
    assert conv.unread_count == 3


async def test_list_conversations_keyset_and_status_filter() -> None:
    row = _conversation_row()
    repo, session = _repo(
        [StubResult(rows=[(row, "last body", True)]), StubResult(rows=[(row, None, False)])]
    )

    page = await repo.list_conversations(
        BRAND_ID, status=ConversationStatus.OPEN, limit=10, cursor=None
    )
    assert page == [(pg_module._conversation(row), "last body", True)]
    sql = compiled(session.executed[0][0])
    assert "wa_conversations.brand_id =" in sql
    assert "wa_conversations.status =" in sql
    assert "EXISTS (SELECT wa_reply_drafts.id" in sql
    assert (
        "ORDER BY wa_conversations.last_inbound_at DESC NULLS LAST, wa_conversations.id DESC"
        in sql
    )
    assert "LIMIT" in sql

    page2 = await repo.list_conversations(BRAND_ID, status=None, limit=10, cursor=(NOW, CONV_ID))
    assert page2[0][1] is None and page2[0][2] is False
    sql2 = compiled(session.executed[1][0])
    assert "wa_conversations.status =" not in sql2
    assert "wa_conversations.last_inbound_at < " in sql2
    assert "wa_conversations.id < " in sql2


async def test_conversation_updates_compose_expected_statements() -> None:
    repo, session = _repo()

    await repo.mark_conversation_read(CONV_ID, now=NOW)
    await repo.mark_conversation_read_delta(CONV_ID, -1)
    await repo.set_conversation_status(CONV_ID, ConversationStatus.ARCHIVED, now=NOW)
    await repo.touch_outbound(CONV_ID, at=NOW)

    read_sql, delta_sql, status_sql, touch_sql = (compiled(s) for s, _ in session.executed)
    assert read_sql.startswith("UPDATE wa_conversations") and "unread_count=" in read_sql
    assert "greatest(wa_conversations.unread_count + " in delta_sql
    assert "status=" in status_sql
    assert "last_outbound_at=" in touch_sql and "updated_at=" in touch_sql


# ----------------------------------------------------------------- messages --
async def test_insert_inbound_if_new_is_idempotent_on_wamid() -> None:
    message = _message(direction=Direction.INBOUND, wamid="wamid.IN1", delivery_status="received")
    repo, session = _repo([StubResult(scalar=message.id), StubResult(scalar=None)])

    assert await repo.insert_inbound_if_new(message) is True
    assert await repo.insert_inbound_if_new(message) is False
    sql = compiled(session.executed[0][0])
    assert sql.startswith("INSERT INTO wa_messages")
    assert "ON CONFLICT (wamid) WHERE wamid IS NOT NULL DO NOTHING" in sql
    assert "RETURNING wa_messages.id" in sql


async def test_outbound_insert_and_message_reads() -> None:
    message = _message()
    row = _message_row(message)
    repo, session = _repo(
        [
            StubResult(scalar=row),  # get_message
            StubResult(scalar=None),  # get_message missing
            StubResult(scalar=row),  # get_message_by_client_id
            StubResult(scalar="wamid.IN9"),  # last_inbound_wamid
            StubResult(scalar=row),  # last_message
            StubResult(scalar=None),  # last_message none
            StubResult(scalars=[row]),  # list_messages (no cursor)
            StubResult(scalars=[row]),  # list_messages (cursor)
            StubResult(scalars=[row]),  # recent_messages
        ]
    )

    assert await repo.insert_outbound(message) is message
    assert isinstance(session.added[0], MessageRow)
    assert session.added[0].delivery_status == "queued"

    assert await repo.get_message(BRAND_ID, message.id) == message
    assert "wa_messages.brand_id =" in compiled(session.executed[0][0])
    assert await repo.get_message(BRAND_ID, message.id) is None
    assert message.client_message_id is not None
    assert await repo.get_message_by_client_id(BRAND_ID, message.client_message_id) == message
    assert "wa_messages.client_message_id =" in compiled(session.executed[2][0])

    assert await repo.last_inbound_wamid(CONV_ID) == "wamid.IN9"
    inbound_sql = compiled(session.executed[3][0])
    assert "wa_messages.direction =" in inbound_sql and "wamid IS NOT NULL" in inbound_sql
    assert "ORDER BY wa_messages.created_at DESC, wa_messages.id DESC" in inbound_sql

    assert await repo.last_message(CONV_ID) == message
    assert await repo.last_message(CONV_ID) is None

    assert await repo.list_messages(CONV_ID, limit=5, cursor=None) == [message]
    first_sql = compiled(session.executed[6][0])
    assert "ORDER BY wa_messages.created_at ASC, wa_messages.id ASC" in first_sql
    assert "wa_messages.created_at > " not in first_sql
    assert await repo.list_messages(CONV_ID, limit=5, cursor=(NOW, message.id)) == [message]
    cursor_sql = compiled(session.executed[7][0])
    assert "wa_messages.created_at > " in cursor_sql and "wa_messages.id > " in cursor_sql
    assert await repo.recent_messages(CONV_ID, limit=3) == [message]
    assert "DESC" in compiled(session.executed[8][0])


async def test_set_delivery_status_only_sets_given_columns() -> None:
    repo, session = _repo()
    message_id = uuid4()

    await repo.set_delivery_status(message_id, DeliveryStatus.SENT, wamid="wamid.OUT1")
    await repo.set_delivery_status(message_id, DeliveryStatus.FAILED, error_code=131026)
    await repo.set_delivery_status(message_id, DeliveryStatus.AMBIGUOUS)

    sent_sql, failed_sql, plain_sql = (compiled(s) for s, _ in session.executed)
    assert "wamid=" in sent_sql and "error_code" not in sent_sql
    assert "error_code=" in failed_sql and "wamid" not in failed_sql
    assert "wamid" not in plain_sql and "error_code" not in plain_sql
    assert "wa_messages.id =" in plain_sql


async def test_update_status_by_wamid_locks_row_and_applies_monotonic_rule() -> None:
    queued = _message_row(_message(delivery_status=DeliveryStatus.QUEUED))
    read = _message_row(_message(delivery_status=DeliveryStatus.READ))
    repo, session = _repo(
        [StubResult(scalar=queued), StubResult(scalar=read), StubResult(scalar=None)]
    )

    assert (
        await repo.update_status_by_wamid(
            "wamid.OUT1", DeliveryStatus.FAILED, brand_id=BRAND_ID, error_code=131047
        )
        is True
    )
    assert queued.delivery_status == "failed" and queued.error_code == 131047
    assert session.flush_count == 1
    sql = compiled(session.executed[0][0])
    assert sql.rstrip().endswith("FOR UPDATE")
    assert "wa_messages.wamid =" in sql
    assert "wa_messages.direction =" in sql
    assert "wa_messages.brand_id =" in sql  # A-3: tenant scope on the externally-triggered write

    # read ⇒ delivered is a regression ⇒ ignored, row untouched.
    assert (
        await repo.update_status_by_wamid(
            "wamid.OUT1", DeliveryStatus.DELIVERED, brand_id=BRAND_ID, error_code=None
        )
        is False
    )
    assert read.delivery_status == "read"
    # unknown wamid / other brand ⇒ False, nothing flushed.
    assert (
        await repo.update_status_by_wamid(
            "wamid.NOPE", DeliveryStatus.SENT, brand_id=BRAND_ID, error_code=None
        )
        is False
    )
    assert session.flush_count == 1


# ------------------------------------------------------------- reply drafts --
async def test_draft_create_reads_and_lock_variant() -> None:
    draft = _draft()
    row = _draft_row(draft)
    repo, session = _repo(
        [
            StubResult(scalar=row),  # get_draft
            StubResult(scalar=row),  # get_draft_for_update
            StubResult(scalar=None),  # get_draft_for_update missing
            StubResult(scalar=row),  # pending_draft
            StubResult(scalar=None),  # pending_draft none
            StubResult(scalars=[row]),  # list_drafts
        ]
    )

    assert await repo.create_draft(draft) is draft
    added = session.added[0]
    assert isinstance(added, ReplyDraftRow)
    assert added.status == "suggested" and added.rejection_reason is None

    assert await repo.get_draft(BRAND_ID, draft.id) == draft
    plain_sql = compiled(session.executed[0][0])
    assert "wa_reply_drafts.brand_id =" in plain_sql and "FOR UPDATE" not in plain_sql
    assert await repo.get_draft_for_update(BRAND_ID, draft.id) == draft
    assert compiled(session.executed[1][0]).rstrip().endswith("FOR UPDATE")
    assert await repo.get_draft_for_update(BRAND_ID, draft.id) is None

    assert await repo.pending_draft(CONV_ID) == draft
    pending_sql = compiled(session.executed[3][0])
    assert "wa_reply_drafts.status =" in pending_sql and "LIMIT" in pending_sql
    assert await repo.pending_draft(CONV_ID) is None
    assert await repo.list_drafts(CONV_ID) == [draft]


async def test_decide_and_expire_drafts() -> None:
    expired_row = _draft_row(_draft(status=DraftStatus.EXPIRED, decided_at=NOW))
    repo, session = _repo([StubResult(), StubResult(), StubResult(scalars=[expired_row])])
    draft_id = uuid4()

    await repo.decide_draft(
        draft_id,
        status=DraftStatus.APPROVED,
        final_body="edited",
        decided_by_user_id=OWNER_USER_ID,
        decided_at=NOW,
    )
    await repo.decide_draft(
        draft_id,
        status=DraftStatus.REJECTED,
        final_body=None,
        decided_by_user_id=OWNER_USER_ID,
        decided_at=NOW,
        rejection_reason=RejectionReason.OFF_TONE,
    )
    approve_sql, reject_sql = (compiled(s) for s, _ in session.executed[:2])
    assert approve_sql.startswith("UPDATE wa_reply_drafts")
    assert "final_body=" in approve_sql and "decided_by_user_id=" in approve_sql
    assert "rejection_reason=" in reject_sql

    expired = await repo.expire_pending_drafts(CONV_ID, now=NOW)
    assert [d.status for d in expired] == [DraftStatus.EXPIRED]
    expire_sql = compiled(session.executed[2][0])
    assert "wa_reply_drafts.status =" in expire_sql
    assert "RETURNING" in expire_sql


# ------------------------------------------------------------------ signals --
async def test_learning_signals_round_trip_without_text() -> None:
    signal = LearningSignal(
        brand_id=BRAND_ID,
        reply_draft_id=uuid4(),
        signal=Signal.APPROVED_AFTER_EDIT,
        edit_distance_ratio=0.25,
        rejection_reason=None,
        prompt_version="wa_v1",
        model="fake-1",
        credential_mode="platform",
        features={"language": "ar"},
        created_at=NOW,
    )
    stored_row = LearningSignalRow(
        id=signal.id,
        brand_id=BRAND_ID,
        reply_draft_id=signal.reply_draft_id,
        signal="rejected",
        edit_distance_ratio=None,
        rejection_reason="off_tone",
        prompt_version="wa_v1",
        model="fake-1",
        credential_mode="platform",
        features=None,
        created_at=NOW,
    )
    repo, session = _repo([StubResult(scalars=[stored_row])])

    assert await repo.add_signal(signal) is signal
    added = session.added[0]
    assert isinstance(added, LearningSignalRow)
    assert added.edit_distance_ratio == 0.25 and added.rejection_reason is None
    assert not hasattr(added, "suggested_body")  # D-069

    rows = await repo.learning_signals(BRAND_ID)
    assert rows == [
        {
            "signal": "rejected",
            "edit_distance_ratio": None,
            "rejection_reason": "off_tone",
            "features": {},
        }
    ]
    assert "wa_learning_signals.brand_id =" in compiled(session.executed[0][0])


# ---------------------------------------------- independent transaction (D-072) --
class _TxnSession(StubSession):
    """A stub that also behaves like ``async_sessionmaker()()`` + ``session.begin()``."""

    def __init__(self, results: list[StubResult] | None = None) -> None:
        super().__init__(results)
        self.events: list[str] = []
        self._in_begin = False

    async def __aenter__(self) -> _TxnSession:
        if self._in_begin:
            self.events.append("begin")
        else:
            self.events.append("open")
        return self

    async def __aexit__(self, exc_type: object, *_: object) -> None:
        if self._in_begin:
            self._in_begin = False
            self.events.append("rollback" if exc_type else "commit")
        else:
            self.events.append("close")

    def begin(self) -> _TxnSession:
        self._in_begin = True
        return self


def _factory(session: _TxnSession) -> Callable[[], _TxnSession]:
    return lambda: session


async def test_independent_session_commits_on_exit_and_rolls_back_on_error() -> None:
    ok = _TxnSession()
    async with independent_session(_factory(ok)) as session:  # type: ignore[arg-type]
        assert session is ok
    assert ok.events == ["open", "begin", "commit", "close"]

    failing = _TxnSession()
    with pytest.raises(RuntimeError, match="boom"):
        async with independent_session(_factory(failing)):  # type: ignore[arg-type]
            raise RuntimeError("boom")
    assert failing.events == ["open", "begin", "rollback", "close"]


async def test_independent_session_defaults_to_the_app_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _TxnSession()
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()
    monkeypatch.setattr(database, "get_session_factory", lambda: _factory(session))
    async with independent_session() as got:
        assert got is session
    assert session.events[-1] == "close"


async def test_pg_independent_writes_runs_fn_on_a_committed_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _TxnSession()
    seen: list[PostgresWhatsAppRepository] = []

    async def _work(repo: PostgresWhatsAppRepository) -> str:
        seen.append(repo)
        await repo.touch_outbound(CONV_ID, at=NOW)
        return "done"

    fn: Callable[[Any], Awaitable[str]] = _work
    assert await PgIndependentWrites(_factory(session)).run(fn) == "done"  # type: ignore[arg-type]
    assert isinstance(seen[0], PostgresWhatsAppRepository)
    assert seen[0]._session is session
    assert session.events == ["open", "begin", "commit", "close"]
    assert compiled(session.executed[0][0]).startswith("UPDATE wa_conversations")

    # Without an explicit factory the app-wide one is used (lazy — never built at import).
    other = _TxnSession()
    monkeypatch.setattr(pg_module, "get_session_factory", lambda: _factory(other))
    assert await PgIndependentWrites().run(fn) == "done"
    assert other.events == ["open", "begin", "commit", "close"]


async def test_postgres_status_marker_uses_an_independent_transaction() -> None:
    session = _TxnSession([StubResult(rowcount=1), StubResult(rowcount=0)])
    marker = PostgresStatusMarker(session_factory=lambda: _factory(session))  # type: ignore[arg-type]

    assert await marker.mark_out_of_band(BRAND_ID, ACCOUNT_ID, ConnectionStatus.REAUTH_REQUIRED)
    assert not await marker.mark_out_of_band(BRAND_ID, ACCOUNT_ID, ConnectionStatus.ACTIVE)
    assert session.events == ["open", "begin", "commit", "close"] * 2
    sql = compiled(session.executed[0][0])
    assert sql.startswith("UPDATE social_connections") and "social_connections.brand_id =" in sql


# ------------------------------------------------------- budget reader (A-4) --
async def test_pg_budget_reader_unions_every_ai_source() -> None:
    session = StubSession(
        [StubResult(scalar=1234), StubResult(scalar=None), StubResult(scalar=NOW)]
    )
    reader = PgBudgetUsageReader(session)  # type: ignore[arg-type]
    since = NOW - timedelta(hours=24)

    assert await reader.combined_usage_since(BRAND_ID, since) == 1234
    sql = compiled(session.executed[0][0])
    assert "UNION ALL" in sql
    assert "FROM content_revisions JOIN content_items" in sql
    assert "FROM wa_reply_drafts" in sql
    assert "content_items.brand_id =" in sql and "wa_reply_drafts.brand_id =" in sql
    assert "'ai_generated'" in sql or "ai_generated" in str(
        session.executed[0][0].compile().params
    )
    assert "sum(" in sql and "coalesce(" in sql

    assert await reader.combined_usage_since(BRAND_ID, since) == 0  # NULL sum ⇒ 0
    assert await reader.oldest_generation_since(BRAND_ID, since) == NOW
    assert "min(" in compiled(session.executed[2][0])
