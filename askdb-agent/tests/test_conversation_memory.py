from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import require_current_user
from api.routes.threads import router as threads_router
from domain.conversation_memory import (
    ThreadDeletionConflict,
    ThreadGrantRevoked,
    ThreadNotFound,
    TurnIdempotencyConflict,
    TurnSequenceConflict,
)
from domain.auth import Principal
from integrations.conversation_store import ConversationMemoryStore
from integrations.deletion_journal import (
    DeletionJournalUnavailable,
    EncryptedDeletionJournal,
)


def _seed_catalog(database_path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE wren_data_sources (
                id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                runtime_status TEXT NOT NULL DEFAULT 'ready'
            );
            CREATE TABLE auth_users (
                id TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE auth_user_data_sources (
                user_id TEXT NOT NULL,
                data_source_id TEXT NOT NULL,
                PRIMARY KEY(user_id, data_source_id)
            );
            CREATE TABLE chat_thread_data_sources (
                thread_id TEXT PRIMARY KEY,
                data_source_id TEXT NOT NULL,
                owner_user_id TEXT,
                created_at TEXT NOT NULL
            );
            INSERT INTO wren_data_sources(id, enabled, runtime_status)
              VALUES ('source-a', 1, 'ready'), ('source-b', 1, 'ready');
            INSERT INTO auth_users(id, role, is_active)
              VALUES ('admin-1', 'admin', 1), ('member-1', 'member', 1),
                     ('member-2', 'member', 1);
            INSERT INTO auth_user_data_sources(user_id, data_source_id)
              VALUES ('member-1', 'source-a');
            """
        )


def test_thread_turns_are_owner_scoped_and_idempotent(tmp_path) -> None:
    database_path = tmp_path / "agent.sqlite3"
    _seed_catalog(database_path)
    store = ConversationMemoryStore(database_path)

    thread = store.create_thread(
        owner_user_id="member-1",
        source_id="source-a",
    )
    started = store.begin_turn(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
        turn_id="turn-1",
        user_content="VIP 用户如何定义？",
        expected_sequence=0,
    )
    retried = store.begin_turn(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
        turn_id="turn-1",
        user_content="VIP 用户如何定义？",
        expected_sequence=0,
    )
    store.complete_turn(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
        turn_id="turn-1",
        assistant_content="当前数据源尚未定义 VIP。",
    )

    assert started.is_new is True
    assert retried.is_new is False
    assert started.user_sequence == retried.user_sequence == 1
    with pytest.raises(TurnIdempotencyConflict):
        store.begin_turn(
            thread_id=thread.thread_id,
            owner_user_id="member-1",
            turn_id="turn-1",
            user_content="另一条请求",
        )
    context = store.load_context(thread.thread_id, owner_user_id="member-1")
    assert [turn.content for turn in context.turns] == [
        "VIP 用户如何定义？",
        "当前数据源尚未定义 VIP。",
    ]
    assert store.begin_turn(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
        turn_id="turn-1",
        user_content="VIP 用户如何定义？",
        expected_sequence=0,
    ).assistant_sequence == 2
    with pytest.raises(TurnSequenceConflict):
        store.begin_turn(
            thread_id=thread.thread_id,
            owner_user_id="member-1",
            turn_id="turn-2",
            user_content="下一个问题",
            expected_sequence=0,
        )
    with pytest.raises(LookupError):
        store.load_context(thread.thread_id, owner_user_id="member-2")


def test_concurrent_turn_retry_creates_one_user_turn(tmp_path) -> None:
    database_path = tmp_path / "agent.sqlite3"
    _seed_catalog(database_path)
    store = ConversationMemoryStore(database_path)
    thread = store.create_thread(owner_user_id="member-1", source_id="source-a")

    def start_turn(_index: int):
        return store.begin_turn(
            thread_id=thread.thread_id,
            owner_user_id="member-1",
            turn_id="same-retry-key-0001",
            user_content="收入是多少？",
        )

    with ThreadPoolExecutor(max_workers=5) as pool:
        outcomes = list(pool.map(start_turn, range(5)))
    with sqlite3.connect(database_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM agent_conversation_turns WHERE thread_id=? AND role='user'",
            (thread.thread_id,),
        ).fetchone()[0]
    assert sum(outcome.is_new for outcome in outcomes) == 1
    assert count == 1


def test_thread_expiry_is_based_on_last_user_turn(tmp_path) -> None:
    database_path = tmp_path / "agent.sqlite3"
    _seed_catalog(database_path)
    now = datetime(2026, 10, 2, tzinfo=UTC)
    clock = [now]
    store = ConversationMemoryStore(database_path, clock=lambda: clock[0])
    thread = store.create_thread(
        owner_user_id="admin-1",
        source_id="source-a",
    )
    clock[0] += timedelta(days=4)
    store.begin_turn(
        thread_id=thread.thread_id,
        owner_user_id="admin-1",
        turn_id="turn-1",
        user_content="最近 30 天收入？",
    )
    store.complete_turn(
        thread_id=thread.thread_id,
        owner_user_id="admin-1",
        turn_id="turn-1",
        assistant_content="收入是 100。",
    )

    assert thread.expires_at == now + timedelta(days=30)
    with sqlite3.connect(database_path) as connection:
        stored_text = connection.execute(
            "SELECT group_concat(content, ' ') FROM agent_conversation_turns"
        ).fetchone()[0]
        expires_at = connection.execute(
            "SELECT expires_at FROM agent_conversation_threads WHERE thread_id=?",
            (thread.thread_id,),
        ).fetchone()[0]
    assert "最近 30 天收入？" in stored_text
    assert datetime.fromisoformat(expires_at) == clock[0] + timedelta(days=30)


def test_inactive_expiry_journals_rule_suppression_and_removes_turns(tmp_path) -> None:
    database_path = tmp_path / "agent.sqlite3"
    _seed_catalog(database_path)
    now = [datetime(2026, 10, 2, tzinfo=UTC)]
    journal = EncryptedDeletionJournal(
        tmp_path / "journal.enc", Fernet.generate_key().decode("ascii")
    )
    store = ConversationMemoryStore(
        database_path,
        clock=lambda: now[0],
        deletion_journal=journal,
        rule_preview=lambda _connection, _thread, _source: (("rule-vip-2",), ("VIP",)),
    )
    thread = store.create_thread(owner_user_id="member-1", source_id="source-a")
    now[0] += timedelta(days=3)
    store.begin_turn(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
        turn_id="turn-1",
        user_content="VIP 定义是什么？",
    )
    store.complete_turn(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
        turn_id="turn-1",
        assistant_content="VIP 是消费大于 10000 的用户。",
    )
    now[0] += timedelta(days=30)

    assert store.expire_inactive_threads() == 1
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT status FROM agent_conversation_threads WHERE thread_id=?",
            (thread.thread_id,),
        ).fetchone()
        turn_count = connection.execute(
            "SELECT COUNT(*) FROM agent_conversation_turns WHERE thread_id=?",
            (thread.thread_id,),
        ).fetchone()[0]
        suppressed = connection.execute(
            "SELECT item_id FROM agent_memory_suppressions WHERE item_type='business_rule'"
        ).fetchone()[0]
    assert row[0] == "expired"
    assert turn_count == 0
    assert suppressed == "rule-vip-2"


def test_stale_in_progress_turns_become_failed_without_exception_text(tmp_path) -> None:
    database_path = tmp_path / "agent.sqlite3"
    _seed_catalog(database_path)
    now = [datetime(2026, 10, 2, tzinfo=UTC)]
    store = ConversationMemoryStore(database_path, clock=lambda: now[0])
    thread = store.create_thread(owner_user_id="member-1", source_id="source-a")
    store.begin_turn(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
        turn_id="interrupted-turn-0001",
        user_content="请计算净收入",
    )
    now[0] += timedelta(minutes=16)

    assert store.fail_stale_turns() == 1
    retry = store.begin_turn(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
        turn_id="interrupted-turn-0001",
        user_content="请计算净收入",
    )
    assert retry.is_new is False
    assert retry.status == "failed"
    assert retry.assistant_content is None


def test_thread_delete_journals_suppression_and_replays_before_history_returns(tmp_path) -> None:
    database_path = tmp_path / "agent.sqlite3"
    backup_path = tmp_path / "pre-delete.sqlite3"
    journal_path = tmp_path / "durable" / "deletions.enc"
    _seed_catalog(database_path)
    key = Fernet.generate_key().decode("ascii")
    journal = EncryptedDeletionJournal(journal_path, key)
    rules = (("rule-vip-1",), ("VIP 用户定义",))
    store = ConversationMemoryStore(
        database_path,
        deletion_journal=journal,
        rule_preview=lambda _connection, _thread, _source: rules,
    )
    thread = store.create_thread(owner_user_id="member-1", source_id="source-a")
    store.begin_turn(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
        turn_id="delete-case-turn-0001",
        user_content="问题保留在本会话",
    )
    store.complete_turn(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
        turn_id="delete-case-turn-0001",
        assistant_content="答复",
    )
    store.begin_turn(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
        turn_id="turn-1",
        user_content="请记住 VIP 定义是近 30 天消费超过 10000 元",
    )
    store.complete_turn(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
        turn_id="turn-1",
        assistant_content="收到，已形成待审核规则候选。",
    )
    impact = store.create_deletion_impact(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
    )
    assert impact.rule_count == 1
    assert impact.rule_labels == ("VIP 用户定义",)

    # Snapshot using SQLite's backup API before the irreversible event.
    with sqlite3.connect(database_path) as original, sqlite3.connect(backup_path) as backup:
        original.backup(backup)
    operation = store.delete_thread(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
        impact_version=impact.impact_version,
        idempotency_key="delete-request-0001",
    )

    assert operation.status == "suppressed"
    assert operation.journal_sequence == 1
    replay = store.delete_thread(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
        impact_version=impact.impact_version,
        idempotency_key="delete-request-0001",
    )
    assert replay.operation_id == operation.operation_id
    with pytest.raises(ThreadDeletionConflict):
        store.delete_thread(
            thread_id=thread.thread_id,
            owner_user_id="member-1",
            impact_version="f" * 32,
            idempotency_key="delete-request-0001",
        )
    with pytest.raises(ThreadNotFound):
        store.load_context(thread.thread_id, owner_user_id="member-1")
    encrypted_bytes = journal_path.read_bytes()
    assert "VIP 用户定义".encode("utf-8") not in encrypted_bytes
    assert b"10000" not in encrypted_bytes

    # Simulate restoring a snapshot taken before deletion. Reconciliation must
    # apply the independent journal before context reads can return.
    restored = ConversationMemoryStore(backup_path, deletion_journal=journal)
    assert restored.reconcile_journal() == 1
    with pytest.raises(ThreadNotFound):
        restored.load_context(thread.thread_id, owner_user_id="member-1")


def test_delete_requires_fresh_impact_and_allows_owner_after_grant_revocation(tmp_path) -> None:
    database_path = tmp_path / "agent.sqlite3"
    _seed_catalog(database_path)
    journal = EncryptedDeletionJournal(
        tmp_path / "journal.enc", Fernet.generate_key().decode("ascii")
    )
    rules = [((), ())]
    store = ConversationMemoryStore(
        database_path,
        deletion_journal=journal,
        rule_preview=lambda _connection, _thread, _source: rules[0],
    )
    thread = store.create_thread(owner_user_id="member-1", source_id="source-a")
    impact = store.create_deletion_impact(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
    )
    rules[0] = (("rule-2",), ("新规则",))
    with pytest.raises(ThreadDeletionConflict):
        store.delete_thread(
            thread_id=thread.thread_id,
            owner_user_id="member-1",
            impact_version=impact.impact_version,
            idempotency_key="delete-request-0002",
        )

    rules[0] = ((), ())
    refreshed = store.create_deletion_impact(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "DELETE FROM auth_user_data_sources WHERE user_id='member-1' AND data_source_id='source-a'"
        )
    with pytest.raises(ThreadGrantRevoked):
        store.load_context(thread.thread_id, owner_user_id="member-1")
    assert store.list_threads(owner_user_id="member-1")[0].thread_id == thread.thread_id
    operation = store.delete_thread(
        thread_id=thread.thread_id,
        owner_user_id="member-1",
        impact_version=refreshed.impact_version,
        idempotency_key="delete-request-0003",
    )
    assert operation.status == "completed_online"


def test_journal_detects_corruption_and_is_idempotent(tmp_path) -> None:
    path = tmp_path / "journal.enc"
    journal = EncryptedDeletionJournal(path, Fernet.generate_key().decode("ascii"))
    first = journal.append(
        event_id="event-1",
        event_type="query_example_revoke",
        source_id="source-a",
        item_type="query_example",
        item_ids=("example-1",),
    )
    retry = journal.append(
        event_id="event-1",
        event_type="query_example_revoke",
        source_id="source-a",
        item_type="query_example",
        item_ids=("example-1",),
    )
    assert retry.sequence == first.sequence == 1
    assert journal.high_water_mark() == 1
    path.write_bytes(path.read_bytes()[:-4] + b"oops")
    with pytest.raises(DeletionJournalUnavailable):
        journal.read_all()


def test_thread_api_uses_server_memory_and_owner_scoped_history(tmp_path) -> None:
    database_path = tmp_path / "agent.sqlite3"
    _seed_catalog(database_path)
    memory = ConversationMemoryStore(database_path)
    app = FastAPI()
    app.state.conversation_memory = memory
    app.include_router(threads_router)
    app.dependency_overrides[require_current_user] = lambda: Principal(
        user_id="member-1", username="member", role="member", must_change_password=False
    )
    client = TestClient(app)

    created = client.post("/v1/threads", json={"data_source_id": "source-a"})
    thread_id = created.json()["thread_id"]
    history = client.get(f"/v1/threads/{thread_id}/history")
    listed = client.get("/v1/threads")

    assert created.status_code == 201
    assert len(thread_id) == 32
    assert history.status_code == 200
    assert history.json()["turns"] == []
    assert listed.json()["threads"][0]["thread_id"] == thread_id
    assert history.headers["cache-control"] == "no-store"

    app.dependency_overrides[require_current_user] = lambda: Principal(
        user_id="member-2", username="other", role="member", must_change_password=False
    )
    assert client.get(f"/v1/threads/{thread_id}/history").status_code == 404


def test_thread_api_delete_requires_confirmation_and_journal(tmp_path) -> None:
    database_path = tmp_path / "agent.sqlite3"
    _seed_catalog(database_path)
    journal = EncryptedDeletionJournal(
        tmp_path / "durable-journal.enc", Fernet.generate_key().decode("ascii")
    )
    memory = ConversationMemoryStore(database_path, deletion_journal=journal)
    app = FastAPI()
    app.state.conversation_memory = memory
    app.include_router(threads_router)
    app.dependency_overrides[require_current_user] = lambda: Principal(
        user_id="member-1", username="member", role="member", must_change_password=False
    )
    client = TestClient(app)
    thread_id = client.post(
        "/v1/threads", json={"data_source_id": "source-a"}
    ).json()["thread_id"]
    impact = client.get(f"/v1/threads/{thread_id}/deletion-impact").json()

    missing_confirmation = client.request(
        "DELETE",
        f"/v1/threads/{thread_id}",
        json={
            "impact_version": impact["impact_version"],
            "confirmed": False,
            "idempotency_key": "delete-api-request-0001",
        },
    )
    deleted = client.request(
        "DELETE",
        f"/v1/threads/{thread_id}",
        json={
            "impact_version": impact["impact_version"],
            "confirmed": True,
            "idempotency_key": "delete-api-request-0002",
        },
    )

    assert missing_confirmation.status_code == 422
    assert deleted.status_code == 200
    assert deleted.json()["thread_content_removed"] is True
    assert client.get(f"/v1/threads/{thread_id}/history").status_code == 404


def test_memory_sanitizer_excludes_sql_and_result_blocks() -> None:
    from application.conversation_memory import sanitize_turn_text

    cleaned = sanitize_turn_text(
        "收入是 100。\n```sql\nSELECT * FROM orders;\n```\n"
        "<result>customer_id=alice@example.com</result>\n"
        "SELECT customer_id\nFROM customers\nWHERE email='alice@example.com';\n"
        "查询完成。"
    )

    assert "收入是 100。" in cleaned
    assert "SELECT" not in cleaned
    assert "FROM customers" not in cleaned
    assert "alice@example.com" not in cleaned
