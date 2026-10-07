from __future__ import annotations

import json
from datetime import UTC, datetime

from cryptography.fernet import Fernet

from integrations.business_rule_store import BusinessRuleMemoryStore
from integrations.deletion_journal import EncryptedDeletionJournal, JournalEvent


_NOW = "2026-10-01T00:00:00+00:00"


def _seed_source(connection, *, active_revision_id: str | None = None) -> None:
    connection.execute(
        """INSERT INTO wren_data_sources
           (id, display_name, connector_type, enabled, active_revision_id,
            runtime_status, created_at, updated_at)
           VALUES ('source-a', 'Source A', 'mysql', 1, %s, 'ready', %s, %s)""",
        (active_revision_id, _NOW, _NOW),
    )


def _insert_candidate(
    connection,
    *,
    rule_id: str,
    term: str | None,
    source_thread_id: str | None = None,
    publication_status: str = "active",
    review_status: str = "approved",
) -> None:
    connection.execute(
        """INSERT INTO business_rule_candidates
           (business_rule_id, data_source_id, term, source_thread_id,
            base_wren_revision_id, base_mdl_digest, content_hash, submitted_by,
            idempotency_hash, request_hash, review_status, publication_status,
            version, created_at, updated_at, expires_at)
           VALUES (%s, 'source-a', %s, %s, 'rev-1', 'digest-1', 'content-hash',
                   'member-1', %s, 'request-hash', %s, %s, 2, %s, %s, %s)""",
        (
            rule_id,
            term,
            source_thread_id,
            f"idempotency-{rule_id}",
            review_status,
            publication_status,
            _NOW,
            _NOW,
            "2026-11-01T00:00:00+00:00",
        ),
    )


def _insert_origin(
    connection,
    *,
    rule_id: str,
    thread_id: str | None,
    term_label: str | None,
    publication_status: str,
) -> None:
    connection.execute(
        """INSERT INTO business_rule_origins
           (data_source_id, business_rule_id, source_thread_id, source_thread_hash,
            term_label, content_hash, active_wren_revision_id, publication_status,
            published_at)
           VALUES ('source-a', %s, %s, 'thread-hash', %s, 'content-hash',
                   'rev-1', %s, %s)""",
        (rule_id, thread_id, term_label, publication_status, _NOW),
    )


def _seed_published_rule(database, *, rule_id: str, thread_id: str) -> None:
    with database.connect() as connection:
        _seed_source(connection)
        _insert_candidate(connection, rule_id=rule_id, term=None)
        _insert_origin(
            connection,
            rule_id=rule_id,
            thread_id=thread_id,
            term_label="异常地区",
            publication_status="active",
        )


def test_deleting_source_thread_immediately_revokes_published_candidate(
    tmp_path, postgres_database
) -> None:
    rule_id = "a" * 32
    thread_id = "source-thread-0001"
    _seed_published_rule(postgres_database, rule_id=rule_id, thread_id=thread_id)
    journal = EncryptedDeletionJournal(
        tmp_path / "journal" / "deletions.enc", Fernet.generate_key().decode("ascii")
    )
    store = BusinessRuleMemoryStore(postgres_database, deletion_journal=journal)
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
    with postgres_database.connect() as connection:
        store.apply(connection, event)
        candidate = connection.execute(
            "SELECT review_status, publication_status, term, definition, version "
            "FROM business_rule_candidates WHERE business_rule_id=%s",
            (rule_id,),
        ).fetchone()

    assert tuple(candidate[index] for index in range(5)) == (
        "revoked", "removal_pending", None, None, 3
    )


def test_thread_delete_preview_includes_only_the_rule_source_thread(
    tmp_path, postgres_database
) -> None:
    rule_id = "d" * 32
    _seed_published_rule(postgres_database, rule_id=rule_id, thread_id="actual-source-thread")
    journal = EncryptedDeletionJournal(
        tmp_path / "journal" / "deletions.enc", Fernet.generate_key().decode("ascii")
    )
    store = BusinessRuleMemoryStore(postgres_database, deletion_journal=journal)

    with postgres_database.connect() as connection:
        assert store.preview(connection, "old-referencing-thread", "source-a") == ((), ())
        assert store.preview(connection, "actual-source-thread", "source-a") == (
            (rule_id,),
            ("异常地区",),
        )


def _seed_orphaned_suppression(
    database,
    *,
    rule_id: str,
    rule_name: str,
    delete_status: str = "completed_online",
) -> None:
    with database.connect() as connection:
        _seed_source(connection, active_revision_id="rev-active")
        connection.execute(
            """INSERT INTO wren_revisions
               (id, source_id, status, config_json, created_at, updated_at)
               VALUES ('rev-active', 'source-a', 'active', %s, %s, %s)""",
            (json.dumps({"rules": [{"name": rule_name, "content": "# 异常地区"}]}), _NOW, _NOW),
        )
        connection.execute(
            """INSERT INTO agent_memory_suppressions
               (event_sequence, data_source_id, item_type, item_id, reason, created_at)
               VALUES (1, 'source-a', 'business_rule', %s, 'thread_delete', %s)""",
            (rule_id, _NOW),
        )
        connection.execute(
            """INSERT INTO agent_thread_deletion_operations
               (operation_id, idempotency_key, thread_id, data_source_id, journal_sequence,
                status, created_at, updated_at)
               VALUES ('delete-1', 'delete-1-key', 'thread-1', 'source-a', 1, %s, %s, %s)""",
            (delete_status, _NOW, _NOW),
        )


def test_orphaned_suppression_recovers_only_matching_managed_wren_identity(
    tmp_path, postgres_database
) -> None:
    rule_id = "b" * 32
    _seed_orphaned_suppression(
        postgres_database, rule_id=rule_id, rule_name=f"askdb_br_{rule_id}"
    )
    journal = EncryptedDeletionJournal(
        tmp_path / "journal" / "deletions.enc", Fernet.generate_key().decode("ascii")
    )
    store = BusinessRuleMemoryStore(postgres_database, deletion_journal=journal)

    pending = store.list_pending_removals()

    assert pending == {"source-a": (rule_id,)}
    with postgres_database.connect() as connection:
        status = connection.execute(
            "SELECT status FROM agent_thread_deletion_operations WHERE operation_id='delete-1'"
        ).fetchone()[0]
    assert status == "suppressed"


def test_orphaned_suppression_does_not_match_native_rule_by_display_label(
    tmp_path, postgres_database
) -> None:
    rule_id = "c" * 32
    _seed_orphaned_suppression(
        postgres_database,
        rule_id=rule_id,
        rule_name="异常地区",
        delete_status="suppressed",
    )
    journal = EncryptedDeletionJournal(
        tmp_path / "journal" / "deletions.enc", Fernet.generate_key().decode("ascii")
    )
    store = BusinessRuleMemoryStore(postgres_database, deletion_journal=journal)

    assert store.list_pending_removals() == {}
    with postgres_database.connect() as connection:
        status = connection.execute(
            "SELECT status FROM agent_thread_deletion_operations WHERE operation_id='delete-1'"
        ).fetchone()[0]
    assert status == "completed_online"


def test_delete_operation_completes_only_after_every_linked_rule_is_removed(
    tmp_path, postgres_database
) -> None:
    rule_a = "a" * 32
    rule_b = "b" * 32
    with postgres_database.connect() as connection:
        _seed_source(connection)
        for rule_id, status in ((rule_a, "removed"), (rule_b, "removal_pending")):
            _insert_candidate(
                connection,
                rule_id=rule_id,
                term="rule",
                publication_status=status,
                review_status="revoked",
            )
            _insert_origin(
                connection,
                rule_id=rule_id,
                thread_id=None,
                term_label="rule",
                publication_status=status,
            )
            connection.execute(
                """INSERT INTO agent_memory_suppressions
                   (event_sequence, data_source_id, item_type, item_id, reason, created_at)
                   VALUES (7, 'source-a', 'business_rule', %s, 'thread_delete', %s)""",
                (rule_id, _NOW),
            )
        connection.execute(
            """INSERT INTO agent_thread_deletion_operations
               (operation_id, idempotency_key, thread_id, data_source_id, journal_sequence,
                status, created_at, updated_at)
               VALUES ('delete-7', 'delete-7-key', 'thread-7', 'source-a', 7,
                       'suppressed', %s, %s)""",
            (_NOW, _NOW),
        )

    journal = EncryptedDeletionJournal(
        tmp_path / "journal" / "deletions.enc", Fernet.generate_key().decode("ascii")
    )
    store = BusinessRuleMemoryStore(postgres_database, deletion_journal=journal)
    with postgres_database.connect() as connection:
        store._advance_thread_deletion_statuses(
            connection,
            data_source_id="source-a",
            business_rule_ids=(rule_a,),
            now=datetime(2026, 10, 4, tzinfo=UTC),
        )
        status = connection.execute(
            "SELECT status FROM agent_thread_deletion_operations WHERE operation_id='delete-7'"
        ).fetchone()[0]
        assert status == "suppressed"
        connection.execute(
            "UPDATE business_rule_origins SET publication_status='removed' "
            "WHERE business_rule_id=%s",
            (rule_b,),
        )
        connection.execute(
            "UPDATE business_rule_candidates SET publication_status='removed' "
            "WHERE business_rule_id=%s",
            (rule_b,),
        )
        store._advance_thread_deletion_statuses(
            connection,
            data_source_id="source-a",
            business_rule_ids=(rule_b,),
            now=datetime(2026, 10, 4, tzinfo=UTC),
        )
        status = connection.execute(
            "SELECT status FROM agent_thread_deletion_operations WHERE operation_id='delete-7'"
        ).fetchone()[0]

    assert status == "completed_online"
