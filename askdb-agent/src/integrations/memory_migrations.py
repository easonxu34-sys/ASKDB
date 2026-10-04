from __future__ import annotations

import hashlib
import inspect
import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta


Migration = tuple[str, str, Callable[[sqlite3.Connection], None]]


def _conversation_schema(connection: sqlite3.Connection) -> None:
    required = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "chat_thread_data_sources" not in required:
        raise sqlite3.OperationalError("Wren thread/source registry is not initialized")

    schema = """
        CREATE TABLE IF NOT EXISTS agent_conversation_threads (
            thread_id TEXT PRIMARY KEY
                REFERENCES chat_thread_data_sources(thread_id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (status IN ('active', 'deleted', 'expired')),
            summary TEXT,
            summary_version INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_user_turn_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            deleted_at TEXT,
            tombstone_until TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_agent_threads_owner_activity
            ON chat_thread_data_sources(owner_user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_agent_threads_expiry
            ON agent_conversation_threads(status, expires_at);
        CREATE TABLE IF NOT EXISTS agent_conversation_turns (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL
                REFERENCES agent_conversation_threads(thread_id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            turn_id TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(thread_id, sequence),
            UNIQUE(thread_id, turn_id, role)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_turns_thread_sequence
            ON agent_conversation_turns(thread_id, sequence);
        CREATE TABLE IF NOT EXISTS agent_turn_requests (
            thread_id TEXT NOT NULL
                REFERENCES agent_conversation_threads(thread_id) ON DELETE CASCADE,
            turn_id TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
            assistant_content TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(thread_id, turn_id)
        );
        CREATE TABLE IF NOT EXISTS agent_thread_deletion_impacts (
            impact_version TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            data_source_id TEXT NOT NULL,
            impact_hash TEXT NOT NULL,
            rule_count INTEGER NOT NULL DEFAULT 0,
            rule_labels_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_agent_delete_impacts_thread
            ON agent_thread_deletion_impacts(thread_id, owner_user_id, expires_at);
        CREATE TABLE IF NOT EXISTS agent_memory_suppressions (
            event_sequence INTEGER NOT NULL,
            data_source_id TEXT NOT NULL,
            item_type TEXT NOT NULL,
            item_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(event_sequence, item_type, item_id)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_suppressions_item
            ON agent_memory_suppressions(data_source_id, item_type, item_id);
        CREATE TABLE IF NOT EXISTS agent_memory_journal_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            journal_applied_seq INTEGER NOT NULL DEFAULT 0,
            healthy INTEGER NOT NULL DEFAULT 1,
            last_error_code TEXT,
            updated_at TEXT NOT NULL
        );
        INSERT OR IGNORE INTO agent_memory_journal_state
            (id, journal_applied_seq, healthy, updated_at)
            VALUES (1, 0, 1, '1970-01-01T00:00:00+00:00');
        CREATE TABLE IF NOT EXISTS agent_thread_deletion_operations (
            operation_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            thread_id TEXT NOT NULL,
            data_source_id TEXT NOT NULL,
            journal_sequence INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    for statement in schema.split(";"):
        if statement.strip():
            connection.execute(statement)


def _journal_and_turn_serialization(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(agent_memory_journal_state)"
        )
    }
    if "journal_initialized" not in columns:
        connection.execute(
            "ALTER TABLE agent_memory_journal_state "
            "ADD COLUMN journal_initialized INTEGER NOT NULL DEFAULT 0"
        )
    if "journal_id" not in columns:
        connection.execute(
            "ALTER TABLE agent_memory_journal_state ADD COLUMN journal_id TEXT"
        )
    connection.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_one_running_turn_per_thread
           ON agent_turn_requests(thread_id) WHERE status='running'"""
    )


def _query_memory_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _query_memory_index_columns(
    connection: sqlite3.Connection, index: str
) -> tuple[str, ...] | None:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (index,)
    ).fetchone()
    if exists is None:
        return None
    return tuple(
        row[2]
        for row in connection.execute(f"PRAGMA index_info({index})")
    )


def _primary_key_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    entries = [
        (row[5], row[1])
        for row in connection.execute(f"PRAGMA table_info({table})")
        if row[5]
    ]
    return tuple(name for _, name in sorted(entries))


def _unique_index_columns(
    connection: sqlite3.Connection, table: str
) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()
    for row in connection.execute(f"PRAGMA index_list({table})"):
        if row[2]:
            result.add(
                tuple(
                    item[2]
                    for item in connection.execute(f"PRAGMA index_info({row[1]})")
                )
            )
    return result


def _legacy_conversation_schema_is_compatible(
    connection: sqlite3.Connection,
) -> bool:
    """Recognize only the known T1A schema shapes before adopting its checksum.

    The initial migration was already applied in some development databases before
    additive request/deletion fields were moved to migration 003. A source checksum
    mismatch is accepted only when the complete known table/column/index/FK shape is
    present; unknown or partially migrated schemas still fail closed.
    """
    expected_columns = {
        "agent_conversation_threads": {
            "thread_id", "status", "summary", "summary_version", "created_at",
            "last_user_turn_at", "expires_at", "deleted_at", "tombstone_until",
        },
        "agent_conversation_turns": {
            "id", "thread_id", "sequence", "role", "turn_id", "content", "created_at",
        },
        "agent_thread_deletion_impacts": {
            "impact_version", "thread_id", "owner_user_id", "data_source_id",
            "impact_hash", "rule_count", "rule_labels_json", "created_at",
            "expires_at", "consumed_at",
        },
        "agent_memory_suppressions": {
            "event_sequence", "data_source_id", "item_type", "item_id", "reason",
            "created_at",
        },
    }
    known_optional_columns = {
        "agent_thread_deletion_impacts": {"query_example_count"},
        "agent_turn_requests": {"user_sequence", "assistant_sequence"},
        "agent_memory_journal_state": {"journal_initialized", "journal_id"},
        "agent_thread_deletion_operations": {"request_hash"},
    }
    base_columns = {
        "agent_turn_requests": {
            "thread_id", "turn_id", "request_hash", "status", "assistant_content",
            "created_at", "updated_at",
        },
        "agent_memory_journal_state": {
            "id", "journal_applied_seq", "healthy", "last_error_code", "updated_at",
        },
        "agent_thread_deletion_operations": {
            "operation_id", "idempotency_key", "thread_id", "data_source_id",
            "journal_sequence", "status", "created_at", "updated_at",
        },
    }
    for table, expected in expected_columns.items():
        columns = _query_memory_columns(connection, table)
        if columns - known_optional_columns.get(table, set()) != expected:
            return False
    for table, base in base_columns.items():
        columns = _query_memory_columns(connection, table)
        if columns - known_optional_columns[table] != base:
            return False

    expected_primary_keys = {
        "agent_conversation_threads": ("thread_id",),
        "agent_conversation_turns": ("id",),
        "agent_turn_requests": ("thread_id", "turn_id"),
        "agent_thread_deletion_impacts": ("impact_version",),
        "agent_memory_suppressions": ("event_sequence", "item_type", "item_id"),
        "agent_memory_journal_state": ("id",),
        "agent_thread_deletion_operations": ("operation_id",),
    }
    if any(
        _primary_key_columns(connection, table) != expected
        for table, expected in expected_primary_keys.items()
    ):
        return False
    expected_unique_indexes = {
        "agent_conversation_turns": {
            ("thread_id", "sequence"),
            ("thread_id", "turn_id", "role"),
        },
        "agent_thread_deletion_operations": {("idempotency_key",)},
    }
    if any(
        not expected.issubset(_unique_index_columns(connection, table))
        for table, expected in expected_unique_indexes.items()
    ):
        return False

    expected_indexes = {
        "idx_agent_threads_owner_activity": ("owner_user_id", "created_at"),
        "idx_agent_threads_expiry": ("status", "expires_at"),
        "idx_agent_turns_thread_sequence": ("thread_id", "sequence"),
        "idx_agent_delete_impacts_thread": (
            "thread_id", "owner_user_id", "expires_at",
        ),
        "idx_agent_suppressions_item": ("data_source_id", "item_type", "item_id"),
    }
    if any(
        _query_memory_index_columns(connection, name) != columns
        for name, columns in expected_indexes.items()
    ):
        return False
    running_index = _query_memory_index_columns(
        connection, "idx_agent_one_running_turn_per_thread"
    )
    if running_index not in (None, ("thread_id",)):
        return False

    expected_foreign_keys = {
        "agent_conversation_threads": ("chat_thread_data_sources", "thread_id", "thread_id"),
        "agent_conversation_turns": ("agent_conversation_threads", "thread_id", "thread_id"),
        "agent_turn_requests": ("agent_conversation_threads", "thread_id", "thread_id"),
    }
    for table, expected in expected_foreign_keys.items():
        foreign_keys = {
            (row[2], row[3], row[4], row[6].upper())
            for row in connection.execute(f"PRAGMA foreign_key_list({table})")
        }
        if (*expected, "CASCADE") not in foreign_keys:
            return False

    required_checks = {
        "agent_conversation_threads": "check(statusin('active','deleted','expired'))",
        "agent_conversation_turns": "check(rolein('user','assistant'))",
        "agent_turn_requests": "check(statusin('running','completed','failed'))",
        "agent_memory_journal_state": "check(id=1)",
    }
    for table, check in required_checks.items():
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if row is None or check not in re.sub(r"\s+", "", row[0].lower()):
            return False
    return True


def _fail_stale_running_turns(connection: sqlite3.Connection) -> None:
    """Resolve only stale running rows before creating the single-running index."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=15)
    rows = connection.execute(
        """SELECT thread_id, turn_id, updated_at FROM agent_turn_requests
           WHERE status='running'"""
    ).fetchall()
    stale: list[tuple[str, str]] = []
    for row in rows:
        try:
            updated = datetime.fromisoformat(row["updated_at"])
        except (TypeError, ValueError) as exc:
            raise sqlite3.DatabaseError(
                "cannot establish age of a running memory turn"
            ) from exc
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        if updated.astimezone(UTC) <= cutoff:
            stale.append((row["thread_id"], row["turn_id"]))
    for thread_id, turn_id in stale:
        connection.execute(
            """UPDATE agent_turn_requests SET status='failed', updated_at=?
               WHERE thread_id=? AND turn_id=? AND status='running'""",
            (now.isoformat(), thread_id, turn_id),
        )
    duplicates = connection.execute(
        """SELECT thread_id, COUNT(*) AS running_count
           FROM agent_turn_requests WHERE status='running'
           GROUP BY thread_id HAVING COUNT(*)>1"""
    ).fetchall()
    if duplicates:
        thread_ids = ", ".join(row["thread_id"] for row in duplicates[:10])
        raise sqlite3.DatabaseError(
            f"multiple active memory turns require recovery before migration: {thread_ids}"
        )


def _additive_memory_fields(connection: sqlite3.Connection) -> None:
    index = connection.execute(
        """SELECT sql FROM sqlite_master
           WHERE type='index' AND name='idx_agent_one_running_turn_per_thread'"""
    ).fetchone()
    index_properties = {
        row[1]: (bool(row[2]), bool(row[4]))
        for row in connection.execute("PRAGMA index_list(agent_turn_requests)")
    }
    normalized_index_sql = (
        re.sub(r"\s+", "", index[0].lower()) if index is not None else ""
    )
    if (
        index_properties.get("idx_agent_one_running_turn_per_thread") != (True, True)
        or "onagent_turn_requests(thread_id)wherestatus='running'" not in normalized_index_sql
    ):
        raise sqlite3.DatabaseError("single-running-turn index has an unexpected definition")

    request_columns = _query_memory_columns(connection, "agent_turn_requests")
    if "user_sequence" not in request_columns:
        connection.execute(
            "ALTER TABLE agent_turn_requests ADD COLUMN user_sequence INTEGER"
        )
    if "assistant_sequence" not in request_columns:
        connection.execute(
            "ALTER TABLE agent_turn_requests ADD COLUMN assistant_sequence INTEGER"
        )
    connection.execute(
        """UPDATE agent_turn_requests AS request
           SET user_sequence=(
               SELECT MIN(turn.sequence) FROM agent_conversation_turns AS turn
               WHERE turn.thread_id=request.thread_id AND turn.turn_id=request.turn_id
                 AND turn.role='user'
           ) WHERE user_sequence IS NULL"""
    )
    connection.execute(
        """UPDATE agent_turn_requests AS request
           SET assistant_sequence=(
               SELECT MIN(turn.sequence) FROM agent_conversation_turns AS turn
               WHERE turn.thread_id=request.thread_id AND turn.turn_id=request.turn_id
                 AND turn.role='assistant'
           ) WHERE assistant_sequence IS NULL"""
    )
    missing_user_sequences = connection.execute(
        "SELECT COUNT(*) FROM agent_turn_requests WHERE user_sequence IS NULL"
    ).fetchone()[0]
    missing_completed_assistant = connection.execute(
        """SELECT COUNT(*) FROM agent_turn_requests
           WHERE status='completed' AND assistant_sequence IS NULL"""
    ).fetchone()[0]
    if missing_user_sequences or missing_completed_assistant:
        raise sqlite3.DatabaseError(
            "cannot backfill memory turn sequence fields from stored turns"
        )

    operation_columns = _query_memory_columns(
        connection, "agent_thread_deletion_operations"
    )
    if "request_hash" not in operation_columns:
        connection.execute(
            "ALTER TABLE agent_thread_deletion_operations ADD COLUMN request_hash TEXT"
        )


def _thread_creation_idempotency(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS agent_thread_creation_requests (
            owner_user_id TEXT NOT NULL,
            creation_key_hash TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(owner_user_id, creation_key_hash)
        )"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_agent_thread_creation_thread
           ON agent_thread_creation_requests(thread_id)"""
    )


def _chunked_thread_history_import(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS agent_thread_history_imports (
            thread_id TEXT PRIMARY KEY
                REFERENCES agent_conversation_threads(thread_id) ON DELETE CASCADE,
            import_id TEXT NOT NULL,
            expected_chunk_count INTEGER NOT NULL
                CHECK (expected_chunk_count BETWEEN 1 AND 64),
            expected_turn_count INTEGER NOT NULL
                CHECK (expected_turn_count BETWEEN 1 AND 500),
            expected_content_bytes INTEGER NOT NULL
                CHECK (expected_content_bytes BETWEEN 1 AND 2097152),
            expected_chunk_hashes_json TEXT NOT NULL,
            received_chunks INTEGER NOT NULL DEFAULT 0,
            received_turn_count INTEGER NOT NULL DEFAULT 0,
            received_content_bytes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            UNIQUE(thread_id, import_id)
        )"""
    )


def _business_rule_workflow(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS business_rule_candidates (
            business_rule_id TEXT PRIMARY KEY
                CHECK (length(business_rule_id)=32),
            data_source_id TEXT NOT NULL REFERENCES wren_data_sources(id),
            term TEXT,
            definition TEXT,
            mdl_references_json TEXT,
            source_thread_id TEXT
                REFERENCES agent_conversation_threads(thread_id) ON DELETE SET NULL,
            base_wren_revision_id TEXT NOT NULL,
            base_mdl_digest TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            submitted_by TEXT NOT NULL,
            idempotency_hash TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            has_exact_term_conflict INTEGER NOT NULL DEFAULT 0
                CHECK (has_exact_term_conflict IN (0,1)),
            review_status TEXT NOT NULL CHECK (review_status IN (
                'pending', 'needs_clarification', 'needs_revalidation', 'approved', 'rejected',
                'withdrawn', 'revoked', 'expired'
            )),
            publication_status TEXT NOT NULL CHECK (publication_status IN (
                'not_published', 'queued', 'publishing', 'active', 'failed',
                'removal_pending', 'removed'
            )),
            clarification_question TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            review_reason_code TEXT,
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            UNIQUE(submitted_by, idempotency_hash)
        )"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_business_rule_candidates_source_status
           ON business_rule_candidates(data_source_id, review_status, publication_status)"""
    )

    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_business_rule_candidates_thread
           ON business_rule_candidates(data_source_id, source_thread_id)"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS business_rule_candidate_events (
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
        )"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_business_rule_events_rule
           ON business_rule_candidate_events(data_source_id, business_rule_id, created_at)"""
    )
    connection.execute(
        """CREATE TRIGGER IF NOT EXISTS business_rule_events_no_update
           BEFORE UPDATE ON business_rule_candidate_events
           BEGIN SELECT RAISE(ABORT, 'business rule audit events are append-only'); END"""
    )
    connection.execute(
        """CREATE TRIGGER IF NOT EXISTS business_rule_events_no_delete
           BEFORE DELETE ON business_rule_candidate_events
           BEGIN SELECT RAISE(ABORT, 'business rule audit events are append-only'); END"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS business_rule_origins (
            data_source_id TEXT NOT NULL REFERENCES wren_data_sources(id),
            business_rule_id TEXT NOT NULL CHECK (length(business_rule_id)=32),
            source_thread_id TEXT,
            source_thread_hash TEXT,
            term_label TEXT,
            content_hash TEXT NOT NULL,
            active_wren_revision_id TEXT,
            publication_status TEXT NOT NULL CHECK (publication_status IN (
                'active', 'removal_pending', 'removed'
            )),
            published_at TEXT NOT NULL,
            redacted_at TEXT,
            purge_after TEXT,
            PRIMARY KEY(data_source_id, business_rule_id)
        )"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_business_rule_origins_thread
           ON business_rule_origins(data_source_id, source_thread_id, publication_status)"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS agent_thread_history_import_chunks (
            thread_id TEXT NOT NULL,
            import_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
            request_hash TEXT NOT NULL,
            turn_count INTEGER NOT NULL CHECK (turn_count > 0),
            content_bytes INTEGER NOT NULL CHECK (content_bytes >= 0),
            created_at TEXT NOT NULL,
            PRIMARY KEY(thread_id, import_id, chunk_index),
            FOREIGN KEY(thread_id, import_id)
                REFERENCES agent_thread_history_imports(thread_id, import_id)
                ON DELETE CASCADE
        )"""
    )

def _query_example_workflow(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS query_example_candidates (
            query_example_id TEXT PRIMARY KEY CHECK (length(query_example_id)=32),
            data_source_id TEXT NOT NULL REFERENCES wren_data_sources(id),
            source_thread_id TEXT REFERENCES agent_conversation_threads(thread_id) ON DELETE SET NULL,
            source_turn_id TEXT,
            normalized_question TEXT,
            sql_template TEXT,
            parameter_specs_json TEXT,
            connector_type TEXT NOT NULL,
            wren_revision_id TEXT NOT NULL,
            mdl_digest TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            submitted_by TEXT NOT NULL,
            idempotency_hash TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            review_status TEXT NOT NULL CHECK (review_status IN (
                'pending', 'approved', 'rejected', 'needs_revalidation',
                'revoked', 'withdrawn', 'expired'
            )),
            publication_status TEXT NOT NULL CHECK (publication_status IN (
                'not_published', 'queued', 'publishing', 'active', 'failed',
                'superseded', 'removed'
            )),
            reviewed_by TEXT,
            review_reason_code TEXT,
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            activated_at TEXT,
            superseded_at TEXT,
            revoked_at TEXT,
            expires_at TEXT NOT NULL,
            UNIQUE(submitted_by, idempotency_hash)
        )"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_query_examples_source_state
           ON query_example_candidates(data_source_id, review_status, publication_status)"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_query_examples_thread
           ON query_example_candidates(data_source_id, source_thread_id)"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS query_example_candidate_events (
            event_id TEXT PRIMARY KEY,
            query_example_id TEXT NOT NULL,
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
        )"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_query_example_events_item
           ON query_example_candidate_events(data_source_id, query_example_id, created_at)"""
    )
    connection.execute(
        """CREATE TRIGGER IF NOT EXISTS query_example_events_no_update
           BEFORE UPDATE ON query_example_candidate_events
           BEGIN SELECT RAISE(ABORT, 'query example audit events are append-only'); END"""
    )
    connection.execute(
        """CREATE TRIGGER IF NOT EXISTS query_example_events_no_delete
           BEFORE DELETE ON query_example_candidate_events
           BEGIN SELECT RAISE(ABORT, 'query example audit events are append-only'); END"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS query_corpus_revisions (
            data_source_id TEXT NOT NULL REFERENCES wren_data_sources(id),
            corpus_revision INTEGER NOT NULL CHECK (corpus_revision > 0),
            connector_type TEXT NOT NULL,
            wren_revision_id TEXT NOT NULL,
            mdl_digest TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            record_ids_json TEXT NOT NULL,
            canonical_path TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN (
                'prepared', 'active', 'superseded', 'failed', 'expired'
            )),
            created_at TEXT NOT NULL,
            activated_at TEXT,
            superseded_at TEXT,
            delete_after TEXT,
            PRIMARY KEY(data_source_id, corpus_revision)
        )"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_query_corpus_retention
           ON query_corpus_revisions(status, delete_after)"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS query_corpus_source_state (
            data_source_id TEXT PRIMARY KEY REFERENCES wren_data_sources(id),
            active_revision INTEGER,
            prepared_revision INTEGER,
            generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
            updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS query_corpus_operations (
            operation_id TEXT PRIMARY KEY,
            data_source_id TEXT NOT NULL REFERENCES wren_data_sources(id),
            operation_type TEXT NOT NULL CHECK (operation_type IN ('approve', 'activate', 'revoke')),
            base_revision INTEGER,
            target_revision INTEGER,
            generation INTEGER NOT NULL,
            actor_user_id TEXT NOT NULL,
            item_id TEXT,
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('prepared', 'active', 'failed', 'cancelled')),
            failure_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_query_corpus_operations_source
           ON query_corpus_operations(data_source_id, status, generation)"""
    )


def _query_example_thread_provenance(connection: sqlite3.Connection) -> None:
    impact_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(agent_thread_deletion_impacts)")
    }
    if "query_example_count" not in impact_columns:
        connection.execute(
            "ALTER TABLE agent_thread_deletion_impacts "
            "ADD COLUMN query_example_count INTEGER NOT NULL DEFAULT 0"
        )
    candidate_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(query_example_candidates)")
    }
    if "source_thread_hash" not in candidate_columns:
        connection.execute(
            "ALTER TABLE query_example_candidates ADD COLUMN source_thread_hash TEXT"
        )
    if "source_turn_hash" not in candidate_columns:
        connection.execute(
            "ALTER TABLE query_example_candidates ADD COLUMN source_turn_hash TEXT"
        )


def _thread_metadata_and_archive_lifecycle(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(agent_conversation_threads)"
        )
    }
    additions = (
        ("title", "TEXT"),
        ("is_pinned", "INTEGER NOT NULL DEFAULT 0"),
        ("archived_at", "TEXT"),
        ("retention_remaining_seconds", "INTEGER"),
        ("metadata_revision", "INTEGER NOT NULL DEFAULT 1"),
    )
    for name, declaration in additions:
        if name not in columns:
            connection.execute(
                f"ALTER TABLE agent_conversation_threads ADD COLUMN {name} {declaration}"
            )

    connection.execute(
        """CREATE TABLE IF NOT EXISTS agent_thread_list_revisions (
            owner_user_id TEXT PRIMARY KEY,
            revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0)
        )"""
    )
    connection.execute(
        """INSERT OR IGNORE INTO agent_thread_list_revisions(owner_user_id, revision)
           SELECT DISTINCT owner_user_id, 1 FROM chat_thread_data_sources"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_agent_threads_archive_expiry
           ON agent_conversation_threads(status, archived_at, expires_at)"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_chat_thread_owner_source
           ON chat_thread_data_sources(owner_user_id, data_source_id, thread_id)"""
    )
    connection.execute(
        """CREATE TRIGGER IF NOT EXISTS agent_thread_source_name_revision
           AFTER UPDATE OF display_name ON wren_data_sources
           WHEN OLD.display_name IS NOT NEW.display_name
           BEGIN
               UPDATE agent_thread_list_revisions
               SET revision=revision+1
               WHERE owner_user_id IN (
                   SELECT DISTINCT owner_user_id FROM chat_thread_data_sources
                   WHERE data_source_id=NEW.id
               );
           END"""
    )


MIGRATIONS: tuple[Migration, ...] = (
    ("agent_memory_001_conversations", "conversation-v1", _conversation_schema),
    (
        "agent_memory_002_journal_and_turn_serialization",
        "journal-id-and-single-running-turn-v1",
        _journal_and_turn_serialization,
    ),
    (
        "agent_memory_003_additive_turn_and_deletion_fields",
        "additive-turn-and-deletion-fields-v1",
        _additive_memory_fields,
    ),
    (
        "agent_memory_004_thread_creation_idempotency",
        "thread-creation-idempotency-v1",
        _thread_creation_idempotency,
    ),
    (
        "agent_memory_005_chunked_thread_history_import",
        "chunked-thread-history-import-v1",
        _chunked_thread_history_import,
    ),
    (
        "agent_memory_006_business_rule_workflow",
        "business-rule-candidate-and-origin-v1",
        _business_rule_workflow,
    ),
    (
        "agent_memory_007_query_example_workflow",
        "query-example-review-corpus-and-retention-v1",
        _query_example_workflow,
    ),
    (
        "agent_memory_008_query_example_thread_provenance",
        "query-example-thread-provenance-and-deletion-impact-v1",
        _query_example_thread_provenance,
    ),
    (
        "agent_memory_009_thread_metadata_and_archive_lifecycle",
        "thread-metadata-owner-list-revisions-and-paused-retention-v1",
        _thread_metadata_and_archive_lifecycle,
    ),
)


def apply_memory_migrations(connection: sqlite3.Connection) -> None:
    """Apply named memory migrations once, serializing concurrent starters."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_id TEXT PRIMARY KEY,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )"""
    )
    connection.execute("BEGIN IMMEDIATE")
    try:
        for migration_id, source_version, apply in MIGRATIONS:
            checksum = hashlib.sha256(
                f"{source_version}\n{inspect.getsource(apply)}".encode("utf-8")
            ).hexdigest()
            existing = connection.execute(
                "SELECT checksum FROM schema_migrations WHERE migration_id=?",
                (migration_id,),
            ).fetchone()
            if existing:
                if existing[0] != checksum:
                    if (
                        migration_id != "agent_memory_001_conversations"
                        or not _legacy_conversation_schema_is_compatible(connection)
                    ):
                        raise sqlite3.DatabaseError("memory migration checksum mismatch")
                    connection.execute(
                        "UPDATE schema_migrations SET checksum=? WHERE migration_id=?",
                        (checksum, migration_id),
                    )
                continue
            if migration_id == "agent_memory_002_journal_and_turn_serialization":
                _fail_stale_running_turns(connection)
            apply(connection)
            connection.execute(
                "INSERT INTO schema_migrations(migration_id, checksum, applied_at) VALUES (?, ?, ?)",
                (migration_id, checksum, datetime.now(UTC).isoformat()),
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
