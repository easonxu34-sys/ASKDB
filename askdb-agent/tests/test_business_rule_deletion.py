from __future__ import annotations

import sqlite3
import json
from datetime import UTC, datetime

from cryptography.fernet import Fernet

from integrations.business_rule_store import BusinessRuleMemoryStore
from integrations.deletion_journal import EncryptedDeletionJournal, JournalEvent


def _seed_published_rule(database_path, *, rule_id: str, thread_id: str) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE business_rule_candidates (
                business_rule_id TEXT PRIMARY KEY,
                data_source_id TEXT NOT NULL,
                term TEXT,
                definition TEXT,
                mdl_references_json TEXT,
                source_thread_id TEXT,
                base_wren_revision_id TEXT NOT NULL,
                base_mdl_digest TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                submitted_by TEXT NOT NULL,
                idempotency_hash TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                has_exact_term_conflict INTEGER NOT NULL,
                review_status TEXT NOT NULL,
                publication_status TEXT NOT NULL,
                clarification_question TEXT,
                reviewed_by TEXT,
                reviewed_at TEXT,
                review_reason_code TEXT,
                version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE business_rule_origins (
                data_source_id TEXT NOT NULL,
                business_rule_id TEXT NOT NULL,
                source_thread_id TEXT,
                source_thread_hash TEXT,
                term_label TEXT,
                content_hash TEXT NOT NULL,
                active_wren_revision_id TEXT,
                publication_status TEXT NOT NULL,
                published_at TEXT NOT NULL,
                redacted_at TEXT,
                purge_after TEXT,
                PRIMARY KEY(data_source_id, business_rule_id)
            );
            CREATE TABLE business_rule_candidate_events (
                event_id TEXT PRIMARY KEY,
                business_rule_id TEXT NOT NULL,
                data_source_id TEXT NOT NULL,
                actor_user_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                previous_review_status TEXT,
                review_status TEXT NOT NULL,
                previous_publication_status TEXT,
                publication_status TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """INSERT INTO business_rule_candidates
               (business_rule_id, data_source_id, term, definition, mdl_references_json,
                source_thread_id, base_wren_revision_id, base_mdl_digest, content_hash,
                submitted_by, idempotency_hash, request_hash, has_exact_term_conflict,
                review_status, publication_status, version, created_at, updated_at, expires_at)
               VALUES (?, 'source-a', NULL, NULL, NULL, NULL, 'rev-1', 'digest-1',
                       'content-hash', 'member-1', 'idem-hash', 'request-hash', 0,
                       'approved', 'active', 2, '2026-10-01T00:00:00+00:00',
                       '2026-10-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00')""",
            (rule_id,),
        )
        connection.execute(
            """INSERT INTO business_rule_origins
               (data_source_id, business_rule_id, source_thread_id, source_thread_hash,
                term_label, content_hash, active_wren_revision_id, publication_status,
                published_at)
               VALUES ('source-a', ?, ?, 'thread-hash', '异常地区', 'content-hash',
                       'rev-1', 'active', '2026-10-01T00:00:00+00:00')""",
            (rule_id, thread_id),
        )


def test_deleting_source_thread_immediately_revokes_published_candidate(tmp_path) -> None:
    database_path = tmp_path / "settings.sqlite3"
    rule_id = "a" * 32
    thread_id = "source-thread-0001"
    _seed_published_rule(database_path, rule_id=rule_id, thread_id=thread_id)
    journal = EncryptedDeletionJournal(
        tmp_path / "journal" / "deletions.enc", Fernet.generate_key().decode("ascii")
    )
    store = BusinessRuleMemoryStore(database_path, deletion_journal=journal)
    event = JournalEvent(
        sequence=1,
        event_id="thread-delete:request-0000000001",
        event_type="thread_delete",
        source_id="source-a",
        thread_id=thread_id,
        item_type="business_rule",
        item_ids=(rule_id,),
        request_hash="request-hash",
        created_at=datetime(2026, 10, 4, tzinfo=UTC),
        previous_hash="0" * 64,
        record_hash="1" * 64,
        actor_id="member-1",
    )
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        store.apply(connection, event)
        candidate = connection.execute(
            "SELECT review_status, publication_status, term, definition, version "
            "FROM business_rule_candidates WHERE business_rule_id=?",
            (rule_id,),
        ).fetchone()

    assert tuple(candidate) == ("revoked", "removal_pending", None, None, 3)


def test_thread_delete_preview_includes_only_the_rule_source_thread(tmp_path) -> None:
    database_path = tmp_path / "settings.sqlite3"
    rule_id = "d" * 32
    _seed_published_rule(
        database_path, rule_id=rule_id, thread_id="actual-source-thread"
    )
    journal = EncryptedDeletionJournal(
        tmp_path / "journal" / "deletions.enc", Fernet.generate_key().decode("ascii")
    )
    store = BusinessRuleMemoryStore(database_path, deletion_journal=journal)

    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        assert store.preview(connection, "old-referencing-thread", "source-a") == ((), ())
        assert store.preview(connection, "actual-source-thread", "source-a") == (
            (rule_id,),
            ("异常地区",),
        )


def _seed_orphaned_suppression(
    database_path, *, rule_id: str, rule_name: str, delete_status: str = "completed_online"
) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE business_rule_candidates (
                business_rule_id TEXT PRIMARY KEY,
                data_source_id TEXT NOT NULL,
                source_thread_id TEXT,
                publication_status TEXT NOT NULL,
                review_status TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                base_wren_revision_id TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE business_rule_candidate_events (
                data_source_id TEXT NOT NULL,
                business_rule_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE business_rule_origins (
                data_source_id TEXT NOT NULL,
                business_rule_id TEXT NOT NULL,
                source_thread_id TEXT,
                source_thread_hash TEXT,
                term_label TEXT,
                content_hash TEXT NOT NULL,
                active_wren_revision_id TEXT,
                publication_status TEXT NOT NULL,
                published_at TEXT NOT NULL,
                redacted_at TEXT,
                purge_after TEXT,
                PRIMARY KEY(data_source_id, business_rule_id)
            );
            CREATE TABLE agent_memory_suppressions (
                event_sequence INTEGER NOT NULL,
                data_source_id TEXT NOT NULL,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE wren_data_sources (
                id TEXT PRIMARY KEY,
                active_revision_id TEXT NOT NULL
            );
            CREATE TABLE wren_revisions (
                source_id TEXT NOT NULL,
                id TEXT NOT NULL,
                status TEXT NOT NULL,
                config_json TEXT NOT NULL,
                project_dir TEXT
            );
            CREATE TABLE agent_thread_deletion_operations (
                operation_id TEXT PRIMARY KEY,
                data_source_id TEXT NOT NULL,
                journal_sequence INTEGER NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO agent_memory_suppressions VALUES (1, 'source-a', 'business_rule', ?, 'thread_delete', '2026-10-04T00:00:00+00:00')",
            (rule_id,),
        )
        connection.execute(
            "INSERT INTO wren_data_sources(id, active_revision_id) VALUES ('source-a', 'rev-active')"
        )
        connection.execute(
            "INSERT INTO wren_revisions(source_id, id, status, config_json, project_dir) VALUES ('source-a', 'rev-active', 'active', ?, NULL)",
            (json.dumps({"rules": [{"name": rule_name, "content": "# 异常地区"}]}),),
        )
        connection.execute(
            "INSERT INTO agent_thread_deletion_operations VALUES ('delete-1', 'source-a', 1, ?, '2026-10-04T00:00:00+00:00')",
            (delete_status,),
        )


def test_orphaned_suppression_recovers_only_matching_managed_wren_identity(tmp_path) -> None:
    database_path = tmp_path / "settings.sqlite3"
    rule_id = "b" * 32
    _seed_orphaned_suppression(
        database_path, rule_id=rule_id, rule_name=f"askdb_br_{rule_id}"
    )
    journal = EncryptedDeletionJournal(
        tmp_path / "journal" / "deletions.enc", Fernet.generate_key().decode("ascii")
    )
    store = BusinessRuleMemoryStore(database_path, deletion_journal=journal)

    pending = store.list_pending_removals()

    assert pending == {"source-a": (rule_id,)}
    with sqlite3.connect(database_path) as connection:
        status = connection.execute(
            "SELECT status FROM agent_thread_deletion_operations WHERE operation_id='delete-1'"
        ).fetchone()[0]
    assert status == "suppressed"


def test_orphaned_suppression_does_not_match_native_rule_by_display_label(tmp_path) -> None:
    database_path = tmp_path / "settings.sqlite3"
    rule_id = "c" * 32
    _seed_orphaned_suppression(
        database_path,
        rule_id=rule_id,
        rule_name="异常地区",
        delete_status="suppressed",
    )
    journal = EncryptedDeletionJournal(
        tmp_path / "journal" / "deletions.enc", Fernet.generate_key().decode("ascii")
    )
    store = BusinessRuleMemoryStore(database_path, deletion_journal=journal)

    pending = store.list_pending_removals()

    assert pending == {}
    with sqlite3.connect(database_path) as connection:
        status = connection.execute(
            "SELECT status FROM agent_thread_deletion_operations WHERE operation_id='delete-1'"
        ).fetchone()[0]
    assert status == "completed_online"


def test_delete_operation_completes_only_after_every_linked_rule_is_removed(tmp_path) -> None:
    database_path = tmp_path / "settings.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE agent_memory_suppressions (
                event_sequence INTEGER NOT NULL,
                data_source_id TEXT NOT NULL,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE agent_thread_deletion_operations (
                operation_id TEXT PRIMARY KEY,
                data_source_id TEXT NOT NULL,
                journal_sequence INTEGER NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE business_rule_origins (
                data_source_id TEXT NOT NULL,
                business_rule_id TEXT NOT NULL,
                publication_status TEXT NOT NULL
            );
            CREATE TABLE business_rule_candidates (
                data_source_id TEXT NOT NULL,
                business_rule_id TEXT NOT NULL,
                publication_status TEXT NOT NULL
            );
            INSERT INTO agent_memory_suppressions VALUES
                (7, 'source-a', 'business_rule', 'rule-a', 'thread_delete', '2026-10-04T00:00:00+00:00'),
                (7, 'source-a', 'business_rule', 'rule-b', 'thread_delete', '2026-10-04T00:00:00+00:00');
            INSERT INTO agent_thread_deletion_operations VALUES
                ('delete-7', 'source-a', 7, 'suppressed', '2026-10-04T00:00:00+00:00');
            INSERT INTO business_rule_origins VALUES
                ('source-a', 'rule-a', 'removed'),
                ('source-a', 'rule-b', 'removal_pending');
            """
        )
    journal = EncryptedDeletionJournal(
        tmp_path / "journal" / "deletions.enc", Fernet.generate_key().decode("ascii")
    )
    store = BusinessRuleMemoryStore(database_path, deletion_journal=journal)
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        store._advance_thread_deletion_statuses(
            connection,
            data_source_id="source-a",
            business_rule_ids=("rule-a",),
            now=datetime(2026, 10, 4, tzinfo=UTC),
        )
        status = connection.execute(
            "SELECT status FROM agent_thread_deletion_operations WHERE operation_id='delete-7'"
        ).fetchone()[0]
        assert status == "suppressed"
        connection.execute(
            "UPDATE business_rule_origins SET publication_status='removed' WHERE business_rule_id='rule-b'"
        )
        store._advance_thread_deletion_statuses(
            connection,
            data_source_id="source-a",
            business_rule_ids=("rule-b",),
            now=datetime(2026, 10, 4, tzinfo=UTC),
        )
        status = connection.execute(
            "SELECT status FROM agent_thread_deletion_operations WHERE operation_id='delete-7'"
        ).fetchone()[0]

    assert status == "completed_online"
