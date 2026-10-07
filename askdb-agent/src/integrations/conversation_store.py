from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from domain.conversation_memory import (
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
    ThreadCursorStale,
    ThreadGrantRevoked,
    ThreadHistoryImportConflict,
    ThreadHistoryImportIncomplete,
    ThreadNotFound,
    ThreadMetadataConflict,
    ThreadPage,
    ThreadState,
    ThreadStateConflict,
    TurnIdempotencyConflict,
    TurnAlreadyRunning,
    TurnNotFound,
    TurnSequenceConflict,
    TurnStart,
)
from domain.business_rules import BusinessRuleForbidden
from integrations.database import PostgresConnection, PostgresDatabase, PostgresRow
from integrations.deletion_journal import (
    DeletionJournalUnavailable,
    EncryptedDeletionJournal,
    JournalEvent,
)
from integrations.memory_deletion import BusinessRuleDeletionParticipant


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _opaque_key(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("idempotency key is invalid")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_UNSET = object()


def _thread_from_row(row: PostgresRow) -> ConversationThread:
    archived_at = row["archived_at"]
    return ConversationThread(
        thread_id=row["thread_id"],
        source_id=row["data_source_id"],
        created_at=_parse_timestamp(row["created_at"]),
        last_user_turn_at=_parse_timestamp(row["last_user_turn_at"]),
        history_import_pending=bool(row["history_import_pending"]),
        source_name=row["source_name"],
        title=row["title"],
        is_pinned=bool(row["is_pinned"]),
        archived_at=_parse_timestamp(archived_at) if archived_at else None,
        metadata_revision=row["metadata_revision"],
        record_status=row["status"],
    )


def _encode_thread_cursor(
    *, owner_user_id: str, view: str, query: str, limit: int, revision: int, offset: int
) -> str:
    payload = json.dumps(
        {
            "v": 1,
            "owner": owner_user_id,
            "view": view,
            "q": query,
            "limit": limit,
            "revision": revision,
            "offset": offset,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_thread_cursor(
    cursor: str,
    *, owner_user_id: str, view: str, query: str, limit: int, revision: int
) -> int:
    if not cursor or len(cursor) > 4096:
        raise ThreadCursorStale("thread-list cursor is invalid")
    try:
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise ThreadCursorStale("thread-list cursor is invalid") from None
    if not isinstance(payload, dict) or any(
        payload.get(key) != expected
        for key, expected in (
            ("v", 1),
            ("owner", owner_user_id),
            ("view", view),
            ("q", query),
            ("limit", limit),
            ("revision", revision),
        )
    ):
        raise ThreadCursorStale("thread-list cursor snapshot changed")
    offset = payload.get("offset")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ThreadCursorStale("thread-list cursor position is invalid")
    return offset


class ConversationMemoryStore:
    """Owner-scoped natural-language thread persistence in the Wren catalog DB."""

    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        tombstone_retention: timedelta = timedelta(days=30),
        deletion_journal: EncryptedDeletionJournal | None = None,
        rule_preview: Callable[[PostgresConnection, str, str], tuple[tuple[str, ...], tuple[str, ...]]] | None = None,
        deletion_participant: BusinessRuleDeletionParticipant | None = None,
        suppression_participants: tuple[Any, ...] = (),
    ) -> None:
        self.database = database or PostgresDatabase()
        self.clock = clock or (lambda: datetime.now(UTC))
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

    def _connect(self) -> PostgresConnection:
        return self.database.connect()

    @staticmethod
    def _bump_thread_list_revision(connection: PostgresConnection, owner_user_id: str) -> None:
        connection.execute(
            """INSERT INTO agent_thread_list_revisions(owner_user_id, revision)
               VALUES (%s, 2)
               ON CONFLICT(owner_user_id) DO UPDATE SET
                 revision=agent_thread_list_revisions.revision+1""",
            (owner_user_id,),
        )

    @staticmethod
    def _thread_list_revision(connection: PostgresConnection, owner_user_id: str) -> int:
        connection.execute(
            """INSERT INTO agent_thread_list_revisions(owner_user_id, revision)
               VALUES (%s, 1) ON CONFLICT(owner_user_id) DO NOTHING""",
            (owner_user_id,),
        )
        row = connection.execute(
            "SELECT revision FROM agent_thread_list_revisions WHERE owner_user_id=%s",
            (owner_user_id,),
        ).fetchone()
        return int(row["revision"])

    def _get_thread_projection(
        self,
        connection: PostgresConnection,
        thread_id: str,
        owner_user_id: str,
    ) -> ConversationThread:
        row = connection.execute(
            """SELECT b.thread_id, b.data_source_id, s.display_name AS source_name,
                      t.status, t.created_at, t.last_user_turn_at,
                      t.title, t.is_pinned, t.archived_at,
                      t.metadata_revision,
                      EXISTS(
                          SELECT 1 FROM agent_thread_history_imports AS history_import
                          WHERE history_import.thread_id=t.thread_id
                            AND history_import.completed_at IS NULL
                      ) AS history_import_pending
               FROM chat_thread_data_sources AS b
               JOIN agent_conversation_threads AS t USING(thread_id)
               LEFT JOIN wren_data_sources AS s ON s.id=b.data_source_id
               WHERE b.thread_id=%s AND b.owner_user_id=%s""",
            (thread_id, owner_user_id),
        ).fetchone()
        if row is None:
            raise ThreadNotFound("thread unavailable")
        return _thread_from_row(row)

    def initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """INSERT INTO agent_memory_journal_state
                   (id, journal_applied_seq, healthy, updated_at, journal_initialized)
                   VALUES (1, 0, 1, %s, 0) ON CONFLICT(id) DO NOTHING""",
                (self._now().isoformat(),),
            )
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
                       SET journal_initialized=1, journal_id=%s, healthy=0,
                           last_error_code='JOURNAL_REPLAY_REQUIRED', updated_at=%s
                       WHERE id=1""",
                    (journal_id, self._now().isoformat()),
                )
            finally:
                connection.close()
            self.reconcile_journal()
        except DeletionJournalUnavailable:
            self._mark_journal_unhealthy("JOURNAL_INVALID")
            raise

    def _has_table(self, connection: PostgresConnection, table: str) -> bool:
        row = connection.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (table,)).fetchone()
        return bool(row["present"])

    def _has_rule_deletion_schema(self, connection: PostgresConnection) -> bool:
        return self._has_table(connection, "business_rule_origins") or self._has_table(
            connection, "business_rule_candidates"
        )

    def assert_deletion_participant_ready(self) -> None:
        connection = self._connect()
        try:
            if self._has_rule_deletion_schema(connection) and self.deletion_participant is None:
                raise ThreadDeletionParticipantUnavailable(
                    "linked business-rule deletion participant is not configured"
                )
            has_query_examples = self._has_table(connection, "query_example_candidates")
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
        self, connection: PostgresConnection, thread_id: str, source_id: str
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
        self, connection: PostgresConnection, thread_id: str, source_id: str
    ) -> tuple[str, ...]:
        has_query_examples = self._has_table(connection, "query_example_candidates")
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
        from application.conversation_memory import sanitize_turn_text

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
        connection = self._connect()
        try:
            connection.acquire_write_lock()
            prior_create = connection.execute(
                """SELECT request_hash, thread_id FROM agent_thread_creation_requests
                   WHERE owner_user_id=%s AND creation_key_hash=%s""",
                (owner_user_id, creation_key_hash),
            ).fetchone()
            if prior_create is not None:
                if prior_create["request_hash"] != request_hash:
                    raise ThreadCreateIdempotencyConflict(
                        "thread creation key was reused with changed input"
                    )
                self._owned_thread(
                    connection,
                    prior_create["thread_id"],
                    owner_user_id,
                    require_source_grant=True,
                )
                prior_thread = self._get_thread_projection(
                    connection, prior_create["thread_id"], owner_user_id
                )
                connection.commit()
                return prior_thread
            user = connection.execute(
                "SELECT role, is_active FROM auth_users WHERE id=%s", (owner_user_id,)
            ).fetchone()
            source = connection.execute(
                "SELECT enabled, display_name FROM wren_data_sources WHERE id=%s", (source_id,)
            ).fetchone()
            if user is None or not user["is_active"]:
                raise ThreadNotFound("thread unavailable")
            if source is None or not source["enabled"]:
                raise ThreadNotFound("source unavailable")
            if user["role"] == "member":
                grant = connection.execute(
                    "SELECT 1 FROM auth_user_data_sources WHERE user_id=%s AND data_source_id=%s",
                    (owner_user_id, source_id),
                ).fetchone()
                if grant is None:
                    raise ThreadGrantRevoked("source grant required")
            elif user["role"] != "admin":
                raise ThreadNotFound("thread unavailable")
            connection.execute(
                """INSERT INTO chat_thread_data_sources
                   (thread_id, data_source_id, owner_user_id, created_at)
                   VALUES (%s, %s, %s, %s)""",
                (thread_id, source_id, owner_user_id, now.isoformat()),
            )
            connection.execute(
                """INSERT INTO agent_conversation_threads
                   (thread_id, status, created_at, last_user_turn_at)
                   VALUES (%s, 'active', %s, %s)""",
                (thread_id, now.isoformat(), now.isoformat()),
            )
            connection.execute(
                """INSERT INTO agent_thread_creation_requests
                   (owner_user_id, creation_key_hash, request_hash, thread_id, created_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (owner_user_id, creation_key_hash, request_hash, thread_id, now.isoformat()),
            )
            self._bump_thread_list_revision(connection, owner_user_id)
            if history_import is not None:
                connection.execute(
                    """INSERT INTO agent_thread_history_imports
                       (thread_id, import_id, expected_chunk_count, expected_turn_count,
                        expected_content_bytes, expected_chunk_hashes_json, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
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
                       VALUES (%s, %s, %s, 'completed', %s, %s, %s, %s, %s)""",
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
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        (uuid.uuid4().hex, thread_id, sequence, "user", turn_key, user_content, now.isoformat()),
                        (uuid.uuid4().hex, thread_id, sequence + 1, "assistant", turn_key, assistant_content, now.isoformat()),
                    ),
                )
                sequence += 2
            created_thread = self._get_thread_projection(connection, thread_id, owner_user_id)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return created_thread

    def list_thread_page(
        self,
        *,
        owner_user_id: str,
        view: str,
        q: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ThreadPage:
        if view not in {"recent", "archived"}:
            raise ValueError("thread list view is invalid")
        if not 1 <= limit <= 100:
            raise ValueError("thread page size is outside the supported range")
        normalized_query = (q or "").strip().casefold()
        if len(normalized_query) > 120:
            raise ValueError("thread search query is too long")
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            revision = self._thread_list_revision(connection, owner_user_id)
            offset = (
                _decode_thread_cursor(
                    cursor,
                    owner_user_id=owner_user_id,
                    view=view,
                    query=normalized_query,
                    limit=limit,
                    revision=revision,
                )
                if cursor
                else 0
            )
            view_filter = (
                "t.status='active' AND t.archived_at IS NULL"
                if view == "recent"
                else "t.status='active' AND t.archived_at IS NOT NULL"
            )
            ordering = (
                'askdb_casefold(COALESCE(s.display_name, \'\')) COLLATE "C", '
                "b.data_source_id, t.is_pinned DESC, t.last_user_turn_at DESC, b.thread_id"
                if view == "recent"
                else 'askdb_casefold(COALESCE(s.display_name, \'\')) COLLATE "C", '
                "b.data_source_id, t.archived_at DESC, b.thread_id"
            )
            rows = connection.execute(
                f"""SELECT b.thread_id, b.data_source_id,
                          s.display_name AS source_name, t.status, t.created_at,
                          t.last_user_turn_at, t.title, t.is_pinned,
                          t.archived_at,
                          t.metadata_revision,
                          EXISTS(
                              SELECT 1 FROM agent_thread_history_imports AS history_import
                              WHERE history_import.thread_id=t.thread_id
                                AND history_import.completed_at IS NULL
                          ) AS history_import_pending
                   FROM chat_thread_data_sources AS b
                   JOIN agent_conversation_threads AS t USING(thread_id)
                   JOIN auth_users AS u ON u.id=b.owner_user_id
                   LEFT JOIN wren_data_sources AS s ON s.id=b.data_source_id
                   WHERE b.owner_user_id=%s AND u.is_active=1
                     AND u.role IN ('admin', 'member') AND {view_filter}
                     AND (%s='' OR POSITION(%s IN askdb_casefold(COALESCE(t.title, ''))) > 0
                          OR POSITION(%s IN askdb_casefold(COALESCE(s.display_name, ''))) > 0)
                   ORDER BY {ordering} LIMIT %s OFFSET %s""",
                (
                    owner_user_id,
                    normalized_query,
                    normalized_query,
                    normalized_query,
                    limit + 1,
                    offset,
                ),
            ).fetchall()
            connection.commit()
        finally:
            connection.close()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        next_cursor = (
            _encode_thread_cursor(
                owner_user_id=owner_user_id,
                view=view,
                query=normalized_query,
                limit=limit,
                revision=revision,
                offset=offset + len(page_rows),
            )
            if has_more
            else None
        )
        return ThreadPage(
            tuple(_thread_from_row(row) for row in page_rows),
            next_cursor,
            revision,
        )

    def list_threads(self, *, owner_user_id: str) -> tuple[ConversationThread, ...]:
        """Return all current recent threads for existing in-process callers."""
        threads: list[ConversationThread] = []
        cursor: str | None = None
        while True:
            page = self.list_thread_page(
                owner_user_id=owner_user_id,
                view="recent",
                limit=100,
                cursor=cursor,
            )
            threads.extend(page.threads)
            cursor = page.next_cursor
            if cursor is None:
                return tuple(threads)

    def update_thread_metadata(
        self,
        *,
        thread_id: str,
        owner_user_id: str,
        expected_metadata_revision: int,
        title: str | None | object = _UNSET,
        is_pinned: bool | object = _UNSET,
    ) -> ConversationThread:
        if title is _UNSET and is_pinned is _UNSET:
            raise ValueError("thread metadata patch is empty")
        if title is not _UNSET:
            if title is not None and not isinstance(title, str):
                raise ValueError("thread title is invalid")
            title = title.strip() or None if isinstance(title, str) else None
            if isinstance(title, str) and len(title) > 120:
                raise ValueError("thread title is too long")
        if is_pinned is not _UNSET and not isinstance(is_pinned, bool):
            raise ValueError("thread pin state is invalid")

        connection = self._connect()
        try:
            connection.acquire_write_lock()
            current = self._get_thread_projection(connection, thread_id, owner_user_id)
            self._require_live_metadata_thread(current)
            if current.metadata_revision != expected_metadata_revision:
                raise ThreadMetadataConflict(current)

            updates: list[str] = []
            values: list[Any] = []
            changed_for_list = False
            if title is not _UNSET:
                updates.append("title=%s")
                values.append(title)
                changed_for_list = changed_for_list or title != current.title
            if is_pinned is not _UNSET:
                updates.append("is_pinned=%s")
                values.append(int(bool(is_pinned)))
                changed_for_list = changed_for_list or bool(is_pinned) != current.is_pinned
            if changed_for_list:
                updates.append("metadata_revision=metadata_revision+1")
                values.extend((thread_id, expected_metadata_revision))
                connection.execute(
                    f"UPDATE agent_conversation_threads SET {', '.join(updates)} "
                    "WHERE thread_id=%s AND metadata_revision=%s",
                    values,
                )
                self._bump_thread_list_revision(connection, owner_user_id)
            updated = self._get_thread_projection(connection, thread_id, owner_user_id)
            connection.commit()
            return updated
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _require_live_metadata_thread(thread: ConversationThread) -> None:
        if thread.record_status != "active":
            raise ThreadStateConflict(thread)

    def archive_thread(
        self,
        *,
        thread_id: str,
        owner_user_id: str,
        expected_metadata_revision: int,
    ) -> ConversationThread:
        now = self._now()
        connection = self._connect()
        try:
            connection.acquire_write_lock()
            current = self._get_thread_projection(connection, thread_id, owner_user_id)
            if current.metadata_revision != expected_metadata_revision:
                raise ThreadMetadataConflict(current)
            self._require_live_metadata_thread(current)
            if current.archived_at is not None:
                raise ThreadStateConflict(current)
            connection.execute(
                """UPDATE agent_conversation_threads
                   SET archived_at=%s, metadata_revision=metadata_revision+1
                   WHERE thread_id=%s AND metadata_revision=%s AND status='active'""",
                (now.isoformat(), thread_id, expected_metadata_revision),
            )
            self._bump_thread_list_revision(connection, owner_user_id)
            updated = self._get_thread_projection(connection, thread_id, owner_user_id)
            connection.commit()
            return updated
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def restore_thread(
        self,
        *,
        thread_id: str,
        owner_user_id: str,
        expected_metadata_revision: int,
    ) -> ConversationThread:
        connection = self._connect()
        try:
            connection.acquire_write_lock()
            current = self._get_thread_projection(connection, thread_id, owner_user_id)
            if current.metadata_revision != expected_metadata_revision:
                raise ThreadMetadataConflict(current)
            self._require_live_metadata_thread(current)
            if current.archived_at is None:
                raise ThreadStateConflict(current)
            connection.execute(
                """UPDATE agent_conversation_threads
                   SET archived_at=NULL, metadata_revision=metadata_revision+1
                   WHERE thread_id=%s AND metadata_revision=%s AND status='active'""",
                (thread_id, expected_metadata_revision),
            )
            self._bump_thread_list_revision(connection, owner_user_id)
            updated = self._get_thread_projection(connection, thread_id, owner_user_id)
            connection.commit()
            return updated
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_thread_states(
        self, *, owner_user_id: str, thread_ids: tuple[str, ...]
    ) -> tuple[ThreadState, ...]:
        if not 1 <= len(thread_ids) <= 200:
            raise ValueError("thread state batch size is outside the supported range")
        ids = tuple(dict.fromkeys(thread_ids))
        placeholders = ",".join("%s" for _ in ids)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"""SELECT b.thread_id, t.status, t.archived_at
                   FROM chat_thread_data_sources AS b
                   JOIN agent_conversation_threads AS t USING(thread_id)
                   WHERE b.owner_user_id=%s AND b.thread_id IN ({placeholders})""",
                (owner_user_id, *ids),
            ).fetchall()
        finally:
            connection.close()
        found: dict[str, str] = {}
        for row in rows:
            if row["status"] in {"deleted", "expired"}:
                status = "deleted"
            elif row["archived_at"] is not None:
                status = "archived"
            else:
                status = "active"
            found[row["thread_id"]] = status
        return tuple(ThreadState(thread_id, found.get(thread_id, "unavailable")) for thread_id in thread_ids)

    def append_legacy_history_import_chunk(
        self,
        *,
        thread_id: str,
        owner_user_id: str,
        import_id: str,
        chunk_index: int,
        turns: tuple[tuple[str, str], ...],
    ) -> HistoryImportChunkResult:
        from application.conversation_memory import sanitize_turn_text

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
            connection.acquire_write_lock()
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
                   FROM agent_thread_history_imports WHERE thread_id=%s""",
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
                   WHERE thread_id=%s AND import_id=%s AND chunk_index=%s""",
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
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
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
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_conversation_turns WHERE thread_id=%s",
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
                       VALUES (%s, %s, %s, 'completed', %s, %s, %s, %s, %s)""",
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
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
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
                   SET received_chunks=%s, received_turn_count=%s, received_content_bytes=%s,
                       completed_at=%s
                   WHERE thread_id=%s AND import_id=%s""",
                (
                    received_chunks,
                    received_turn_count,
                    received_content_bytes,
                    now.isoformat() if completed else None,
                    thread_id,
                    import_id,
                ),
            )
            if completed:
                self._bump_thread_list_revision(connection, owner_user_id)
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
        connection: PostgresConnection,
        thread_id: str,
        owner_user_id: str,
        *,
        require_source_grant: bool,
    ) -> PostgresRow:
        row = connection.execute(
            """SELECT b.data_source_id, b.owner_user_id, s.display_name AS source_name,
                      t.status, t.summary,
                      t.summary_version, t.created_at, t.last_user_turn_at,
                      t.deleted_at, t.archived_at, t.title, t.is_pinned,
                      t.metadata_revision
               FROM chat_thread_data_sources AS b
               JOIN agent_conversation_threads AS t USING(thread_id)
               LEFT JOIN wren_data_sources AS s ON s.id=b.data_source_id
               WHERE b.thread_id=%s AND b.owner_user_id=%s""",
            (thread_id, owner_user_id),
        ).fetchone()
        if row is None or row["status"] != "active" or row["deleted_at"] is not None:
            raise ThreadNotFound("thread unavailable")
        if require_source_grant:
            account = connection.execute(
                "SELECT role, is_active FROM auth_users WHERE id=%s", (owner_user_id,)
            ).fetchone()
            source = connection.execute(
                "SELECT enabled FROM wren_data_sources WHERE id=%s", (row["data_source_id"],)
            ).fetchone()
            if account is None or not account["is_active"] or source is None or not source["enabled"]:
                raise ThreadGrantRevoked("source grant required")
            if account["role"] == "member" and connection.execute(
                """SELECT 1 FROM auth_user_data_sources
                   WHERE user_id=%s AND data_source_id=%s""",
                (owner_user_id, row["data_source_id"]),
            ).fetchone() is None:
                raise ThreadGrantRevoked("source grant required")
            if account["role"] not in {"admin", "member"}:
                raise ThreadGrantRevoked("source grant required")
        return row

    @staticmethod
    def _history_import_pending(connection: PostgresConnection, thread_id: str) -> bool:
        row = connection.execute(
            """SELECT completed_at FROM agent_thread_history_imports
               WHERE thread_id=%s""",
            (thread_id,),
        ).fetchone()
        return row is not None and row["completed_at"] is None

    def _require_history_import_complete(
        self, connection: PostgresConnection, thread_id: str
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
                          turn.content, turn.created_at, request.personal_events
                   FROM agent_conversation_turns AS turn
                   JOIN agent_turn_requests AS request
                     ON request.thread_id=turn.thread_id
                    AND request.turn_id=turn.turn_id
                   WHERE turn.thread_id=%s AND request.status='completed'
                   ORDER BY turn.sequence DESC LIMIT %s""",
                (thread_id, limit),
            ).fetchall()
            current_sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM agent_conversation_turns WHERE thread_id=%s",
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
                    personal_events=tuple(item['personal_events']) if item['role']=='assistant' else (),
                )
                for item in reversed(turns)
            ),
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
        from application.conversation_memory import sanitize_turn_text

        content = sanitize_turn_text(user_content)
        if not content:
            raise ValueError("user message is empty after sanitization")
        turn_key = _opaque_key(turn_id)
        request_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        now = self._now()
        connection = self._connect()
        try:
            connection.acquire_write_lock()
            owned_thread = self._owned_thread(
                connection,
                thread_id,
                owner_user_id,
                require_source_grant=True,
            )
            self._require_history_import_complete(connection, thread_id)
            existing = connection.execute(
                """SELECT request_hash, status, assistant_content,
                          user_sequence, assistant_sequence, updated_at
                   FROM agent_turn_requests WHERE thread_id=%s AND turn_id=%s""",
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
                        """UPDATE agent_turn_requests SET status='failed', updated_at=%s
                           WHERE thread_id=%s AND turn_id=%s AND status='running'""",
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
                   WHERE thread_id=%s AND status='running' LIMIT 1""",
                (thread_id,),
            ).fetchone()
            if running is not None:
                if _parse_timestamp(running["updated_at"]) <= now - timedelta(minutes=15):
                    connection.execute(
                        """UPDATE agent_turn_requests SET status='failed', updated_at=%s
                           WHERE thread_id=%s AND turn_id=%s AND status='running'""",
                        (now.isoformat(), thread_id, running["turn_id"]),
                    )
                else:
                    raise TurnAlreadyRunning("another turn is already running for this thread")
            next_sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_conversation_turns WHERE thread_id=%s",
                (thread_id,),
            ).fetchone()[0]
            current_sequence = next_sequence - 1
            if expected_sequence is not None and expected_sequence != current_sequence:
                raise TurnSequenceConflict("conversation sequence changed; reload thread history")
            connection.execute(
                """INSERT INTO agent_turn_requests
                   (thread_id, turn_id, request_hash, status, user_sequence, created_at, updated_at)
                   VALUES (%s, %s, %s, 'running', %s, %s, %s)""",
                (thread_id, turn_key, request_hash, next_sequence, now.isoformat(), now.isoformat()),
            )
            connection.execute(
                """INSERT INTO agent_conversation_turns
                   (id, thread_id, sequence, role, turn_id, content, created_at)
                   VALUES (%s, %s, %s, 'user', %s, %s, %s)""",
                (uuid.uuid4().hex, thread_id, next_sequence, turn_key, content, now.isoformat()),
            )
            connection.execute(
                """UPDATE agent_conversation_threads
                   SET last_user_turn_at=%s WHERE thread_id=%s""",
                (now.isoformat(), thread_id),
            )
            self._bump_thread_list_revision(connection, owner_user_id)
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
        analysis_descriptor: dict | None = None,
        personal_events: list | None = None,
    ) -> str:
        from application.conversation_memory import sanitize_turn_text

        content = sanitize_turn_text(assistant_content)
        turn_key = _opaque_key(turn_id)
        now = self._now()
        connection = self._connect()
        try:
            connection.acquire_write_lock()
            self._owned_thread(
                connection,
                thread_id,
                owner_user_id,
                require_source_grant=True,
            )
            request = connection.execute(
                """SELECT status, assistant_content FROM agent_turn_requests
                   WHERE thread_id=%s AND turn_id=%s""",
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
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_conversation_turns WHERE thread_id=%s",
                (thread_id,),
            ).fetchone()[0]
            connection.execute(
                """INSERT INTO agent_conversation_turns
                   (id, thread_id, sequence, role, turn_id, content, created_at)
                   VALUES (%s, %s, %s, 'assistant', %s, %s, %s)""",
                (uuid.uuid4().hex, thread_id, next_sequence, turn_key, content, now.isoformat()),
            )
            connection.execute(
                """UPDATE agent_turn_requests
                   SET status='completed', assistant_sequence=%s, assistant_content=%s, updated_at=%s
                   WHERE thread_id=%s AND turn_id=%s""",
                (next_sequence, content, now.isoformat(), thread_id, turn_key),
            )
            if analysis_descriptor is not None:
                descriptor = json.dumps(analysis_descriptor,ensure_ascii=False)
                if len(descriptor) > 6000:
                    raise ValueError('analysis descriptor too large')
                connection.execute('UPDATE agent_turn_requests SET analysis_descriptor=%s::jsonb WHERE thread_id=%s AND turn_id=%s', (descriptor,thread_id,turn_key))
            if personal_events:
                connection.execute('UPDATE agent_turn_requests SET personal_events=%s::jsonb WHERE thread_id=%s AND turn_id=%s', (json.dumps(personal_events[:16]),thread_id,turn_key))
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
            connection.acquire_write_lock()
            self._owned_thread(
                connection,
                thread_id,
                owner_user_id,
                require_source_grant=True,
            )
            cursor = connection.execute(
                """UPDATE agent_turn_requests SET status='failed', updated_at=%s
                   WHERE thread_id=%s AND turn_id=%s AND status='running'""",
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
            connection.acquire_write_lock()
            stale = connection.execute(
                """SELECT thread_id, turn_id FROM agent_turn_requests
                   WHERE status='running' AND updated_at<=%s
                   ORDER BY updated_at LIMIT %s""",
                (cutoff, limit),
            ).fetchall()
            for row in stale:
                connection.execute(
                    """UPDATE agent_turn_requests SET status='failed', updated_at=%s
                       WHERE thread_id=%s AND turn_id=%s AND status='running'""",
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
        from application.conversation_memory import sanitize_turn_text

        safe_summary = sanitize_turn_text(summary or "") or None
        now = self._now()
        connection = self._connect()
        try:
            connection.acquire_write_lock()
            self._owned_thread(
                connection,
                thread_id,
                owner_user_id,
                require_source_grant=True,
            )
            cursor = connection.execute(
                """UPDATE agent_conversation_threads
                   SET summary=%s, summary_version=summary_version+1
                   WHERE thread_id=%s AND summary_version=%s AND status='active'""",
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
        from integrations.deletion_journal import _canonical

        now = self._now()
        impact_version = uuid.uuid4().hex
        connection = self._connect()
        try:
            connection.acquire_write_lock()
            thread = self._owned_thread(
                connection,
                thread_id,
                owner_user_id,
                require_source_grant=False,
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
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
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
    ) -> ThreadDeletionOperation:
        from integrations.deletion_journal import _canonical

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
            connection.acquire_write_lock()
            prior = connection.execute(
                """SELECT o.operation_id, o.thread_id, o.data_source_id,
                          o.journal_sequence, o.status, t.tombstone_until,
                          b.owner_user_id, o.request_hash
                   FROM agent_thread_deletion_operations AS o
                   JOIN agent_conversation_threads AS t USING(thread_id)
                   JOIN chat_thread_data_sources AS b USING(thread_id)
                   WHERE o.idempotency_key=%s""",
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
            )
            impact = connection.execute(
                """SELECT impact_hash, expires_at, consumed_at
                   FROM agent_thread_deletion_impacts
                   WHERE impact_version=%s AND thread_id=%s AND owner_user_id=%s""",
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
            release_barrier = self._acquire_suppression_mutation_barrier(thread["data_source_id"])
            try:
                event = journal.append(
                    event_id=event_id,
                    event_type="thread_delete",
                    source_id=thread["data_source_id"],
                    thread_id=thread_id,
                    item_type="business_rule",
                    item_ids=tuple(sorted(set(rule_ids))),
                    request_hash=request_hash,
                    actor_id=owner_user_id,
                    created_at=now,
                )
            except DeletionJournalUnavailable:
                raise
            self._apply_journal_event(connection, event)
            status = self._thread_deletion_status(
                connection, thread["data_source_id"], tuple(sorted(set(rule_ids)))
            )
            connection.execute(
                "UPDATE agent_thread_deletion_impacts SET consumed_at=%s WHERE impact_version=%s",
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
        from integrations.deletion_journal import _canonical

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
            connection.acquire_write_lock()
            actor = connection.execute(
                "SELECT role, is_active FROM auth_users WHERE id=%s", (actor_user_id,)
            ).fetchone()
            if actor is None or not actor["is_active"] or actor["role"] != "admin":
                raise BusinessRuleForbidden("admin role required to revoke a shared rule")
            source = connection.execute(
                "SELECT enabled FROM wren_data_sources WHERE id=%s", (data_source_id,)
            ).fetchone()
            candidate = connection.execute(
                """SELECT 1 FROM business_rule_candidates
                   WHERE data_source_id=%s AND business_rule_id=%s""",
                (data_source_id, normalized_rule_id),
            ).fetchone()
            origin = connection.execute(
                """SELECT 1 FROM business_rule_origins
                   WHERE data_source_id=%s AND business_rule_id=%s""",
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
        from integrations.deletion_journal import _canonical

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
            connection.acquire_write_lock()
            actor = connection.execute(
                "SELECT role, is_active FROM auth_users WHERE id=%s", (actor_user_id,)
            ).fetchone()
            if actor is None or not actor["is_active"] or actor["role"] != "admin":
                raise BusinessRuleForbidden("admin role required to revoke a shared query example")
            source = connection.execute(
                "SELECT enabled FROM wren_data_sources WHERE id=%s", (data_source_id,)
            ).fetchone()
            candidate = connection.execute(
                """SELECT 1 FROM query_example_candidates
                   WHERE data_source_id=%s AND query_example_id=%s""",
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
        connection: PostgresConnection, source_id: str, business_rule_id: str
    ) -> str:
        exists = connection.execute(
            """SELECT 1 FROM business_rule_origins
               WHERE data_source_id=%s AND business_rule_id=%s
                 AND publication_status='removal_pending'""",
            (source_id, business_rule_id),
        ).fetchone()
        return "removal_pending" if exists else "completed_online"

    @classmethod
    def _thread_deletion_status(
        cls, connection: PostgresConnection, source_id: str, rule_ids: tuple[str, ...]
    ) -> str:
        if not rule_ids:
            return "completed_online"
        placeholders = ",".join("%s" for _ in rule_ids)
        pending = connection.execute(
            f"""SELECT 1 FROM business_rule_origins
                WHERE data_source_id=%s AND business_rule_id IN ({placeholders})
                  AND publication_status='removal_pending' LIMIT 1""",
            (source_id, *rule_ids),
        ).fetchone()
        return "suppressed" if pending else "completed_online"

    def _apply_journal_event(
        self, connection: PostgresConnection, event: JournalEvent
    ) -> None:
        state = connection.execute(
            "SELECT journal_applied_seq FROM agent_memory_journal_state WHERE id=1"
        ).fetchone()
        applied = state["journal_applied_seq"] if state else 0
        if event.sequence <= applied:
            return
        if event.sequence != applied + 1:
            raise DeletionJournalUnavailable("journal event is not the next contiguous sequence")
        if event.event_type == "thread_delete" and event.thread_id:
            timestamp = event.created_at.isoformat()
            owner = connection.execute(
                "SELECT owner_user_id FROM chat_thread_data_sources WHERE thread_id=%s",
                (event.thread_id,),
            ).fetchone()
            connection.execute(
                "DELETE FROM agent_conversation_turns WHERE thread_id=%s",
                (event.thread_id,),
            )
            connection.execute(
                "DELETE FROM agent_turn_requests WHERE thread_id=%s", (event.thread_id,)
            )
            connection.execute(
                """UPDATE agent_conversation_threads
                   SET status=%s, summary=NULL, summary_version=summary_version+1,
                       deleted_at=%s, tombstone_until=%s
                   WHERE thread_id=%s""",
                (
                    "deleted",
                    timestamp,
                    (event.created_at + self.tombstone_retention).isoformat(),
                    event.thread_id,
                ),
            )
            if owner is not None:
                self._bump_thread_list_revision(connection, owner["owner_user_id"])
        for item_id in event.item_ids:
            connection.execute(
                """INSERT INTO agent_memory_suppressions
                   (event_sequence, data_source_id, item_type, item_id, reason, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT(event_sequence, item_type, item_id) DO NOTHING""",
                (
                    event.sequence,
                    event.source_id,
                    event.item_type,
                    item_id,
                    event.event_type,
                    event.created_at.isoformat(),
                ),
            )
        if event.event_type == "thread_delete" and event.thread_id:
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
        if event.event_type in {"thread_delete", "query_example_revoke"}:
            has_query_examples = self._has_table(connection, "query_example_candidates")
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
        if event.event_type == "thread_delete" and event.thread_id:
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
                """INSERT INTO agent_thread_deletion_operations
                   (operation_id, idempotency_key, thread_id, data_source_id,
                    journal_sequence, request_hash, status, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT(idempotency_key) DO NOTHING""",
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
               SET journal_applied_seq=%s, healthy=1, last_error_code=NULL, updated_at=%s
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
                   SET healthy=0, last_error_code=%s, updated_at=%s WHERE id=1""",
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
                connection.acquire_write_lock()
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
                       SET healthy=1, last_error_code=NULL, updated_at=%s WHERE id=1""",
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

    def purge_expired_tombstones(
        self, *, now: datetime | None = None, limit: int = 100
    ) -> int:
        moment = now or self._now()
        connection = self._connect()
        try:
            connection.acquire_write_lock()
            rows = connection.execute(
                """SELECT thread_id FROM agent_conversation_threads
                   WHERE status IN ('deleted', 'expired') AND tombstone_until<=%s
                   ORDER BY tombstone_until LIMIT %s""",
                (moment.isoformat(), limit),
            ).fetchall()
            for row in rows:
                thread_id = row["thread_id"]
                connection.execute(
                    "DELETE FROM agent_thread_deletion_impacts WHERE thread_id=%s",
                    (thread_id,),
                )
                connection.execute(
                    "DELETE FROM agent_thread_deletion_operations WHERE thread_id=%s",
                    (thread_id,),
                )
                connection.execute(
                    "DELETE FROM chat_thread_data_sources WHERE thread_id=%s",
                    (thread_id,),
                )
            connection.commit()
            return len(rows)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
