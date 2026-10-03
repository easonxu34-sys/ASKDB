from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from askdb_agent.domain.conversation_memory import (
    ConversationThread,
    ConversationTurn,
    HistoryImportChunkResult,
    LegacyHistoryImportDescriptor,
    MemoryRevocationOperation,
    ThreadDeletionConflict,
    ThreadDeletionImpact,
    ThreadDeletionJournalRequired,
    ThreadDeletionParticipantUnavailable,
    ThreadDeletionOperation,
    ThreadCreateIdempotencyConflict,
    ThreadContext,
    ThreadGrantRevoked,
    ThreadHistoryImportConflict,
    ThreadHistoryImportIncomplete,
    ThreadNotFound,
    TurnIdempotencyConflict,
    TurnAlreadyRunning,
    TurnNotFound,
    TurnSequenceConflict,
    TurnStart,
)
from askdb_agent.domain.business_rules import BusinessRuleForbidden
from askdb_agent.integrations.memory_migrations import apply_memory_migrations
from askdb_agent.integrations.deletion_journal import (
    DeletionJournalUnavailable,
    EncryptedDeletionJournal,
    JournalEvent,
)
from askdb_agent.integrations.memory_deletion import BusinessRuleDeletionParticipant


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _opaque_key(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("idempotency key is invalid")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ConversationMemoryStore:
    """Owner-scoped natural-language thread persistence in the Wren catalog DB."""

    def __init__(
        self,
        database_path: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        retention: timedelta = timedelta(days=30),
        tombstone_retention: timedelta = timedelta(days=30),
        deletion_journal: EncryptedDeletionJournal | None = None,
        rule_preview: Callable[[sqlite3.Connection, str, str], tuple[tuple[str, ...], tuple[str, ...]]] | None = None,
        deletion_participant: BusinessRuleDeletionParticipant | None = None,
        suppression_participants: tuple[Any, ...] = (),
    ) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.retention = retention
        self.tombstone_retention = tombstone_retention
        self.deletion_journal = deletion_journal
        self.rule_preview = rule_preview
        self.deletion_participant = deletion_participant
        self.suppression_participants = suppression_participants
        self.initialize()

    def _acquire_suppression_mutation_barrier(self, source_id: str) -> Callable[[], None] | None:
        participant = self.deletion_participant
        acquire = getattr(participant, "acquire_suppression_mutation", None)
        release = getattr(participant, "release_suppression_mutation", None)
        if not callable(acquire) or not callable(release):
            return None
        acquire(source_id)
        return lambda: release(source_id)

    def _now(self) -> datetime:
        value = self.clock()
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        os.chmod(self.database_path, 0o600)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def initialize(self) -> None:
        connection = self._connect()
        try:
            # The memory-enabled service is single-process, but requests and the
            # sweeper use separate SQLite connections. WAL keeps their short
            # reads from blocking writes while FULL sync preserves commit
            # durability for journal-applied suppression state.
            connection.execute("PRAGMA journal_mode = WAL")
            apply_memory_migrations(connection)
            state = connection.execute(
                "SELECT journal_initialized, journal_id FROM agent_memory_journal_state WHERE id=1"
            ).fetchone()
        finally:
            connection.close()
        initialized = bool(state["journal_initialized"])
        if self.deletion_journal is None:
            if initialized:
                self._mark_journal_unhealthy("JOURNAL_NOT_CONFIGURED")
                raise DeletionJournalUnavailable("initialized journal is not configured")
            return
        try:
            journal_id = self.deletion_journal.initialize(
                expected_journal_id=state["journal_id"] if initialized else None,
                allow_create=not initialized,
            )
            connection = self._connect()
            try:
                connection.execute(
                    """UPDATE agent_memory_journal_state
                       SET journal_initialized=1, journal_id=?, healthy=0,
                           last_error_code='JOURNAL_REPLAY_REQUIRED', updated_at=?
                       WHERE id=1""",
                    (journal_id, self._now().isoformat()),
                )
            finally:
                connection.close()
            self.reconcile_journal()
        except DeletionJournalUnavailable:
            self._mark_journal_unhealthy("JOURNAL_INVALID")
            raise

    def _has_rule_deletion_schema(self, connection: sqlite3.Connection) -> bool:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('business_rule_origins', 'business_rule_candidates')"
        ).fetchall()
        return bool(rows)

    def assert_deletion_participant_ready(self) -> None:
        connection = self._connect()
        try:
            if self._has_rule_deletion_schema(connection) and self.deletion_participant is None:
                raise ThreadDeletionParticipantUnavailable(
                    "linked business-rule deletion participant is not configured"
                )
            has_query_examples = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='query_example_candidates'"
            ).fetchone() is not None
            if has_query_examples and not any(
                callable(getattr(participant, "apply_suppression", None))
                and callable(getattr(participant, "preview", None))
                for participant in self.suppression_participants
            ):
                raise ThreadDeletionParticipantUnavailable(
                    "query-example suppression and preview participant is not configured"
                )
        finally:
            connection.close()

    def _preview_linked_rules(
        self, connection: sqlite3.Connection, thread_id: str, source_id: str
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if self.deletion_participant is not None:
            return self.deletion_participant.preview(connection, thread_id, source_id)
        if self._has_rule_deletion_schema(connection):
            raise ThreadDeletionParticipantUnavailable(
                "linked business-rule deletion participant is not configured"
            )
        if self.rule_preview is not None:
            return self.rule_preview(connection, thread_id, source_id)
        return (), ()

    def _preview_query_examples(
        self, connection: sqlite3.Connection, thread_id: str, source_id: str
    ) -> tuple[str, ...]:
        has_query_examples = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='query_example_candidates'"
        ).fetchone() is not None
        if not has_query_examples:
            return ()
        for participant in self.suppression_participants:
            preview = getattr(participant, "preview", None)
            if callable(preview):
                return tuple(preview(connection, thread_id, source_id))
        raise ThreadDeletionParticipantUnavailable(
            "query-example deletion preview participant is not configured"
        )

    def create_thread(
        self,
        *,
        owner_user_id: str,
        source_id: str,
        creation_key: str | None = None,
        initial_history: tuple[tuple[str, str], ...] = (),
        history_import: LegacyHistoryImportDescriptor | None = None,
    ) -> ConversationThread:
        from askdb_agent.application.conversation_memory import sanitize_turn_text

        if len(initial_history) > 500:
            raise ValueError("legacy history exceeds the supported turn limit")
        if history_import is not None:
            if initial_history:
                raise ValueError("inline history cannot accompany a chunked import")
            if (
                not 1 <= len(history_import.chunk_hashes) <= 64
                or not 1 <= history_import.turn_count <= 500
                or not 1 <= history_import.content_bytes <= 2 * 1024 * 1024
                or any(
                    not re.fullmatch(r"[a-f0-9]{64}", item)
                    for item in history_import.chunk_hashes
                )
            ):
                raise ValueError("legacy history import descriptor is invalid")
        creation_key = creation_key or uuid.uuid4().hex
        if not 16 <= len(creation_key) <= 128:
            raise ValueError("thread creation key is invalid")
        creation_key_hash = _opaque_key(creation_key)
        safe_history_items: list[tuple[str, str]] = []
        for raw_user, raw_assistant in initial_history:
            user_content = sanitize_turn_text(raw_user, max_chars=8192)
            if user_content:
                safe_history_items.append(
                    (user_content, sanitize_turn_text(raw_assistant, max_chars=8192))
                )
        safe_history = tuple(safe_history_items)
        import_contract = (
            {
                "import_id": history_import.import_id,
                "chunk_hashes": history_import.chunk_hashes,
                "turn_count": history_import.turn_count,
                "content_bytes": history_import.content_bytes,
            }
            if history_import is not None
            else None
        )
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "source_id": source_id,
                    "history": safe_history,
                    "history_import": import_contract,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        now = self._now()
        thread_id = uuid.uuid4().hex
        expires_at = now + self.retention
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            prior_create = connection.execute(
                """SELECT request_hash, thread_id FROM agent_thread_creation_requests
                   WHERE owner_user_id=? AND creation_key_hash=?""",
                (owner_user_id, creation_key_hash),
            ).fetchone()
            if prior_create is not None:
                if prior_create["request_hash"] != request_hash:
                    raise ThreadCreateIdempotencyConflict(
                        "thread creation key was reused with changed input"
                    )
                prior_thread = self._owned_thread(
                    connection,
                    prior_create["thread_id"],
                    owner_user_id,
                    require_source_grant=True,
                )
                connection.commit()
                return ConversationThread(
                    prior_create["thread_id"],
                    prior_thread["data_source_id"],
                    _parse_timestamp(prior_thread["created_at"]),
                    _parse_timestamp(prior_thread["last_user_turn_at"]),
                    _parse_timestamp(prior_thread["expires_at"]),
                    self._history_import_pending(connection, prior_create["thread_id"]),
                )
            user = connection.execute(
                "SELECT role, is_active FROM auth_users WHERE id=?", (owner_user_id,)
            ).fetchone()
            source = connection.execute(
                "SELECT enabled FROM wren_data_sources WHERE id=?", (source_id,)
            ).fetchone()
            if user is None or not user["is_active"]:
                raise ThreadNotFound("thread unavailable")
            if source is None or not source["enabled"]:
                raise ThreadNotFound("source unavailable")
            if user["role"] == "member":
                grant = connection.execute(
                    "SELECT 1 FROM auth_user_data_sources WHERE user_id=? AND data_source_id=?",
                    (owner_user_id, source_id),
                ).fetchone()
                if grant is None:
                    raise ThreadGrantRevoked("source grant required")
            elif user["role"] != "admin":
                raise ThreadNotFound("thread unavailable")
            connection.execute(
                """INSERT INTO chat_thread_data_sources
                   (thread_id, data_source_id, owner_user_id, created_at)
                   VALUES (?, ?, ?, ?)""",
                (thread_id, source_id, owner_user_id, now.isoformat()),
            )
            connection.execute(
                """INSERT INTO agent_conversation_threads
                   (thread_id, status, created_at, last_user_turn_at, expires_at)
                   VALUES (?, 'active', ?, ?, ?)""",
                (thread_id, now.isoformat(), now.isoformat(), expires_at.isoformat()),
            )
            connection.execute(
                """INSERT INTO agent_thread_creation_requests
                   (owner_user_id, creation_key_hash, request_hash, thread_id, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (owner_user_id, creation_key_hash, request_hash, thread_id, now.isoformat()),
            )
            if history_import is not None:
                connection.execute(
                    """INSERT INTO agent_thread_history_imports
                       (thread_id, import_id, expected_chunk_count, expected_turn_count,
                        expected_content_bytes, expected_chunk_hashes_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        thread_id,
                        history_import.import_id,
                        len(history_import.chunk_hashes),
                        history_import.turn_count,
                        history_import.content_bytes,
                        json.dumps(history_import.chunk_hashes, separators=(",", ":")),
                        now.isoformat(),
                    ),
                )
            sequence = 1
            for index, (user_content, assistant_content) in enumerate(safe_history):
                turn_key = _opaque_key(f"legacy:{creation_key_hash}:{index}")
                user_hash = hashlib.sha256(user_content.encode("utf-8")).hexdigest()
                connection.execute(
                    """INSERT INTO agent_turn_requests
                       (thread_id, turn_id, request_hash, status, assistant_content,
                        created_at, updated_at, user_sequence, assistant_sequence)
                       VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?)""",
                    (
                        thread_id,
                        turn_key,
                        user_hash,
                        assistant_content,
                        now.isoformat(),
                        now.isoformat(),
                        sequence,
                        sequence + 1,
                    ),
                )
                connection.executemany(
                    """INSERT INTO agent_conversation_turns
                       (id, thread_id, sequence, role, turn_id, content, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        (uuid.uuid4().hex, thread_id, sequence, "user", turn_key, user_content, now.isoformat()),
                        (uuid.uuid4().hex, thread_id, sequence + 1, "assistant", turn_key, assistant_content, now.isoformat()),
                    ),
                )
                sequence += 2
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return ConversationThread(
            thread_id,
            source_id,
            now,
            now,
            expires_at,
            history_import is not None,
        )

    def list_threads(self, *, owner_user_id: str) -> tuple[ConversationThread, ...]:
        now = self._now()
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT b.thread_id, b.data_source_id, t.created_at,
                          t.last_user_turn_at, t.expires_at,
                          EXISTS(
                              SELECT 1 FROM agent_thread_history_imports AS history_import
                              WHERE history_import.thread_id=t.thread_id
                                AND history_import.completed_at IS NULL
                          ) AS history_import_pending
                   FROM chat_thread_data_sources AS b
                   JOIN agent_conversation_threads AS t USING(thread_id)
                   JOIN auth_users AS u ON u.id=b.owner_user_id
                   WHERE b.owner_user_id=? AND u.is_active=1
                     AND u.role IN ('admin', 'member')
                     AND t.status='active' AND t.expires_at>?
                   ORDER BY t.last_user_turn_at DESC, b.thread_id""",
                (owner_user_id, now.isoformat()),
            ).fetchall()
        finally:
            connection.close()
        return tuple(
            ConversationThread(
                row["thread_id"],
                row["data_source_id"],
                _parse_timestamp(row["created_at"]),
                _parse_timestamp(row["last_user_turn_at"]),
                _parse_timestamp(row["expires_at"]),
                bool(row["history_import_pending"]),
            )
            for row in rows
        )

    def append_legacy_history_import_chunk(
        self,
        *,
        thread_id: str,
        owner_user_id: str,
        import_id: str,
        chunk_index: int,
        turns: tuple[tuple[str, str], ...],
    ) -> HistoryImportChunkResult:
        from askdb_agent.application.conversation_memory import sanitize_turn_text

        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", import_id):
            raise ValueError("legacy history import id is invalid")
        if not turns or len(turns) > 500:
            raise ValueError("legacy history import chunk is empty or too large")
        raw_payload = json.dumps(
            [
                {"user_content": user, "assistant_content": assistant}
                for user, assistant in turns
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        request_hash = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
        content_bytes = sum(
            len(user.encode("utf-8")) + len(assistant.encode("utf-8"))
            for user, assistant in turns
        )
        now = self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._owned_thread(
                connection,
                thread_id,
                owner_user_id,
                require_source_grant=True,
            )
            descriptor = connection.execute(
                """SELECT import_id, expected_chunk_count, expected_turn_count,
                          expected_content_bytes, expected_chunk_hashes_json,
                          received_chunks, received_turn_count, received_content_bytes,
                          completed_at
                   FROM agent_thread_history_imports WHERE thread_id=?""",
                (thread_id,),
            ).fetchone()
            if descriptor is None or descriptor["import_id"] != import_id:
                raise ThreadHistoryImportConflict("history import is not registered")
            if not 0 <= chunk_index < descriptor["expected_chunk_count"]:
                raise ThreadHistoryImportConflict("history import chunk index is invalid")
            expected_hashes = json.loads(descriptor["expected_chunk_hashes_json"])
            if not isinstance(expected_hashes, list) or len(expected_hashes) != descriptor["expected_chunk_count"]:
                raise ThreadHistoryImportConflict("history import descriptor is corrupt")

            existing_chunk = connection.execute(
                """SELECT request_hash FROM agent_thread_history_import_chunks
                   WHERE thread_id=? AND import_id=? AND chunk_index=?""",
                (thread_id, import_id, chunk_index),
            ).fetchone()
            if existing_chunk is not None:
                if existing_chunk["request_hash"] != request_hash:
                    raise ThreadHistoryImportConflict("history import chunk changed on retry")
                connection.commit()
                return HistoryImportChunkResult(
                    descriptor["received_chunks"],
                    descriptor["expected_chunk_count"],
                    descriptor["completed_at"] is not None,
                    True,
                )
            if descriptor["completed_at"] is not None:
                raise ThreadHistoryImportConflict("history import is already complete")
            if chunk_index != descriptor["received_chunks"]:
                raise ThreadHistoryImportConflict("history import chunks must be sequential")
            if expected_hashes[chunk_index] != request_hash:
                raise ThreadHistoryImportConflict("history import chunk does not match its descriptor")

            received_chunks = descriptor["received_chunks"] + 1
            received_turn_count = descriptor["received_turn_count"] + len(turns)
            received_content_bytes = descriptor["received_content_bytes"] + content_bytes
            if (
                received_turn_count > descriptor["expected_turn_count"]
                or received_content_bytes > descriptor["expected_content_bytes"]
                or received_content_bytes > 2 * 1024 * 1024
            ):
                raise ThreadHistoryImportConflict("history import exceeds its declared bounds")
            completed = received_chunks == descriptor["expected_chunk_count"]
            if completed and (
                received_turn_count != descriptor["expected_turn_count"]
                or received_content_bytes != descriptor["expected_content_bytes"]
            ):
                raise ThreadHistoryImportConflict("history import does not match its declared totals")

            connection.execute(
                """INSERT INTO agent_thread_history_import_chunks
                   (thread_id, import_id, chunk_index, request_hash, turn_count,
                    content_bytes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    thread_id,
                    import_id,
                    chunk_index,
                    request_hash,
                    len(turns),
                    content_bytes,
                    now.isoformat(),
                ),
            )
            next_sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_conversation_turns WHERE thread_id=?",
                (thread_id,),
            ).fetchone()[0]
            for turn_index, (raw_user, raw_assistant) in enumerate(turns):
                user_content = sanitize_turn_text(raw_user, max_chars=8192)
                if not user_content:
                    continue
                assistant_content = sanitize_turn_text(raw_assistant, max_chars=8192)
                turn_key = _opaque_key(
                    f"legacy-import:{import_id}:{chunk_index}:{turn_index}"
                )
                user_hash = hashlib.sha256(user_content.encode("utf-8")).hexdigest()
                connection.execute(
                    """INSERT INTO agent_turn_requests
                       (thread_id, turn_id, request_hash, status, assistant_content,
                        created_at, updated_at, user_sequence, assistant_sequence)
                       VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?)""",
                    (
                        thread_id,
                        turn_key,
                        user_hash,
                        assistant_content,
                        now.isoformat(),
                        now.isoformat(),
                        next_sequence,
                        next_sequence + 1,
                    ),
                )
                connection.executemany(
                    """INSERT INTO agent_conversation_turns
                       (id, thread_id, sequence, role, turn_id, content, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        (
                            uuid.uuid4().hex,
                            thread_id,
                            next_sequence,
                            "user",
                            turn_key,
                            user_content,
                            now.isoformat(),
                        ),
                        (
                            uuid.uuid4().hex,
                            thread_id,
                            next_sequence + 1,
                            "assistant",
                            turn_key,
                            assistant_content,
                            now.isoformat(),
                        ),
                    ),
                )
                next_sequence += 2
            connection.execute(
                """UPDATE agent_thread_history_imports
                   SET received_chunks=?, received_turn_count=?, received_content_bytes=?,
                       completed_at=?
                   WHERE thread_id=? AND import_id=?""",
                (
                    received_chunks,
                    received_turn_count,
                    received_content_bytes,
                    now.isoformat() if completed else None,
                    thread_id,
                    import_id,
                ),
            )
            connection.commit()
            return HistoryImportChunkResult(
                received_chunks,
                descriptor["expected_chunk_count"],
                completed,
                False,
            )
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _owned_thread(
        self,
        connection: sqlite3.Connection,
        thread_id: str,
        owner_user_id: str,
        *,
        require_source_grant: bool,
        allow_expired: bool = False,
    ) -> sqlite3.Row:
        row = connection.execute(
            """SELECT b.data_source_id, b.owner_user_id, t.status, t.summary,
                      t.summary_version, t.created_at, t.last_user_turn_at,
                      t.expires_at, t.deleted_at
               FROM chat_thread_data_sources AS b
               JOIN agent_conversation_threads AS t USING(thread_id)
               WHERE b.thread_id=? AND b.owner_user_id=?""",
            (thread_id, owner_user_id),
        ).fetchone()
        if row is None or row["status"] != "active" or row["deleted_at"] is not None:
            raise ThreadNotFound("thread unavailable")
        if not allow_expired and _parse_timestamp(row["expires_at"]) <= self._now():
            raise ThreadNotFound("thread expired")
        if require_source_grant:
            account = connection.execute(
                "SELECT role, is_active FROM auth_users WHERE id=?", (owner_user_id,)
            ).fetchone()
            source = connection.execute(
                "SELECT enabled FROM wren_data_sources WHERE id=?", (row["data_source_id"],)
            ).fetchone()
            if account is None or not account["is_active"] or source is None or not source["enabled"]:
                raise ThreadGrantRevoked("source grant required")
            if account["role"] == "member" and connection.execute(
                """SELECT 1 FROM auth_user_data_sources
                   WHERE user_id=? AND data_source_id=?""",
                (owner_user_id, row["data_source_id"]),
            ).fetchone() is None:
                raise ThreadGrantRevoked("source grant required")
            if account["role"] not in {"admin", "member"}:
                raise ThreadGrantRevoked("source grant required")
        return row

    @staticmethod
    def _history_import_pending(connection: sqlite3.Connection, thread_id: str) -> bool:
        row = connection.execute(
            """SELECT completed_at FROM agent_thread_history_imports
               WHERE thread_id=?""",
            (thread_id,),
        ).fetchone()
        return row is not None and row["completed_at"] is None

    def _require_history_import_complete(
        self, connection: sqlite3.Connection, thread_id: str
    ) -> None:
        if self._history_import_pending(connection, thread_id):
            raise ThreadHistoryImportIncomplete("legacy history import is incomplete")

    def load_context(
        self,
        thread_id: str,
        *,
        owner_user_id: str,
        limit: int = 30,
    ) -> ThreadContext:
        if not 1 <= limit <= 200:
            raise ValueError("turn limit is outside the supported range")
        self.assert_journal_ready()
        connection = self._connect()
        try:
            row = self._owned_thread(
                connection,
                thread_id,
                owner_user_id,
                require_source_grant=True,
            )
            self._require_history_import_complete(connection, thread_id)
            turns = connection.execute(
                """SELECT turn.turn_id, turn.sequence, turn.role,
                          turn.content, turn.created_at
                   FROM agent_conversation_turns AS turn
                   JOIN agent_turn_requests AS request
                     ON request.thread_id=turn.thread_id
                    AND request.turn_id=turn.turn_id
                   WHERE turn.thread_id=? AND request.status='completed'
                   ORDER BY turn.sequence DESC LIMIT ?""",
                (thread_id, limit),
            ).fetchall()
            current_sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM agent_conversation_turns WHERE thread_id=?",
                (thread_id,),
            ).fetchone()[0]
        finally:
            connection.close()
        return ThreadContext(
            thread_id=thread_id,
            source_id=row["data_source_id"],
            current_sequence=current_sequence,
            summary=row["summary"],
            summary_version=row["summary_version"],
            turns=tuple(
                ConversationTurn(
                    turn_id=item["turn_id"],
                    sequence=item["sequence"],
                    role=item["role"],
                    content=item["content"],
                    created_at=_parse_timestamp(item["created_at"]),
                )
                for item in reversed(turns)
            ),
            expires_at=_parse_timestamp(row["expires_at"]),
        )

    def begin_turn(
        self,
        *,
        thread_id: str,
        owner_user_id: str,
        turn_id: str,
        user_content: str,
        expected_sequence: int | None = None,
    ) -> TurnStart:
        from askdb_agent.application.conversation_memory import sanitize_turn_text

        content = sanitize_turn_text(user_content)
        if not content:
            raise ValueError("user message is empty after sanitization")
        turn_key = _opaque_key(turn_id)
        request_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        now = self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._owned_thread(
                connection,
                thread_id,
                owner_user_id,
                require_source_grant=True,
            )
            self._require_history_import_complete(connection, thread_id)
            existing = connection.execute(
                """SELECT request_hash, status, assistant_content,
                          user_sequence, assistant_sequence, updated_at
                   FROM agent_turn_requests WHERE thread_id=? AND turn_id=?""",
                (thread_id, turn_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise TurnIdempotencyConflict("turn ID already used for another request")
                if (
                    existing["status"] == "running"
                    and _parse_timestamp(existing["updated_at"])
                    <= now - timedelta(minutes=15)
                ):
                    connection.execute(
                        """UPDATE agent_turn_requests SET status='failed', updated_at=?
                           WHERE thread_id=? AND turn_id=? AND status='running'""",
                        (now.isoformat(), thread_id, turn_key),
                    )
                    connection.commit()
                    return TurnStart(False, "failed", user_sequence=existing["user_sequence"])
                connection.commit()
                return TurnStart(
                    False,
                    existing["status"],
                    existing["assistant_content"],
                    existing["user_sequence"],
                    existing["assistant_sequence"],
                )
            running = connection.execute(
                """SELECT turn_id, updated_at FROM agent_turn_requests
                   WHERE thread_id=? AND status='running' LIMIT 1""",
                (thread_id,),
            ).fetchone()
            if running is not None:
                if _parse_timestamp(running["updated_at"]) <= now - timedelta(minutes=15):
                    connection.execute(
                        """UPDATE agent_turn_requests SET status='failed', updated_at=?
                           WHERE thread_id=? AND turn_id=? AND status='running'""",
                        (now.isoformat(), thread_id, running["turn_id"]),
                    )
                else:
                    raise TurnAlreadyRunning("another turn is already running for this thread")
            next_sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_conversation_turns WHERE thread_id=?",
                (thread_id,),
            ).fetchone()[0]
            current_sequence = next_sequence - 1
            if expected_sequence is not None and expected_sequence != current_sequence:
                raise TurnSequenceConflict("conversation sequence changed; reload thread history")
            connection.execute(
                """INSERT INTO agent_turn_requests
                   (thread_id, turn_id, request_hash, status, user_sequence, created_at, updated_at)
                   VALUES (?, ?, ?, 'running', ?, ?, ?)""",
                (thread_id, turn_key, request_hash, next_sequence, now.isoformat(), now.isoformat()),
            )
            connection.execute(
                """INSERT INTO agent_conversation_turns
                   (id, thread_id, sequence, role, turn_id, content, created_at)
                   VALUES (?, ?, ?, 'user', ?, ?, ?)""",
                (uuid.uuid4().hex, thread_id, next_sequence, turn_key, content, now.isoformat()),
            )
            expires_at = now + self.retention
            connection.execute(
                """UPDATE agent_conversation_threads
                   SET last_user_turn_at=?, expires_at=? WHERE thread_id=?""",
                (now.isoformat(), expires_at.isoformat(), thread_id),
            )
            connection.commit()
            return TurnStart(True, "running", user_sequence=next_sequence)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def complete_turn(
        self,
        *,
        thread_id: str,
        owner_user_id: str,
        turn_id: str,
        assistant_content: str,
    ) -> str:
        from askdb_agent.application.conversation_memory import sanitize_turn_text

        content = sanitize_turn_text(assistant_content)
        turn_key = _opaque_key(turn_id)
        now = self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._owned_thread(
                connection,
                thread_id,
                owner_user_id,
                require_source_grant=True,
            )
            request = connection.execute(
                """SELECT status, assistant_content FROM agent_turn_requests
                   WHERE thread_id=? AND turn_id=?""",
                (thread_id, turn_key),
            ).fetchone()
            if request is None:
                raise TurnNotFound("turn was not started")
            if request["status"] == "completed":
                connection.commit()
                return request["assistant_content"] or ""
            if request["status"] != "running":
                raise TurnNotFound("turn is not running")
            next_sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_conversation_turns WHERE thread_id=?",
                (thread_id,),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO agent_conversation_turns
                   (id, thread_id, sequence, role, turn_id, content, created_at)
                   VALUES (?, ?, ?, 'assistant', ?, ?, ?)""",
                (uuid.uuid4().hex, thread_id, next_sequence, turn_key, content, now.isoformat()),
            )
            connection.execute(
                """UPDATE agent_turn_requests
                   SET status='completed', assistant_sequence=?, assistant_content=?, updated_at=?
                   WHERE thread_id=? AND turn_id=?""",
                (next_sequence, content, now.isoformat(), thread_id, turn_key),
            )
            connection.commit()
            return content
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fail_turn(
        self,
        *,
        thread_id: str,
        owner_user_id: str,
        turn_id: str,
    ) -> bool:
        """Mark an interrupted turn failed without retaining its exception."""
        turn_key = _opaque_key(turn_id)
        now = self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._owned_thread(
                connection,
                thread_id,
                owner_user_id,
                require_source_grant=True,
            )
            cursor = connection.execute(
                """UPDATE agent_turn_requests SET status='failed', updated_at=?
                   WHERE thread_id=? AND turn_id=? AND status='running'""",
                (now.isoformat(), thread_id, turn_key),
            )
            connection.commit()
            return cursor.rowcount == 1
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fail_stale_turns(
        self,
        *,
        now: datetime | None = None,
        stale_after: timedelta = timedelta(minutes=15),
        limit: int = 500,
    ) -> int:
        moment = now or self._now()
        cutoff = (moment - stale_after).isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            stale = connection.execute(
                """SELECT thread_id, turn_id FROM agent_turn_requests
                   WHERE status='running' AND updated_at<=?
                   ORDER BY updated_at LIMIT ?""",
                (cutoff, limit),
            ).fetchall()
            for row in stale:
                connection.execute(
                    """UPDATE agent_turn_requests SET status='failed', updated_at=?
                       WHERE thread_id=? AND turn_id=? AND status='running'""",
                    (moment.isoformat(), row["thread_id"], row["turn_id"]),
                )
            connection.commit()
            return len(stale)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update_summary(
        self,
        *,
        thread_id: str,
        owner_user_id: str,
        expected_version: int,
        summary: str | None,
    ) -> bool:
        from askdb_agent.application.conversation_memory import sanitize_turn_text

        safe_summary = sanitize_turn_text(summary or "") or None
        now = self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._owned_thread(
                connection,
                thread_id,
                owner_user_id,
                require_source_grant=True,
            )
            cursor = connection.execute(
                """UPDATE agent_conversation_threads
                   SET summary=?, summary_version=summary_version+1
                   WHERE thread_id=? AND summary_version=? AND status='active'""",
                (safe_summary, thread_id, expected_version),
            )
            connection.commit()
            return cursor.rowcount == 1
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_deletion_impact(
        self,
        *,
        thread_id: str,
        owner_user_id: str,
        confirmation_ttl: timedelta = timedelta(minutes=5),
    ) -> ThreadDeletionImpact:
        from askdb_agent.integrations.deletion_journal import _canonical

        now = self._now()
        impact_version = uuid.uuid4().hex
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            thread = self._owned_thread(
                connection,
                thread_id,
                owner_user_id,
                require_source_grant=False,
                allow_expired=True,
            )
            rule_ids, labels = self._preview_linked_rules(
                connection, thread_id, thread["data_source_id"]
            )
            query_example_ids = self._preview_query_examples(
                connection, thread_id, thread["data_source_id"]
            )
            safe_labels = tuple(label[:160] for label in labels[:20])
            impact_hash = hashlib.sha256(
                _canonical(
                    {
                        "thread_id": thread_id,
                        "source_id": thread["data_source_id"],
                        "rule_ids": list(sorted(set(rule_ids))),
                        "rule_labels": list(safe_labels),
                        "query_example_ids": list(query_example_ids),
                    }
                )
            ).hexdigest()
            expires_at = now + confirmation_ttl
            connection.execute(
                """INSERT INTO agent_thread_deletion_impacts
                   (impact_version, thread_id, owner_user_id, data_source_id,
                    impact_hash, rule_count, rule_labels_json, query_example_count,
                    created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    impact_version,
                    thread_id,
                    owner_user_id,
                    thread["data_source_id"],
                    impact_hash,
                    len(set(rule_ids)),
                    __import__("json").dumps(safe_labels, ensure_ascii=False),
                    len(query_example_ids),
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            connection.commit()
            return ThreadDeletionImpact(
                impact_version,
                thread_id,
                thread["data_source_id"],
                len(set(rule_ids)),
                safe_labels,
                len(query_example_ids),
                expires_at,
            )
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def delete_thread(
        self,
        *,
        thread_id: str,
        owner_user_id: str,
        impact_version: str,
        idempotency_key: str,
        event_type: str = "thread_delete",
    ) -> ThreadDeletionOperation:
        from askdb_agent.integrations.deletion_journal import _canonical

        journal = self.deletion_journal
        if journal is None:
            raise ThreadDeletionJournalRequired("durable deletion journal is required")
        self.assert_journal_ready()
        now = self._now()
        idempotency_hash = _opaque_key(idempotency_key)
        request_hash = hashlib.sha256(
            _canonical(
                {
                    "thread_id": thread_id,
                    "impact_version": impact_version,
                    "confirmed": True,
                }
            )
        ).hexdigest()
        connection = self._connect()
        release_barrier: Callable[[], None] | None = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                """SELECT o.operation_id, o.thread_id, o.data_source_id,
                          o.journal_sequence, o.status, t.tombstone_until,
                          b.owner_user_id, o.request_hash
                   FROM agent_thread_deletion_operations AS o
                   JOIN agent_conversation_threads AS t USING(thread_id)
                   JOIN chat_thread_data_sources AS b USING(thread_id)
                   WHERE o.idempotency_key=?""",
                (idempotency_hash,),
            ).fetchone()
            if prior:
                if prior["thread_id"] != thread_id or prior["owner_user_id"] != owner_user_id:
                    raise ThreadNotFound("thread unavailable")
                # Pre-003 deletion rows have no request hash. The durable journal and
                # unique idempotency key still identify the already-applied operation;
                # replay it instead of making a retry irrecoverably conflict.
                if (
                    prior["request_hash"] is not None
                    and prior["request_hash"] != request_hash
                ):
                    raise ThreadDeletionConflict("idempotency key already used for a different request")
                connection.commit()
                return ThreadDeletionOperation(
                    prior["operation_id"],
                    prior["thread_id"],
                    prior["status"],
                    prior["journal_sequence"],
                    _parse_timestamp(prior["tombstone_until"]),
                )

            thread = self._owned_thread(
                connection,
                thread_id,
                owner_user_id,
                require_source_grant=False,
                allow_expired=True,
            )
            impact = connection.execute(
                """SELECT impact_hash, expires_at, consumed_at
                   FROM agent_thread_deletion_impacts
                   WHERE impact_version=? AND thread_id=? AND owner_user_id=?""",
                (impact_version, thread_id, owner_user_id),
            ).fetchone()
            if (
                impact is None
                or impact["consumed_at"] is not None
                or _parse_timestamp(impact["expires_at"]) <= now
            ):
                raise ThreadDeletionConflict("deletion confirmation expired; preview again")
            rule_ids, labels = self._preview_linked_rules(
                connection, thread_id, thread["data_source_id"]
            )
            query_example_ids = self._preview_query_examples(
                connection, thread_id, thread["data_source_id"]
            )
            safe_labels = tuple(label[:160] for label in labels[:20])
            current_hash = hashlib.sha256(
                _canonical(
                    {
                        "thread_id": thread_id,
                        "source_id": thread["data_source_id"],
                        "rule_ids": list(sorted(set(rule_ids))),
                        "rule_labels": list(safe_labels),
                        "query_example_ids": list(query_example_ids),
                    }
                )
            ).hexdigest()
            if current_hash != impact["impact_hash"]:
                raise ThreadDeletionConflict("deletion impact changed; preview again")

            event_id = f"thread-delete:{idempotency_hash}"
            if event_type not in {"thread_delete", "thread_expire"}:
                raise ValueError("unsupported thread deletion event")
            release_barrier = self._acquire_suppression_mutation_barrier(thread["data_source_id"])
            try:
                event = journal.append(
                    event_id=event_id,
                    event_type=event_type,
                    source_id=thread["data_source_id"],
                    thread_id=thread_id,
                    item_type="business_rule",
                    item_ids=tuple(sorted(set(rule_ids))),
                    request_hash=request_hash,
                    actor_id=(
                        owner_user_id
                        if event_type == "thread_delete"
                        else "system:thread-expiry"
                    ),
                    created_at=now,
                )
            except DeletionJournalUnavailable:
                raise
            self._apply_journal_event(connection, event)
            status = self._thread_deletion_status(
                connection, thread["data_source_id"], tuple(sorted(set(rule_ids)))
            )
            connection.execute(
                "UPDATE agent_thread_deletion_impacts SET consumed_at=? WHERE impact_version=?",
                (now.isoformat(), impact_version),
            )
            connection.commit()
            return ThreadDeletionOperation(
                event.event_id,
                thread_id,
                status,
                event.sequence,
                now + self.tombstone_retention,
            )
        except BaseException:
            connection.rollback()
            # A durable append with failed DB application must close memory recall
            # until reconciliation replays the missing contiguous journal prefix.
            try:
                if journal.high_water_mark() > self._applied_journal_sequence():
                    self._mark_journal_unhealthy("JOURNAL_REPLAY_REQUIRED")
            except DeletionJournalUnavailable:
                self._mark_journal_unhealthy("JOURNAL_INVALID")
            raise
        finally:
            if release_barrier is not None:
                release_barrier()
            connection.close()

    def revoke_business_rule(
        self,
        *,
        data_source_id: str,
        business_rule_id: str,
        actor_user_id: str,
        idempotency_key: str,
    ) -> MemoryRevocationOperation:
        from askdb_agent.integrations.deletion_journal import _canonical

        journal = self.deletion_journal
        if journal is None:
            raise ThreadDeletionJournalRequired("durable deletion journal is required")
        if len(business_rule_id) != 32 or any(
            character not in "0123456789abcdef" for character in business_rule_id.lower()
        ):
            raise ValueError("business rule ID is invalid")
        normalized_rule_id = business_rule_id.lower()
        self.assert_journal_ready()
        idempotency_hash = _opaque_key(idempotency_key)
        request_hash = hashlib.sha256(
            _canonical(
                {
                    "source_id": data_source_id,
                    "business_rule_id": normalized_rule_id,
                    "actor_user_id": actor_user_id,
                }
            )
        ).hexdigest()
        now = self._now()
        connection = self._connect()
        release_barrier: Callable[[], None] | None = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            actor = connection.execute(
                "SELECT role, is_active FROM auth_users WHERE id=?", (actor_user_id,)
            ).fetchone()
            if actor is None or not actor["is_active"] or actor["role"] != "admin":
                raise BusinessRuleForbidden("admin role required to revoke a shared rule")
            source = connection.execute(
                "SELECT enabled FROM wren_data_sources WHERE id=?", (data_source_id,)
            ).fetchone()
            candidate = connection.execute(
                """SELECT 1 FROM business_rule_candidates
                   WHERE data_source_id=? AND business_rule_id=?""",
                (data_source_id, normalized_rule_id),
            ).fetchone()
            origin = connection.execute(
                """SELECT 1 FROM business_rule_origins
                   WHERE data_source_id=? AND business_rule_id=?""",
                (data_source_id, normalized_rule_id),
            ).fetchone()
            if source is None or not source["enabled"] or (candidate is None and origin is None):
                raise ThreadNotFound("business rule unavailable")
            event_id = f"business-rule-revoke:{idempotency_hash}"
            release_barrier = self._acquire_suppression_mutation_barrier(data_source_id)
            event = journal.append(
                event_id=event_id,
                event_type="business_rule_revoke",
                source_id=data_source_id,
                item_type="business_rule",
                item_ids=(normalized_rule_id,),
                request_hash=request_hash,
                actor_id=actor_user_id,
                created_at=now,
            )
            self._apply_journal_event(connection, event)
            connection.commit()
            return MemoryRevocationOperation(
                event.event_id,
                data_source_id,
                normalized_rule_id,
                self._business_rule_removal_status(connection, data_source_id, normalized_rule_id),
                event.sequence,
            )
        except BaseException:
            connection.rollback()
            try:
                if journal.high_water_mark() > self._applied_journal_sequence():
                    self._mark_journal_unhealthy("JOURNAL_REPLAY_REQUIRED")
            except DeletionJournalUnavailable:
                self._mark_journal_unhealthy("JOURNAL_INVALID")
            raise
        finally:
            if release_barrier is not None:
                release_barrier()
            connection.close()

    def revoke_query_example(
        self,
        *,
        data_source_id: str,
        query_example_id: str,
        actor_user_id: str,
        idempotency_key: str,
    ) -> MemoryRevocationOperation:
        from askdb_agent.integrations.deletion_journal import _canonical

        journal = self.deletion_journal
        if journal is None:
            raise ThreadDeletionJournalRequired("durable deletion journal is required")
        if len(query_example_id) != 32 or any(
            character not in "0123456789abcdef" for character in query_example_id.lower()
        ):
            raise ValueError("query example ID is invalid")
        normalized_id = query_example_id.lower()
        self.assert_journal_ready()
        idempotency_hash = _opaque_key(idempotency_key)
        request_hash = hashlib.sha256(
            _canonical(
                {
                    "source_id": data_source_id,
                    "query_example_id": normalized_id,
                    "actor_user_id": actor_user_id,
                }
            )
        ).hexdigest()
        now = self._now()
        connection = self._connect()
        release_barrier: Callable[[], None] | None = None
        try:
            connection.execute("BEGIN IMMEDIATE")
            actor = connection.execute(
                "SELECT role, is_active FROM auth_users WHERE id=?", (actor_user_id,)
            ).fetchone()
            if actor is None or not actor["is_active"] or actor["role"] != "admin":
                raise BusinessRuleForbidden("admin role required to revoke a shared query example")
            source = connection.execute(
                "SELECT enabled FROM wren_data_sources WHERE id=?", (data_source_id,)
            ).fetchone()
            candidate = connection.execute(
                """SELECT 1 FROM query_example_candidates
                   WHERE data_source_id=? AND query_example_id=?""",
                (data_source_id, normalized_id),
            ).fetchone()
            if source is None or not source["enabled"] or candidate is None:
                raise ThreadNotFound("query example unavailable")
            release_barrier = self._acquire_suppression_mutation_barrier(data_source_id)
            event = journal.append(
                event_id=f"query-example-revoke:{idempotency_hash}",
                event_type="query_example_revoke",
                source_id=data_source_id,
                item_type="query_example",
                item_ids=(normalized_id,),
                request_hash=request_hash,
                actor_id=actor_user_id,
                created_at=now,
            )
            self._apply_journal_event(connection, event)
            connection.commit()
            return MemoryRevocationOperation(
                event.event_id, data_source_id, normalized_id, "suppressed", event.sequence
            )
        except BaseException:
            connection.rollback()
            try:
                if journal.high_water_mark() > self._applied_journal_sequence():
                    self._mark_journal_unhealthy("JOURNAL_REPLAY_REQUIRED")
            except DeletionJournalUnavailable:
                self._mark_journal_unhealthy("JOURNAL_INVALID")
            raise
        finally:
            if release_barrier is not None:
                release_barrier()
            connection.close()

    @staticmethod
    def _business_rule_removal_status(
        connection: sqlite3.Connection, source_id: str, business_rule_id: str
    ) -> str:
        exists = connection.execute(
            """SELECT 1 FROM business_rule_origins
               WHERE data_source_id=? AND business_rule_id=?
                 AND publication_status='removal_pending'""",
            (source_id, business_rule_id),
        ).fetchone()
        return "removal_pending" if exists else "completed_online"

    @classmethod
    def _thread_deletion_status(
        cls, connection: sqlite3.Connection, source_id: str, rule_ids: tuple[str, ...]
    ) -> str:
        if not rule_ids:
            return "completed_online"
        placeholders = ",".join("?" for _ in rule_ids)
        pending = connection.execute(
            f"""SELECT 1 FROM business_rule_origins
                WHERE data_source_id=? AND business_rule_id IN ({placeholders})
                  AND publication_status='removal_pending' LIMIT 1""",
            (source_id, *rule_ids),
        ).fetchone()
        return "suppressed" if pending else "completed_online"

    def _apply_journal_event(
        self, connection: sqlite3.Connection, event: JournalEvent
    ) -> None:
        state = connection.execute(
            "SELECT journal_applied_seq FROM agent_memory_journal_state WHERE id=1"
        ).fetchone()
        applied = state["journal_applied_seq"] if state else 0
        if event.sequence <= applied:
            return
        if event.sequence != applied + 1:
            raise DeletionJournalUnavailable("journal event is not the next contiguous sequence")
        if event.event_type in {"thread_delete", "thread_expire"} and event.thread_id:
            timestamp = event.created_at.isoformat()
            connection.execute(
                "DELETE FROM agent_conversation_turns WHERE thread_id=?",
                (event.thread_id,),
            )
            connection.execute(
                "DELETE FROM agent_turn_requests WHERE thread_id=?", (event.thread_id,)
            )
            connection.execute(
                """UPDATE agent_conversation_threads
                   SET status=?, summary=NULL, summary_version=summary_version+1,
                       deleted_at=?, tombstone_until=?
                   WHERE thread_id=?""",
                (
                    "expired" if event.event_type == "thread_expire" else "deleted",
                    timestamp,
                    (event.created_at + self.tombstone_retention).isoformat(),
                    event.thread_id,
                ),
            )
        for item_id in event.item_ids:
            connection.execute(
                """INSERT OR IGNORE INTO agent_memory_suppressions
                   (event_sequence, data_source_id, item_type, item_id, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    event.sequence,
                    event.source_id,
                    event.item_type,
                    item_id,
                    event.event_type,
                    event.created_at.isoformat(),
                ),
            )
        if event.event_type in {"thread_delete", "thread_expire"} and event.thread_id:
            if self.deletion_participant is not None:
                self.deletion_participant.apply(connection, event)
            elif self._has_rule_deletion_schema(connection):
                raise ThreadDeletionParticipantUnavailable(
                    "linked business-rule deletion participant is not configured"
                )
        elif event.event_type == "business_rule_revoke":
            if self.deletion_participant is not None:
                self.deletion_participant.apply(connection, event)
            elif self._has_rule_deletion_schema(connection):
                raise ThreadDeletionParticipantUnavailable(
                    "business-rule revocation participant is not configured"
                )
        if event.event_type in {"thread_delete", "thread_expire", "query_example_revoke"}:
            has_query_examples = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='query_example_candidates'"
            ).fetchone() is not None
            query_participants = [
                participant for participant in self.suppression_participants
                if callable(getattr(participant, "apply_suppression", None))
            ]
            if has_query_examples and not query_participants:
                raise ThreadDeletionParticipantUnavailable(
                    "query-example suppression participant is not configured"
                )
            for participant in query_participants:
                apply_suppression = getattr(participant, "apply_suppression", None)
                if callable(apply_suppression):
                    apply_suppression(connection, event)
        if event.event_type in {"thread_delete", "thread_expire"} and event.thread_id:
            key_prefix = "thread-delete:"
            idempotency_key = (
                event.event_id[len(key_prefix):]
                if event.event_id.startswith(key_prefix)
                else event.event_id
            )
            status = self._thread_deletion_status(
                connection, event.source_id, event.item_ids
            )
            connection.execute(
                """INSERT OR IGNORE INTO agent_thread_deletion_operations
                   (operation_id, idempotency_key, thread_id, data_source_id,
                    journal_sequence, request_hash, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.event_id,
                    idempotency_key,
                    event.thread_id,
                    event.source_id,
                    event.sequence,
                    event.request_hash,
                    status,
                    event.created_at.isoformat(),
                    self._now().isoformat(),
                ),
            )
        connection.execute(
            """UPDATE agent_memory_journal_state
               SET journal_applied_seq=?, healthy=1, last_error_code=NULL, updated_at=?
               WHERE id=1""",
            (event.sequence, self._now().isoformat()),
        )

    def _applied_journal_sequence(self) -> int:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT journal_applied_seq FROM agent_memory_journal_state WHERE id=1"
            ).fetchone()
            return int(row["journal_applied_seq"]) if row else 0
        finally:
            connection.close()

    def _mark_journal_unhealthy(self, code: str) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """UPDATE agent_memory_journal_state
                   SET healthy=0, last_error_code=?, updated_at=? WHERE id=1""",
                (code, self._now().isoformat()),
            )
        finally:
            connection.close()

    def reconcile_journal(self) -> int:
        journal = self.deletion_journal
        if journal is None:
            raise ThreadDeletionJournalRequired("durable deletion journal is required")
        try:
            state_connection = self._connect()
            try:
                state = state_connection.execute(
                    """SELECT journal_initialized, journal_id, journal_applied_seq
                       FROM agent_memory_journal_state WHERE id=1"""
                ).fetchone()
            finally:
                state_connection.close()
            if state is None or not state["journal_initialized"] or not state["journal_id"]:
                raise DeletionJournalUnavailable("database has no initialized journal identity")
            journal_id = journal.initialize(
                expected_journal_id=state["journal_id"], allow_create=False
            )
            events = journal.read_all()
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                state = connection.execute(
                    """SELECT journal_initialized, journal_id, journal_applied_seq
                       FROM agent_memory_journal_state WHERE id=1"""
                ).fetchone()
                if (
                    state is None
                    or not state["journal_initialized"]
                    or state["journal_id"] != journal_id
                ):
                    raise DeletionJournalUnavailable("journal identity changed during replay")
                applied = state["journal_applied_seq"] if state else 0
                if applied > len(events):
                    raise DeletionJournalUnavailable("database watermark is ahead of journal")
                for event in events[applied:]:
                    self._apply_journal_event(connection, event)
                connection.execute(
                    """UPDATE agent_memory_journal_state
                       SET healthy=1, last_error_code=NULL, updated_at=? WHERE id=1""",
                    (self._now().isoformat(),),
                )
                connection.commit()
                return events[-1].sequence if events else 0
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
        except Exception as exc:
            code = (
                "JOURNAL_INVALID"
                if isinstance(exc, DeletionJournalUnavailable)
                else "JOURNAL_REPLAY_FAILED"
            )
            self._mark_journal_unhealthy(code)
            raise

    def assert_journal_ready(self) -> None:
        if self.deletion_journal is None:
            connection = self._connect()
            try:
                state = connection.execute(
                    "SELECT journal_initialized FROM agent_memory_journal_state WHERE id=1"
                ).fetchone()
            finally:
                connection.close()
            if state and state["journal_initialized"]:
                self._mark_journal_unhealthy("JOURNAL_NOT_CONFIGURED")
                raise ThreadDeletionJournalRequired("durable deletion journal is unavailable")
            return
        self.reconcile_journal()

    def expire_inactive_threads(self, *, now: datetime | None = None, limit: int = 100) -> int:
        if self.deletion_journal is None:
            raise ThreadDeletionJournalRequired("durable deletion journal is required")
        moment = now or self._now()
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT b.thread_id, b.owner_user_id
                   FROM chat_thread_data_sources AS b
                   JOIN agent_conversation_threads AS t USING(thread_id)
                   WHERE t.status='active' AND t.expires_at<=?
                   ORDER BY t.expires_at LIMIT ?""",
                (moment.isoformat(), limit),
            ).fetchall()
        finally:
            connection.close()
        expired = 0
        for row in rows:
            try:
                impact = self.create_deletion_impact(
                    thread_id=row["thread_id"], owner_user_id=row["owner_user_id"]
                )
                self.delete_thread(
                    thread_id=row["thread_id"],
                    owner_user_id=row["owner_user_id"],
                    impact_version=impact.impact_version,
                    idempotency_key=f"expire:{row['thread_id']}:{impact.impact_version}",
                    event_type="thread_expire",
                )
                expired += 1
            except ThreadDeletionConflict:
                continue
        return expired

    def purge_expired_tombstones(
        self, *, now: datetime | None = None, limit: int = 100
    ) -> int:
        moment = now or self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT thread_id FROM agent_conversation_threads
                   WHERE status IN ('deleted', 'expired') AND tombstone_until<=?
                   ORDER BY tombstone_until LIMIT ?""",
                (moment.isoformat(), limit),
            ).fetchall()
            for row in rows:
                thread_id = row["thread_id"]
                connection.execute(
                    "DELETE FROM agent_thread_deletion_impacts WHERE thread_id=?",
                    (thread_id,),
                )
                connection.execute(
                    "DELETE FROM agent_thread_deletion_operations WHERE thread_id=?",
                    (thread_id,),
                )
                connection.execute(
                    "DELETE FROM chat_thread_data_sources WHERE thread_id=?",
                    (thread_id,),
                )
            connection.commit()
            return len(rows)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
