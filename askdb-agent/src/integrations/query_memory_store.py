from __future__ import annotations

import hashlib
import base64
import json
import math
import os
import re
import threading
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from domain.memory_recall import (
    PublicationStatus,
    QueryExample,
    QueryParameterSpec,
    QueryParameterType,
    ReviewStatus,
    normalize_question,
    query_example_content_hash,
    verify_query_example_content_hash,
)
from domain.query_memory import QueryCorpusRevision, QueryExampleCandidate
from integrations.database import PostgresConnection, PostgresDatabase, PostgresRow
from integrations.deletion_journal import EncryptedDeletionJournal, JournalEvent


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _iso(value: datetime) -> str:
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat()


class QueryMemoryError(RuntimeError):
    code = "QUERY_MEMORY_ERROR"


class QueryMemoryForbidden(QueryMemoryError):
    code = "QUERY_MEMORY_FORBIDDEN"


class QueryMemoryNotFound(QueryMemoryError):
    code = "QUERY_EXAMPLE_NOT_FOUND"


class QueryMemoryConflict(QueryMemoryError):
    code = "QUERY_EXAMPLE_STATE_CHANGED"


class QueryMemoryStaleSource(QueryMemoryError):
    code = "QUERY_EXAMPLE_SOURCE_STALE"


class QueryMemoryValidationError(QueryMemoryError):
    code = "QUERY_EXAMPLE_INPUT_INVALID"


class QueryMemoryUnavailable(QueryMemoryError):
    code = "QUERY_MEMORY_UNAVAILABLE"


class QueryMemoryQuotaExceeded(QueryMemoryError):
    code = "MEMORY_CANDIDATE_QUOTA_EXCEEDED"

    def __init__(self, retry_after: int):
        super().__init__("memory candidate quota exceeded")
        self.retry_after = max(1, retry_after)


class QueryMemoryStore:
    """Source-scoped query-example governance and immutable corpus artifacts."""

    MAX_PENDING_PER_SUBMITTER_SOURCE = 20
    MAX_PENDING_PER_THREAD = 20
    MAX_NEW_CANDIDATES_PER_HOUR = 10
    CANDIDATE_TTL = timedelta(days=90)
    REVISION_TTL = timedelta(days=30)

    def __init__(
        self,
        database: PostgresDatabase | None = None,
        *,
        corpus_root: Path | None = None,
        clock: Any = None,
        deletion_journal: EncryptedDeletionJournal | None = None,
    ) -> None:
        self.database = database or PostgresDatabase()
        configured_root = os.environ.get("ASKDB_AGENT_MEMORY_CORPUS_DIR", "").strip()
        self.corpus_root = Path(
            corpus_root
            or configured_root
            or Path(__file__).resolve().parents[2] / "data" / "agent-memory-corpus"
        ).expanduser().resolve()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.deletion_journal = deletion_journal
        self._source_locks: dict[str, threading.RLock] = {}
        self._source_locks_guard = threading.Lock()

    def _now(self) -> datetime:
        value = self.clock()
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def _connect(self) -> PostgresConnection:
        return self.database.connect()

    def initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("SELECT 1 FROM query_corpus_revisions LIMIT 1")
        finally:
            connection.close()

    def _source_lock(self, source_id: str) -> threading.RLock:
        with self._source_locks_guard:
            return self._source_locks.setdefault(source_id, threading.RLock())

    @staticmethod
    def _parameters(raw: str | None) -> tuple[QueryParameterSpec, ...]:
        if not raw:
            return ()
        try:
            payload = json.loads(raw)
            if not isinstance(payload, list) or len(payload) > 32:
                raise ValueError
            result: list[QueryParameterSpec] = []
            seen: set[str] = set()
            for item in payload:
                if not isinstance(item, dict):
                    raise ValueError
                name = item.get("name")
                value_type = item.get("type")
                nullable = item.get("nullable", False)
                if (
                    not isinstance(name, str)
                    or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name)
                    or name in seen
                    or not isinstance(nullable, bool)
                ):
                    raise ValueError
                result.append(QueryParameterSpec(name, QueryParameterType(value_type), nullable))
                seen.add(name)
            return tuple(result)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("stored query example parameter metadata is invalid") from exc

    @classmethod
    def _candidate(cls, row: PostgresRow) -> QueryExampleCandidate:
        try:
            source_turn_id = uuid.UUID(row["source_turn_id"]) if row["source_turn_id"] else None
            return QueryExampleCandidate(
                id=uuid.UUID(hex=row["query_example_id"]),
                data_source_id=row["data_source_id"],
                source_thread_id=row["source_thread_id"],
                source_turn_id=source_turn_id,
                normalized_question=row["normalized_question"],
                sql_template=row["sql_template"],
                parameter_specs=cls._parameters(row["parameter_specs_json"]),
                connector_type=row["connector_type"],
                wren_revision_id=row["wren_revision_id"],
                mdl_digest=row["mdl_digest"],
                content_hash=row["content_hash"],
                submitted_by=row["submitted_by"],
                review_status=ReviewStatus(row["review_status"]),
                publication_status=PublicationStatus(row["publication_status"]),
                reviewed_by=row["reviewed_by"],
                review_reason_code=row["review_reason_code"],
                version=int(row["version"]),
                created_at=_parse_time(row["created_at"]),
                reviewed_at=_parse_time(row["reviewed_at"]),
                activated_at=_parse_time(row["activated_at"]),
                superseded_at=_parse_time(row["superseded_at"]),
                revoked_at=_parse_time(row["revoked_at"]),
                expires_at=_parse_time(row["expires_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("stored query example record is invalid") from exc

    @staticmethod
    def _hash_example(
        *, data_source_id: str, question: str, sql_template: str,
        parameter_specs: tuple[QueryParameterSpec, ...], connector_type: str,
        wren_revision_id: str, mdl_digest: str,
    ) -> str:
        placeholder = QueryExample(
            id=uuid.UUID(int=0), data_source_id=data_source_id,
            normalized_question=question, sql_template=sql_template,
            connector_type=connector_type, wren_revision_id=wren_revision_id,
            mdl_digest=mdl_digest, review_status=ReviewStatus.PENDING,
            publication_status=PublicationStatus.NOT_PUBLISHED,
            publication_operation_id=None, content_hash="", source_turn_id=None,
            submitted_by="", reviewed_by=None, created_at=datetime(1970, 1, 1, tzinfo=UTC),
            reviewed_at=None, activated_at=None, superseded_at=None, revoked_at=None,
            parameter_specs=parameter_specs,
        )
        return query_example_content_hash(placeholder)

    @staticmethod
    def _record_event(
        connection: PostgresConnection,
        *, example_id: str, source_id: str, actor_id: str, event_type: str,
        previous_review: str | None, review: str, previous_publication: str | None,
        publication: str, content_hash: str, reason: str, now: datetime,
        event_id: str | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO query_example_candidate_events
               (event_id, query_example_id, data_source_id, actor_user_id, event_type,
                previous_review_status, review_status, previous_publication_status,
                publication_status, content_hash, reason_code, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
            (event_id or uuid.uuid4().hex, example_id, source_id, actor_id, event_type,
             previous_review, review, previous_publication, publication,
             content_hash, reason, _iso(now)),
        )

    @staticmethod
    def _assert_actor_source(
        connection: PostgresConnection, *, actor_id: str, source_id: str, admin_only: bool = False
    ) -> PostgresRow:
        source = connection.execute(
            "SELECT enabled FROM wren_data_sources WHERE id=%s", (source_id,)
        ).fetchone()
        actor = connection.execute(
            "SELECT role, is_active FROM auth_users WHERE id=%s", (actor_id,)
        ).fetchone()
        if source is None or not source["enabled"]:
            raise QueryMemoryNotFound("source is unavailable")
        if actor is None or not actor["is_active"]:
            raise QueryMemoryForbidden("active account required")
        if admin_only:
            if actor["role"] != "admin":
                raise QueryMemoryForbidden("admin role required")
        elif actor["role"] == "member" and connection.execute(
            "SELECT 1 FROM auth_user_data_sources WHERE user_id=%s AND data_source_id=%s",
            (actor_id, source_id),
        ).fetchone() is None:
            raise QueryMemoryForbidden("source grant required")
        elif actor["role"] not in {"admin", "member"}:
            raise QueryMemoryForbidden("active account required")
        return actor

    @staticmethod
    def _example_from_row(row: PostgresRow) -> QueryExample:
        candidate = QueryMemoryStore._candidate(row)
        example = candidate.recall_record()
        if example is None:
            raise QueryMemoryConflict("query example content was removed")
        verify_query_example_content_hash(example)
        return example

    def submit(
        self, *, actor_id: str, data_source_id: str, thread_id: str,
        source_turn_key: str | None, idempotency_key: str, normalized_question: str,
        sql_template: str, parameter_specs: tuple[QueryParameterSpec, ...],
        connector_type: str, wren_revision_id: str, mdl_digest: str,
    ) -> QueryExampleCandidate:
        if not 16 <= len(idempotency_key) <= 128:
            raise QueryMemoryValidationError("idempotency key is invalid")
        question = normalize_question(normalized_question)
        if not question or len(question) > 500:
            raise QueryMemoryValidationError("query example question is invalid")
        if not isinstance(sql_template, str) or not sql_template.strip() or len(sql_template) > 20_000:
            raise QueryMemoryValidationError("SQL template is invalid")
        if len(parameter_specs) > 32 or len({item.name for item in parameter_specs}) != len(parameter_specs):
            raise QueryMemoryValidationError("SQL template parameter list is invalid")
        if source_turn_key is not None and not re.fullmatch(r"[a-f0-9]{64}", source_turn_key):
            raise QueryMemoryValidationError("source turn key is invalid")
        for item in parameter_specs:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item.name):
                raise QueryMemoryValidationError("SQL template parameter name is invalid")
        content_hash = self._hash_example(
            data_source_id=data_source_id, question=question, sql_template=sql_template,
            parameter_specs=parameter_specs, connector_type=connector_type,
            wren_revision_id=wren_revision_id, mdl_digest=mdl_digest,
        )
        request_hash = _digest(_canonical({
            "data_source_id": data_source_id, "thread_id": thread_id,
            "source_turn_key": source_turn_key, "content_hash": content_hash,
        }))
        idempotency_hash = _digest(idempotency_key.encode("utf-8"))
        now = self._now()
        connection = self._connect()
        try:
            connection.acquire_write_lock()
            actor = self._assert_actor_source(
                connection, actor_id=actor_id, source_id=data_source_id
            )
            thread = connection.execute(
                """SELECT binding.owner_user_id, binding.data_source_id,
                          memory.status
                   FROM chat_thread_data_sources AS binding
                   JOIN agent_conversation_threads AS memory
                     ON memory.thread_id=binding.thread_id
                   WHERE binding.thread_id=%s""", (thread_id,),
            ).fetchone()
            if (
                thread is None or thread["data_source_id"] != data_source_id
                or thread["owner_user_id"] != actor_id or thread["status"] != "active"
            ):
                raise QueryMemoryNotFound("thread is unavailable")
            source_turn_id = None
            if source_turn_key:
                source_turn = connection.execute(
                    """SELECT turn.id
                       FROM agent_conversation_turns AS turn
                       JOIN agent_turn_requests AS request
                         ON request.thread_id=turn.thread_id
                        AND request.turn_id=turn.turn_id
                       WHERE turn.thread_id=%s AND turn.turn_id=%s
                         AND turn.role='user' AND request.status='completed'""",
                    (thread_id, source_turn_key),
                ).fetchone()
                if source_turn is None:
                    raise QueryMemoryValidationError(
                        "source turn is incomplete or does not belong to thread"
                    )
                source_turn_id = source_turn["id"]
            revision = connection.execute(
                """SELECT source.active_revision_id, source.connector_type,
                          source.runtime_status, revision.status, revision.mdl_digest
                   FROM wren_data_sources AS source
                   JOIN wren_revisions AS revision
                     ON revision.source_id=source.id AND revision.id=source.active_revision_id
                   WHERE source.id=%s AND source.enabled=1""", (data_source_id,),
            ).fetchone()
            if (
                revision is None or revision["active_revision_id"] != wren_revision_id
                or revision["runtime_status"] != "ready"
                or revision["status"] != "active" or revision["mdl_digest"] != mdl_digest
                or revision["connector_type"] != connector_type
            ):
                raise QueryMemoryStaleSource("active source semantics changed")
            previous = connection.execute(
                "SELECT * FROM query_example_candidates WHERE submitted_by=%s AND idempotency_hash=%s",
                (actor_id, idempotency_hash),
            ).fetchone()
            if previous is not None:
                if previous["request_hash"] != request_hash:
                    raise QueryMemoryConflict("idempotency key was reused with different content")
                connection.commit()
                return self._candidate(previous)
            submitter_count = connection.execute(
                """SELECT COUNT(*) FROM query_example_candidates
                   WHERE data_source_id=%s AND submitted_by=%s
                     AND review_status IN ('pending','needs_revalidation','approved')
                     AND publication_status NOT IN ('active','superseded','removed')""",
                (data_source_id, actor_id),
            ).fetchone()[0]
            if submitter_count >= self.MAX_PENDING_PER_SUBMITTER_SOURCE:
                raise QueryMemoryQuotaExceeded(3600)
            thread_count = connection.execute(
                """SELECT COUNT(*) FROM query_example_candidates
                   WHERE data_source_id=%s AND source_thread_id=%s
                     AND review_status IN ('pending','needs_revalidation','approved')
                     AND publication_status NOT IN ('active','superseded','removed')""",
                (data_source_id, thread_id),
            ).fetchone()[0]
            if thread_count >= self.MAX_PENDING_PER_THREAD:
                raise QueryMemoryQuotaExceeded(3600)
            cutoff = _iso(now - timedelta(hours=1))
            recent_query = connection.execute(
                "SELECT COUNT(*), MIN(created_at) FROM query_example_candidates WHERE submitted_by=%s AND created_at>=%s",
                (actor_id, cutoff),
            ).fetchone()
            recent_rules = connection.execute(
                "SELECT COUNT(*), MIN(created_at) FROM business_rule_candidates WHERE submitted_by=%s AND created_at>=%s",
                (actor_id, cutoff),
            ).fetchone()
            recent_count = int(recent_query[0]) + int(recent_rules[0])
            if recent_count >= self.MAX_NEW_CANDIDATES_PER_HOUR:
                timestamps = [value for value in (recent_query[1], recent_rules[1]) if value]
                oldest = min(_parse_time(value) for value in timestamps)
                retry_after = math.ceil((oldest + timedelta(hours=1) - now).total_seconds())
                raise QueryMemoryQuotaExceeded(retry_after)
            example_id = uuid.uuid4().hex
            expires_at = now + self.CANDIDATE_TTL
            connection.execute(
                """INSERT INTO query_example_candidates
                   (query_example_id, data_source_id, source_thread_id, source_turn_id,
                    normalized_question, sql_template, parameter_specs_json, connector_type,
                    wren_revision_id, mdl_digest, content_hash, submitted_by,
                    idempotency_hash, request_hash, review_status, publication_status,
                    version, created_at, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending',
                           'not_published', 1, %s, %s)""",
                (example_id, data_source_id, thread_id, source_turn_id, question, sql_template,
                 _canonical([{"name": p.name, "type": p.value_type.value, "nullable": p.nullable}
                             for p in parameter_specs]).decode("utf-8"), connector_type,
                 wren_revision_id, mdl_digest, content_hash, actor_id, idempotency_hash,
                 request_hash, _iso(now), _iso(expires_at)),
            )
            self._record_event(
                connection, example_id=example_id, source_id=data_source_id, actor_id=actor_id,
                event_type="submitted", previous_review=None, review="pending",
                previous_publication=None, publication="not_published", content_hash=content_hash,
                reason="explicit_user_submission", now=now,
            )
            row = connection.execute(
                "SELECT * FROM query_example_candidates WHERE query_example_id=%s", (example_id,)
            ).fetchone()
            connection.commit()
            return self._candidate(row)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def resolve_thread_source(self, *, actor_id: str, thread_id: str) -> str:
        """Resolve source from the owner-bound server thread, never from request data."""
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT binding.data_source_id, binding.owner_user_id,
                          memory.status
                   FROM chat_thread_data_sources AS binding
                   JOIN agent_conversation_threads AS memory
                     ON memory.thread_id=binding.thread_id
                   WHERE binding.thread_id=%s""", (thread_id,),
            ).fetchone()
            if (
                row is None or row["owner_user_id"] != actor_id
                or row["status"] != "active"
            ):
                raise QueryMemoryNotFound("thread is unavailable")
            return str(row["data_source_id"])
        finally:
            connection.close()

    @staticmethod
    def preview(
        connection: PostgresConnection, thread_id: str, source_id: str
    ) -> tuple[str, ...]:
        """Return content-bearing query candidates that thread deletion will remove."""
        rows = connection.execute(
            """SELECT query_example_id FROM query_example_candidates
               WHERE data_source_id=%s AND source_thread_id=%s
               ORDER BY query_example_id""",
            (source_id, thread_id),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def get_candidate(self, example_id: str) -> QueryExampleCandidate:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM query_example_candidates WHERE query_example_id=%s", (example_id,)
            ).fetchone()
            if row is None:
                raise QueryMemoryNotFound("query example is unavailable")
            return self._candidate(row)
        finally:
            connection.close()

    def list_candidates(
        self, *, actor_id: str, is_admin: bool, data_source_id: str,
        review_status: str | None = None, limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[tuple[QueryExampleCandidate, ...], str | None]:
        if not 1 <= limit <= 100:
            raise QueryMemoryValidationError("page size is invalid")
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            actor = self._assert_actor_source(
                connection, actor_id=actor_id, source_id=data_source_id, admin_only=is_admin
            )
            if is_admin and actor["role"] != "admin":
                raise QueryMemoryForbidden("admin role required")
            clauses = ["data_source_id=%s"]
            parameters: list[object] = [data_source_id]
            if not is_admin:
                clauses.append("submitted_by=%s")
                parameters.append(actor_id)
            if review_status:
                if review_status not in {item.value for item in ReviewStatus}:
                    raise QueryMemoryValidationError("review status is invalid")
                clauses.append("review_status=%s")
                parameters.append(review_status)
            scope = "admin" if is_admin else actor_id
            boundary = self._decode_cursor(
                cursor,
                expected={
                    "source_id": data_source_id,
                    "review_status": review_status,
                    "scope": scope,
                },
            ) if cursor else None
            if boundary is not None:
                clauses.append(
                    "(created_at < %s OR (created_at = %s AND query_example_id > %s))"
                )
                parameters.extend((boundary["created_at"], boundary["created_at"], boundary["id"]))
            parameters.append(limit + 1)
            rows = connection.execute(
                f"SELECT * FROM query_example_candidates WHERE {' AND '.join(clauses)} "
                "ORDER BY created_at DESC, query_example_id ASC LIMIT %s", parameters,
            ).fetchall()
            connection.commit()
            has_more = len(rows) > limit
            page_rows = rows[:limit]
            next_cursor = None
            if has_more and page_rows:
                last = page_rows[-1]
                next_cursor = self._encode_cursor(
                    created_at=last["created_at"],
                    example_id=last["query_example_id"],
                    source_id=data_source_id,
                    review_status=review_status,
                    scope=scope,
                )
            return tuple(self._candidate(row) for row in page_rows), next_cursor
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _encode_cursor(
        *, created_at: str, example_id: str, source_id: str,
        review_status: str | None, scope: str,
    ) -> str:
        payload = _canonical({
            "created_at": created_at,
            "id": example_id,
            "source_id": source_id,
            "review_status": review_status,
            "scope": scope,
        })
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(
        cursor: str | None, *, expected: dict[str, Any]
    ) -> dict[str, str]:
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1024:
            raise QueryMemoryValidationError("candidate cursor is invalid")
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            if not isinstance(payload, dict) or any(
                payload.get(key) != value for key, value in expected.items()
            ):
                raise ValueError
            created_at = payload.get("created_at")
            example_id = payload.get("id")
            if (
                not isinstance(created_at, str)
                or _parse_time(created_at) is None
                or not isinstance(example_id, str)
                or not re.fullmatch(r"[a-f0-9]{32}", example_id)
            ):
                raise ValueError
            return {"created_at": created_at, "id": example_id}
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise QueryMemoryValidationError("candidate cursor is invalid or out of scope") from exc

    def _revision_dir(self, data_source_id: str) -> Path:
        source_key = _digest(data_source_id.encode("utf-8"))
        source_directory = self.corpus_root / "sources" / source_key
        directory = source_directory / "revisions"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        for path in (self.corpus_root, self.corpus_root / "sources", source_directory, directory):
            if path.is_symlink() or not path.resolve().is_relative_to(self.corpus_root):
                raise QueryMemoryUnavailable("query corpus directory is unsafe")
            os.chmod(path, 0o700)
        return directory

    def _write_revision_file(
        self, *, source_id: str, revision: int, connector_type: str,
        wren_revision_id: str, mdl_digest: str, records: list[dict[str, Any]], now: datetime,
    ) -> tuple[str, str, tuple[str, ...]]:
        base = {
            "schema_version": 1,
            "data_source_id": source_id,
            "corpus_revision": revision,
            "connector_type": connector_type,
            "wren_revision_id": wren_revision_id,
            "mdl_digest": mdl_digest,
            "created_at": _iso(now),
            "records": records,
        }
        content_hash = _digest(_canonical(base))
        payload = {**base, "content_hash": content_hash}
        encoded = _canonical(payload) + b"\n"
        directory = self._revision_dir(source_id)
        path = directory / f"{revision:08d}.json"
        if path.is_symlink() or not path.resolve().is_relative_to(self.corpus_root):
            raise QueryMemoryUnavailable("query corpus artifact path is unsafe")
        if path.exists() and _digest(path.read_bytes()) != _digest(encoded):
            raise QueryMemoryUnavailable("unregistered corpus artifact conflicts with next revision")
        if not path.exists():
            temporary = directory / f".{revision:08d}.{uuid.uuid4().hex}.tmp"
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb", closefd=False) as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                os.close(descriptor)
            os.replace(temporary, path)
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return str(path), content_hash, tuple(record["id"] for record in records)

    @staticmethod
    def _record_payload(row: PostgresRow) -> dict[str, Any]:
        try:
            parameters = json.loads(row["parameter_specs_json"] or "[]")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("stored query example parameter metadata is invalid") from exc
        return {
            "id": row["query_example_id"],
            "normalized_question": row["normalized_question"],
            "sql_template": row["sql_template"],
            "parameter_specs": parameters,
            "connector_type": row["connector_type"],
            "wren_revision_id": row["wren_revision_id"],
            "mdl_digest": row["mdl_digest"],
            "content_hash": row["content_hash"],
        }

    def _prepare_revision(
        self, connection: PostgresConnection, *, source_id: str,
        connector_type: str, wren_revision_id: str, mdl_digest: str,
        actor_id: str, operation_type: str, item_id: str | None, now: datetime,
    ) -> QueryCorpusRevision:
        connection.execute(
            """INSERT INTO query_corpus_source_state
               (data_source_id, active_revision, prepared_revision, generation, updated_at)
               VALUES (%s, NULL, NULL, 0, %s) ON CONFLICT(data_source_id) DO NOTHING""", (source_id, _iso(now)),
        )
        state = connection.execute(
            "SELECT * FROM query_corpus_source_state WHERE data_source_id=%s", (source_id,)
        ).fetchone()
        prior_prepared = state["prepared_revision"]
        if prior_prepared is not None:
            connection.execute(
                """UPDATE query_corpus_revisions SET status='superseded', superseded_at=%s,
                       delete_after=%s WHERE data_source_id=%s AND corpus_revision=%s
                         AND status='prepared'""",
                (_iso(now), _iso(now + self.REVISION_TTL), source_id, prior_prepared),
            )
        next_revision = connection.execute(
            "SELECT COALESCE(MAX(corpus_revision), 0) + 1 FROM query_corpus_revisions WHERE data_source_id=%s",
            (source_id,),
        ).fetchone()[0]
        rows = connection.execute(
            """SELECT candidate.* FROM query_example_candidates AS candidate
               WHERE candidate.data_source_id=%s AND candidate.review_status='approved'
                 AND candidate.publication_status IN ('active','queued','failed')
                 AND candidate.connector_type=%s AND candidate.wren_revision_id=%s
                 AND candidate.mdl_digest=%s
                 AND NOT EXISTS (
                    SELECT 1 FROM agent_memory_suppressions AS suppression
                    WHERE suppression.data_source_id=candidate.data_source_id
                      AND suppression.item_type='query_example'
                      AND suppression.item_id=candidate.query_example_id
                 )
               ORDER BY candidate.query_example_id""",
            (source_id, connector_type, wren_revision_id, mdl_digest),
        ).fetchall()
        records = [self._record_payload(row) for row in rows]
        path, content_hash, record_ids = self._write_revision_file(
            source_id=source_id, revision=next_revision, connector_type=connector_type,
            wren_revision_id=wren_revision_id, mdl_digest=mdl_digest,
            records=records, now=now,
        )
        connection.execute(
            """INSERT INTO query_corpus_revisions
               (data_source_id, corpus_revision, connector_type, wren_revision_id,
                mdl_digest, content_hash, record_ids_json, canonical_path, status, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'prepared', %s)""",
            (source_id, next_revision, connector_type, wren_revision_id, mdl_digest,
             content_hash, _canonical(list(record_ids)).decode("utf-8"), path, _iso(now)),
        )
        generation = int(state["generation"]) + 1
        operation_id = uuid.uuid4().hex
        connection.execute(
            """UPDATE query_corpus_source_state SET prepared_revision=%s, generation=%s, updated_at=%s
               WHERE data_source_id=%s AND generation=%s""",
            (next_revision, generation, _iso(now), source_id, state["generation"]),
        )
        connection.execute(
            """INSERT INTO query_corpus_operations
               (operation_id, data_source_id, operation_type, base_revision, target_revision,
                generation, actor_user_id, item_id, content_hash, status, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'prepared', %s, %s)""",
            (operation_id, source_id, operation_type, state["active_revision"], next_revision,
             generation, actor_id, item_id, content_hash, _iso(now), _iso(now)),
        )
        return QueryCorpusRevision(
            data_source_id=source_id, corpus_revision=next_revision,
            connector_type=connector_type, wren_revision_id=wren_revision_id,
            mdl_digest=mdl_digest, content_hash=content_hash,
            record_ids=tuple(uuid.UUID(hex=value) for value in record_ids),
            canonical_path=path, status="prepared", created_at=now,
            activated_at=None, superseded_at=None, delete_after=None,
        )

    def approve(
        self, example_id: str, *, actor_id: str, expected_version: int,
        connector_type: str, wren_revision_id: str, mdl_digest: str,
    ) -> tuple[QueryExampleCandidate, QueryCorpusRevision]:
        source_id = self._candidate_source(example_id)
        now = self._now()
        with self._source_lock(source_id):
            connection = self._connect()
            try:
                connection.acquire_write_lock()
                row = connection.execute(
                    "SELECT * FROM query_example_candidates WHERE query_example_id=%s AND data_source_id=%s",
                    (example_id, source_id),
                ).fetchone()
                if row is None:
                    raise QueryMemoryNotFound("query example is unavailable")
                self._assert_actor_source(connection, actor_id=actor_id, source_id=source_id, admin_only=True)
                source = connection.execute(
                    """SELECT source.active_revision_id, source.connector_type,
                              source.runtime_status, revision.status, revision.mdl_digest
                       FROM wren_data_sources AS source JOIN wren_revisions AS revision
                         ON revision.source_id=source.id AND revision.id=source.active_revision_id
                       WHERE source.id=%s AND source.enabled=1""", (source_id,),
                ).fetchone()
                if (
                    source is None or source["active_revision_id"] != wren_revision_id
                    or source["runtime_status"] != "ready"
                    or source["status"] != "active" or source["mdl_digest"] != mdl_digest
                    or source["connector_type"] != connector_type
                ):
                    raise QueryMemoryStaleSource("active source semantics changed")
                if row["version"] != expected_version or row["review_status"] not in {"pending", "needs_revalidation"}:
                    raise QueryMemoryConflict("query example state changed")
                if _parse_time(row["expires_at"]) <= now:
                    raise QueryMemoryConflict("query example candidate expired")
                if (
                    row["wren_revision_id"] != wren_revision_id
                    or row["mdl_digest"] != mdl_digest
                    or row["connector_type"] != connector_type
                ):
                    raise QueryMemoryStaleSource("query example must be revalidated first")
                self._example_from_row(row)
                connection.execute(
                    """UPDATE query_example_candidates SET review_status='approved',
                           publication_status='queued', reviewed_by=%s, reviewed_at=%s,
                           review_reason_code='admin_approved', version=version+1
                       WHERE query_example_id=%s AND version=%s""",
                    (actor_id, _iso(now), example_id, expected_version),
                )
                revision = self._prepare_revision(
                    connection, source_id=source_id, connector_type=connector_type,
                    wren_revision_id=wren_revision_id, mdl_digest=mdl_digest,
                    actor_id=actor_id, operation_type="approve", item_id=example_id, now=now,
                )
                self._record_event(
                    connection, example_id=example_id, source_id=source_id, actor_id=actor_id,
                    event_type="approved", previous_review=row["review_status"],
                    review="approved", previous_publication=row["publication_status"],
                    publication="queued", content_hash=row["content_hash"],
                    reason="admin_approved_pending_activation", now=now,
                )
                updated = connection.execute(
                    "SELECT * FROM query_example_candidates WHERE query_example_id=%s", (example_id,)
                ).fetchone()
                connection.commit()
                return self._candidate(updated), revision
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _candidate_source(self, example_id: str) -> str:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT data_source_id FROM query_example_candidates WHERE query_example_id=%s",
                (example_id,),
            ).fetchone()
            if row is None:
                raise QueryMemoryNotFound("query example is unavailable")
            return str(row["data_source_id"])
        finally:
            connection.close()

    def reject(
        self, example_id: str, *, actor_id: str, expected_version: int,
        reason_code: str,
    ) -> QueryExampleCandidate:
        allowed = {"incorrect_sql", "incorrect_semantics", "unsafe_template", "duplicate", "other"}
        if reason_code not in allowed:
            raise QueryMemoryValidationError("review reason code is invalid")
        source_id = self._candidate_source(example_id)
        now = self._now()
        connection = self._connect()
        try:
            connection.acquire_write_lock()
            self._assert_actor_source(connection, actor_id=actor_id, source_id=source_id, admin_only=True)
            row = connection.execute(
                "SELECT * FROM query_example_candidates WHERE query_example_id=%s", (example_id,)
            ).fetchone()
            if row is None:
                raise QueryMemoryNotFound("query example is unavailable")
            if row["version"] != expected_version or row["review_status"] not in {"pending", "needs_revalidation"}:
                raise QueryMemoryConflict("query example state changed")
            connection.execute(
                """UPDATE query_example_candidates SET review_status='rejected',
                       publication_status='removed', reviewed_by=%s, reviewed_at=%s,
                       review_reason_code=%s, version=version+1
                   WHERE query_example_id=%s AND version=%s""",
                (actor_id, _iso(now), reason_code, example_id, expected_version),
            )
            self._record_event(
                connection, example_id=example_id, source_id=source_id, actor_id=actor_id,
                event_type="rejected", previous_review=row["review_status"], review="rejected",
                previous_publication=row["publication_status"], publication="removed",
                content_hash=row["content_hash"], reason=reason_code, now=now,
            )
            result = connection.execute(
                "SELECT * FROM query_example_candidates WHERE query_example_id=%s", (example_id,)
            ).fetchone()
            connection.commit()
            return self._candidate(result)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def withdraw(
        self, example_id: str, *, actor_id: str, expected_version: int,
    ) -> QueryExampleCandidate:
        source_id = self._candidate_source(example_id)
        now = self._now()
        connection = self._connect()
        try:
            connection.acquire_write_lock()
            self._assert_actor_source(connection, actor_id=actor_id, source_id=source_id)
            row = connection.execute(
                "SELECT * FROM query_example_candidates WHERE query_example_id=%s", (example_id,)
            ).fetchone()
            if row is None or row["submitted_by"] != actor_id:
                raise QueryMemoryNotFound("query example is unavailable")
            if row["version"] != expected_version or row["review_status"] != "pending":
                raise QueryMemoryConflict("only a pending query example can be withdrawn")
            connection.execute(
                """UPDATE query_example_candidates SET review_status='withdrawn',
                       publication_status='removed', normalized_question=NULL,
                       sql_template=NULL, parameter_specs_json=NULL, source_thread_id=NULL,
                       source_turn_id=NULL, version=version+1
                   WHERE query_example_id=%s AND version=%s""", (example_id, expected_version),
            )
            self._record_event(
                connection, example_id=example_id, source_id=source_id, actor_id=actor_id,
                event_type="withdrawn", previous_review="pending", review="withdrawn",
                previous_publication="not_published", publication="removed",
                content_hash=row["content_hash"], reason="submitter_withdrew", now=now,
            )
            result = connection.execute(
                "SELECT * FROM query_example_candidates WHERE query_example_id=%s", (example_id,)
            ).fetchone()
            connection.commit()
            return self._candidate(result)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_needs_revalidation(
        self, example_id: str, *, actor_id: str, expected_version: int,
    ) -> QueryExampleCandidate:
        source_id = self._candidate_source(example_id)
        now = self._now()
        connection = self._connect()
        try:
            connection.acquire_write_lock()
            self._assert_actor_source(connection, actor_id=actor_id, source_id=source_id, admin_only=True)
            row = connection.execute(
                "SELECT * FROM query_example_candidates WHERE query_example_id=%s", (example_id,)
            ).fetchone()
            if row is None or row["version"] != expected_version:
                raise QueryMemoryConflict("query example state changed")
            if row["review_status"] not in {"pending", "approved", "needs_revalidation"}:
                raise QueryMemoryConflict("query example cannot be revalidated")
            connection.execute(
                """UPDATE query_example_candidates SET review_status='needs_revalidation',
                       publication_status=CASE WHEN publication_status='active'
                         THEN 'superseded' ELSE publication_status END,
                       review_reason_code='active_semantics_changed', version=version+1
                   WHERE query_example_id=%s AND version=%s""", (example_id, expected_version),
            )
            self._record_event(
                connection, example_id=example_id, source_id=source_id, actor_id=actor_id,
                event_type="needs_revalidation", previous_review=row["review_status"],
                review="needs_revalidation", previous_publication=row["publication_status"],
                publication=("superseded" if row["publication_status"] == "active" else row["publication_status"]),
                content_hash=row["content_hash"], reason="active_semantics_changed", now=now,
            )
            result = connection.execute(
                "SELECT * FROM query_example_candidates WHERE query_example_id=%s", (example_id,)
            ).fetchone()
            connection.commit()
            return self._candidate(result)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def revalidate(
        self, example_id: str, *, actor_id: str, expected_version: int,
        connector_type: str, wren_revision_id: str, mdl_digest: str,
    ) -> QueryExampleCandidate:
        source_id = self._candidate_source(example_id)
        now = self._now()
        connection = self._connect()
        try:
            connection.acquire_write_lock()
            self._assert_actor_source(connection, actor_id=actor_id, source_id=source_id, admin_only=True)
            source = connection.execute(
                """SELECT source.active_revision_id, source.connector_type, source.runtime_status,
                          revision.status, revision.mdl_digest FROM wren_data_sources AS source
                   JOIN wren_revisions AS revision
                     ON revision.source_id=source.id AND revision.id=source.active_revision_id
                   WHERE source.id=%s AND source.enabled=1""", (source_id,),
            ).fetchone()
            row = connection.execute(
                "SELECT * FROM query_example_candidates WHERE query_example_id=%s", (example_id,)
            ).fetchone()
            if row is None or row["version"] != expected_version or row["review_status"] != "needs_revalidation":
                raise QueryMemoryConflict("query example state changed")
            if (
                source is None or source["active_revision_id"] != wren_revision_id
                or source["connector_type"] != connector_type or source["runtime_status"] != "ready"
                or source["status"] != "active"
                or source["mdl_digest"] != mdl_digest
            ):
                raise QueryMemoryStaleSource("active source semantics changed")
            if row["normalized_question"] is None or row["sql_template"] is None:
                raise QueryMemoryConflict("query example content was removed")
            parameters = self._parameters(row["parameter_specs_json"])
            content_hash = self._hash_example(
                data_source_id=source_id, question=row["normalized_question"],
                sql_template=row["sql_template"], parameter_specs=parameters,
                connector_type=connector_type, wren_revision_id=wren_revision_id,
                mdl_digest=mdl_digest,
            )
            connection.execute(
                """UPDATE query_example_candidates SET review_status='pending',
                       publication_status='not_published', connector_type=%s, wren_revision_id=%s,
                       mdl_digest=%s, content_hash=%s, reviewed_by=%s, reviewed_at=%s,
                       review_reason_code='revalidated_requires_review', version=version+1
                   WHERE query_example_id=%s AND version=%s""",
                (connector_type, wren_revision_id, mdl_digest, content_hash, actor_id,
                 _iso(now), example_id, expected_version),
            )
            self._record_event(
                connection, example_id=example_id, source_id=source_id, actor_id=actor_id,
                event_type="revalidated", previous_review="needs_revalidation", review="pending",
                previous_publication=row["publication_status"], publication="not_published",
                content_hash=content_hash, reason="new_semantics_requires_fresh_approval", now=now,
            )
            result = connection.execute(
                "SELECT * FROM query_example_candidates WHERE query_example_id=%s", (example_id,)
            ).fetchone()
            connection.commit()
            return self._candidate(result)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def apply_suppression(self, connection: PostgresConnection, event: JournalEvent) -> None:
        """Apply query-example revoke and thread deletion during journal replay."""
        now = event.created_at
        if event.event_type == "query_example_revoke":
            for example_id in event.item_ids:
                row = connection.execute(
                    """SELECT * FROM query_example_candidates
                       WHERE data_source_id=%s AND query_example_id=%s""",
                    (event.source_id, example_id),
                ).fetchone()
                if row is None:
                    continue
                thread_hash = row["source_thread_hash"]
                turn_hash = row["source_turn_hash"]
                if self.deletion_journal is not None:
                    thread_hash = thread_hash or (
                        self.deletion_journal.keyed_audit_hash(row["source_thread_id"])
                        if row["source_thread_id"]
                        else None
                    )
                    turn_hash = turn_hash or (
                        self.deletion_journal.keyed_audit_hash(row["source_turn_id"])
                        if row["source_turn_id"]
                        else None
                    )
                connection.execute(
                    """UPDATE query_example_candidates SET review_status='revoked',
                           publication_status='removed', reviewed_by=%s, reviewed_at=%s,
                           revoked_at=%s, normalized_question=NULL, sql_template=NULL,
                           parameter_specs_json=NULL, source_thread_id=NULL, source_turn_id=NULL,
                           source_thread_hash=%s, source_turn_hash=%s,
                           version=version+1 WHERE query_example_id=%s""",
                    (event.actor_id or "system:journal-replay", _iso(now), _iso(now),
                     thread_hash, turn_hash, example_id),
                )
                self._record_event(
                    connection, example_id=example_id, source_id=event.source_id,
                    actor_id=event.actor_id or "system:journal-replay", event_type="revoked",
                    previous_review=row["review_status"], review="revoked",
                    previous_publication=row["publication_status"], publication="removed",
                    content_hash=row["content_hash"], reason="admin_revoked",
                    now=now, event_id=f"journal-{event.sequence}-{example_id}",
                )
            return
        if event.event_type != "thread_delete" or not event.thread_id:
            return
        rows = connection.execute(
            """SELECT * FROM query_example_candidates
               WHERE data_source_id=%s AND source_thread_id=%s""",
            (event.source_id, event.thread_id),
        ).fetchall()
        actor_id = event.actor_id or "system:thread-deletion"
        for row in rows:
            example_id = row["query_example_id"]
            if row["publication_status"] == "active" and row["review_status"] == "approved":
                thread_hash = row["source_thread_hash"]
                turn_hash = row["source_turn_hash"]
                if self.deletion_journal is not None:
                    thread_hash = thread_hash or self.deletion_journal.keyed_audit_hash(
                        event.thread_id
                    )
                    turn_hash = turn_hash or (
                        self.deletion_journal.keyed_audit_hash(row["source_turn_id"])
                        if row["source_turn_id"]
                        else None
                    )
                connection.execute(
                    """UPDATE query_example_candidates SET source_thread_id=NULL,
                           source_turn_id=NULL, source_thread_hash=COALESCE(source_thread_hash, %s),
                           source_turn_hash=COALESCE(source_turn_hash, %s), version=version+1
                       WHERE query_example_id=%s""",
                    (thread_hash, turn_hash, example_id),
                )
                new_review, new_publication = "approved", "active"
                event_type, reason = "source_thread_redacted", "published_example_survives_thread_deletion"
            else:
                new_review = "withdrawn"
                new_publication = "removed"
                connection.execute(
                    """UPDATE query_example_candidates SET source_thread_id=NULL,
                           source_turn_id=NULL, normalized_question=NULL, sql_template=NULL,
                           source_thread_hash=NULL, source_turn_hash=NULL,
                           parameter_specs_json=NULL, review_status=%s, publication_status='removed',
                           version=version+1 WHERE query_example_id=%s""",
                    (new_review, example_id),
                )
                connection.execute(
                    """INSERT INTO agent_memory_suppressions
                       (event_sequence, data_source_id, item_type, item_id, reason, created_at)
                       VALUES (%s, %s, 'query_example', %s, %s, %s)
                       ON CONFLICT(event_sequence, item_type, item_id) DO NOTHING""",
                    (event.sequence, event.source_id, example_id, event.event_type, _iso(now)),
                )
                event_type, reason = event.event_type, "unpublished_candidate_deleted_with_thread"
            self._record_event(
                connection, example_id=example_id, source_id=event.source_id,
                actor_id=actor_id, event_type=event_type,
                previous_review=row["review_status"], review=new_review,
                previous_publication=row["publication_status"], publication=new_publication,
                content_hash=row["content_hash"], reason=reason, now=now,
                event_id=f"journal-{event.sequence}-query-{example_id}",
            )

    def expire_pending_candidates(self, *, limit: int = 100) -> int:
        if not 1 <= limit <= 500:
            raise QueryMemoryValidationError("expiry batch size is invalid")
        now = self._now()
        connection = self._connect()
        try:
            connection.acquire_write_lock()
            rows = connection.execute(
                """SELECT * FROM query_example_candidates
                   WHERE review_status IN ('pending','approved')
                     AND publication_status IN ('not_published','queued','failed')
                     AND expires_at<=%s ORDER BY expires_at LIMIT %s""",
                (_iso(now), limit),
            ).fetchall()
            for row in rows:
                example_id = row["query_example_id"]
                connection.execute(
                    """UPDATE query_example_candidates SET review_status='expired',
                           publication_status='removed', source_thread_id=NULL, source_turn_id=NULL,
                           normalized_question=NULL, sql_template=NULL, parameter_specs_json=NULL,
                           version=version+1 WHERE query_example_id=%s""", (example_id,),
                )
                connection.execute(
                    """INSERT INTO agent_memory_suppressions
                       (event_sequence, data_source_id, item_type, item_id, reason, created_at)
                       VALUES ((SELECT journal_applied_seq FROM agent_memory_journal_state WHERE id=1),
                               %s, 'query_example', %s, 'candidate_expired', %s)
                       ON CONFLICT(event_sequence, item_type, item_id) DO NOTHING""",
                    (row["data_source_id"], example_id, _iso(now)),
                )
                self._record_event(
                    connection, example_id=example_id, source_id=row["data_source_id"],
                    actor_id="system:candidate-expiry", event_type="expired",
                    previous_review=row["review_status"], review="expired",
                    previous_publication=row["publication_status"], publication="removed",
                    content_hash=row["content_hash"], reason="candidate_ttl_elapsed", now=now,
                )
            connection.commit()
            return len(rows)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def activate_prepared_revision(
        self, *, data_source_id: str, target_revision: int, expected_generation: int,
        wren_revision_id: str, mdl_digest: str, actor_id: str,
        wren_operation_id: str, wren_generation: int,
    ) -> QueryCorpusRevision:
        """Atomically publish a prepared corpus and persist its source generation."""
        now = self._now()
        with self._source_lock(data_source_id):
            connection = self._connect()
            try:
                connection.acquire_write_lock()
                self._assert_actor_source(
                    connection, actor_id=actor_id, source_id=data_source_id, admin_only=True
                )
                state = connection.execute(
                    "SELECT * FROM query_corpus_source_state WHERE data_source_id=%s",
                    (data_source_id,),
                ).fetchone()
                row = connection.execute(
                    """SELECT * FROM query_corpus_revisions
                       WHERE data_source_id=%s AND corpus_revision=%s""",
                    (data_source_id, target_revision),
                ).fetchone()
                source = connection.execute(
                    """SELECT source.active_revision_id, source.runtime_status,
                              revision.status, revision.mdl_digest
                       FROM wren_data_sources AS source JOIN wren_revisions AS revision
                         ON revision.source_id=source.id AND revision.id=source.active_revision_id
                       WHERE source.id=%s AND source.enabled=1""", (data_source_id,),
                ).fetchone()
                wren_operation = connection.execute(
                    """SELECT operation_type, status, base_revision_id, base_generation,
                              target_revision_id, actor_id, payload_json
                       FROM wren_operations WHERE id=%s AND source_id=%s""",
                    (wren_operation_id, data_source_id),
                ).fetchone()
                corpus_operation = connection.execute(
                    """SELECT status, content_hash FROM query_corpus_operations
                       WHERE data_source_id=%s AND target_revision=%s AND generation=%s""",
                    (data_source_id, target_revision, expected_generation),
                ).fetchone()
                wren_state = connection.execute(
                    """SELECT generation, active_operation_id
                       FROM wren_source_operation_state WHERE source_id=%s""",
                    (data_source_id,),
                ).fetchone()
                if (
                    state is None or row is None or state["prepared_revision"] != target_revision
                    or state["generation"] != expected_generation or row["status"] != "prepared"
                    or row["wren_revision_id"] != wren_revision_id or row["mdl_digest"] != mdl_digest
                    or source is None or source["active_revision_id"] != wren_revision_id
                    or source["runtime_status"] != "ready"
                    or source["status"] != "active" or source["mdl_digest"] != mdl_digest
                    or wren_operation is None or wren_operation["operation_type"] != "query_corpus_activate"
                    or wren_operation["status"] != "running"
                    or wren_operation["base_revision_id"] != wren_revision_id
                    or wren_operation["base_generation"] != wren_generation
                    or wren_operation["target_revision_id"] != f"query_corpus_{target_revision}"
                    or wren_operation["actor_id"] != actor_id
                    or corpus_operation is None or corpus_operation["status"] != "prepared"
                    or corpus_operation["content_hash"] != row["content_hash"]
                    or wren_state is None or wren_state["generation"] != wren_generation
                    or wren_state["active_operation_id"] != wren_operation_id
                ):
                    raise QueryMemoryConflict("prepared query corpus is stale")
                try:
                    operation_payload = json.loads(wren_operation["payload_json"] or "{}")
                except (TypeError, ValueError) as exc:
                    raise QueryMemoryUnavailable("source activation operation is corrupt") from exc
                if (
                    not isinstance(operation_payload, dict)
                    or operation_payload.get("corpus_revision") != target_revision
                    or operation_payload.get("content_hash") != row["content_hash"]
                    or operation_payload.get("corpus_generation") != expected_generation
                ):
                    raise QueryMemoryConflict("source activation operation does not match the corpus")
                manifest = self._read_canonical_file(row["canonical_path"], row["content_hash"])
                if (
                    manifest.get("data_source_id") != data_source_id
                    or manifest.get("corpus_revision") != target_revision
                    or manifest.get("connector_type") != row["connector_type"]
                    or manifest.get("wren_revision_id") != wren_revision_id
                    or manifest.get("mdl_digest") != mdl_digest
                ):
                    raise QueryMemoryUnavailable("prepared query corpus manifest does not match its registry")
                record_ids = tuple(item["id"] for item in manifest["records"])
                suppressed = set()
                if record_ids:
                    placeholders = ",".join("%s" for _ in record_ids)
                    suppressed = {
                        item[0] for item in connection.execute(
                            f"""SELECT item_id FROM agent_memory_suppressions
                                WHERE data_source_id=%s AND item_type='query_example'
                                  AND item_id IN ({placeholders})""",
                            (data_source_id, *record_ids),
                        ).fetchall()
                    }
                if suppressed:
                    raise QueryMemoryConflict("prepared corpus contains suppressed examples")
                active_revision = state["active_revision"]
                if active_revision is not None:
                    connection.execute(
                        """UPDATE query_corpus_revisions SET status='superseded',
                               superseded_at=%s, delete_after=%s
                           WHERE data_source_id=%s AND corpus_revision=%s AND status='active'""",
                        (_iso(now), _iso(now + self.REVISION_TTL), data_source_id, active_revision),
                    )
                    connection.execute(
                        """UPDATE query_example_candidates SET publication_status='superseded',
                               superseded_at=COALESCE(superseded_at, %s)
                           WHERE data_source_id=%s AND publication_status='active'""",
                        (_iso(now), data_source_id),
                    )
                if record_ids:
                    placeholders = ",".join("%s" for _ in record_ids)
                    provenance_rows = connection.execute(
                        f"""SELECT query_example_id, source_thread_id, source_turn_id
                            FROM query_example_candidates
                            WHERE data_source_id=%s AND query_example_id IN ({placeholders})
                              AND review_status='approved'""",
                        (data_source_id, *record_ids),
                    ).fetchall()
                    if len(provenance_rows) != len(record_ids):
                        raise QueryMemoryConflict(
                            "prepared corpus records no longer match approved records"
                        )
                    if self.deletion_journal is None and any(
                        item["source_thread_id"] or item["source_turn_id"]
                        for item in provenance_rows
                    ):
                        raise QueryMemoryUnavailable(
                            "deletion journal is required to redact query-example provenance"
                        )
                    for provenance in provenance_rows:
                        thread_hash = (
                            self.deletion_journal.keyed_audit_hash(
                                provenance["source_thread_id"]
                            )
                            if self.deletion_journal is not None
                            and provenance["source_thread_id"]
                            else None
                        )
                        turn_hash = (
                            self.deletion_journal.keyed_audit_hash(
                                provenance["source_turn_id"]
                            )
                            if self.deletion_journal is not None
                            and provenance["source_turn_id"]
                            else None
                        )
                        connection.execute(
                            """UPDATE query_example_candidates SET source_thread_id=NULL,
                                   source_turn_id=NULL,
                                   source_thread_hash=COALESCE(source_thread_hash, %s),
                                   source_turn_hash=COALESCE(source_turn_hash, %s)
                               WHERE data_source_id=%s AND query_example_id=%s""",
                            (thread_hash, turn_hash, data_source_id,
                             provenance["query_example_id"]),
                        )
                    connection.execute(
                        f"""UPDATE query_example_candidates SET publication_status='active',
                               activated_at=COALESCE(activated_at, %s), version=version+1
                           WHERE data_source_id=%s AND query_example_id IN ({placeholders})
                             AND review_status='approved'""",
                        (_iso(now), data_source_id, *record_ids),
                    )
                    activated_count = connection.execute(
                        f"""SELECT COUNT(*) FROM query_example_candidates
                            WHERE data_source_id=%s AND query_example_id IN ({placeholders})
                              AND review_status='approved' AND publication_status='active'""",
                        (data_source_id, *record_ids),
                    ).fetchone()[0]
                    if activated_count != len(record_ids):
                        raise QueryMemoryConflict("prepared corpus records no longer match approved records")
                changed = connection.execute(
                    """UPDATE query_corpus_revisions SET status='active', activated_at=%s
                       WHERE data_source_id=%s AND corpus_revision=%s AND status='prepared'""",
                    (_iso(now), data_source_id, target_revision),
                )
                if changed.rowcount != 1:
                    raise QueryMemoryConflict("prepared query corpus revision changed")
                changed = connection.execute(
                    """UPDATE query_corpus_source_state SET active_revision=%s, prepared_revision=NULL,
                           generation=generation+1, updated_at=%s
                       WHERE data_source_id=%s AND generation=%s AND prepared_revision=%s""",
                    (target_revision, _iso(now), data_source_id, expected_generation, target_revision),
                )
                if changed.rowcount != 1:
                    raise QueryMemoryConflict("query corpus generation changed")
                changed = connection.execute(
                    """UPDATE query_corpus_operations SET status='active', updated_at=%s
                       WHERE data_source_id=%s AND target_revision=%s AND generation=%s AND status='prepared'""",
                    (_iso(now), data_source_id, target_revision, expected_generation),
                )
                if changed.rowcount != 1:
                    raise QueryMemoryConflict("query corpus operation changed")
                next_wren_generation = wren_generation + 1
                changed = connection.execute(
                    """UPDATE wren_source_operation_state SET generation=%s, updated_at=%s
                       WHERE source_id=%s AND generation=%s AND active_operation_id=%s""",
                    (next_wren_generation, _iso(now), data_source_id, wren_generation,
                     wren_operation_id),
                )
                if changed.rowcount != 1:
                    raise QueryMemoryConflict("source operation generation changed")
                changed = connection.execute(
                    """UPDATE wren_operations SET phase='generation_persisted',
                           activated_generation=%s, updated_at=%s
                       WHERE id=%s AND source_id=%s AND status='running'
                         AND operation_type='query_corpus_activate'""",
                    (next_wren_generation, _iso(now), wren_operation_id, data_source_id),
                )
                if changed.rowcount != 1:
                    raise QueryMemoryConflict("source activation operation changed")
                changed = connection.execute(
                    """UPDATE wren_data_sources SET runtime_status='unavailable', updated_at=%s
                       WHERE id=%s AND active_revision_id=%s AND runtime_status='ready'""",
                    (_iso(now), data_source_id, wren_revision_id),
                )
                if changed.rowcount != 1:
                    raise QueryMemoryConflict("source runtime state changed")
                updated = connection.execute(
                    "SELECT * FROM query_corpus_revisions WHERE data_source_id=%s AND corpus_revision=%s",
                    (data_source_id, target_revision),
                ).fetchone()
                connection.commit()
                return self._revision(updated)
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def prepared_revision_for_activation(
        self, *, data_source_id: str, target_revision: int, actor_id: str,
    ) -> tuple[QueryCorpusRevision, int]:
        """Return the current prepared revision and its CAS generation for admin activation."""
        connection = self._connect()
        try:
            self._assert_actor_source(
                connection, actor_id=actor_id, source_id=data_source_id, admin_only=True
            )
            state = connection.execute(
                "SELECT * FROM query_corpus_source_state WHERE data_source_id=%s",
                (data_source_id,),
            ).fetchone()
            row = connection.execute(
                """SELECT * FROM query_corpus_revisions
                   WHERE data_source_id=%s AND corpus_revision=%s""",
                (data_source_id, target_revision),
            ).fetchone()
            is_currently_active = (
                state is not None and row is not None
                and state["active_revision"] == target_revision and row["status"] == "active"
            )
            is_currently_prepared = (
                state is not None and row is not None
                and state["prepared_revision"] == target_revision and row["status"] == "prepared"
            )
            if not is_currently_active and not is_currently_prepared:
                raise QueryMemoryConflict("query corpus revision is no longer prepared")
            return self._revision(row), int(state["generation"])
        except psycopg.Error as exc:
            raise QueryMemoryUnavailable("query corpus state is unavailable") from exc
        finally:
            connection.close()

    @staticmethod
    def revision_identity(revision: QueryCorpusRevision) -> str:
        return f"query-corpus:{revision.corpus_revision}:{revision.content_hash}"

    def prepared_examples(self, revision: QueryCorpusRevision) -> tuple[QueryExample, ...]:
        """Load the exact approved and unsuppressed records named by a prepared manifest."""
        connection = self._connect()
        try:
            row = connection.execute(
                """SELECT * FROM query_corpus_revisions
                   WHERE data_source_id=%s AND corpus_revision=%s AND status='prepared'""",
                (revision.data_source_id, revision.corpus_revision),
            ).fetchone()
            if (
                row is None or row["content_hash"] != revision.content_hash
                or row["wren_revision_id"] != revision.wren_revision_id
                or row["mdl_digest"] != revision.mdl_digest
                or row["connector_type"] != revision.connector_type
            ):
                raise QueryMemoryConflict("prepared query corpus changed")
            manifest = self._read_canonical_file(row["canonical_path"], row["content_hash"])
            if (
                manifest.get("data_source_id") != revision.data_source_id
                or manifest.get("corpus_revision") != revision.corpus_revision
                or manifest.get("connector_type") != revision.connector_type
                or manifest.get("wren_revision_id") != revision.wren_revision_id
                or manifest.get("mdl_digest") != revision.mdl_digest
            ):
                raise QueryMemoryUnavailable("prepared query corpus manifest does not match its registry")
            examples: list[QueryExample] = []
            for item in manifest["records"]:
                candidate = connection.execute(
                    """SELECT * FROM query_example_candidates
                       WHERE data_source_id=%s AND query_example_id=%s""",
                    (revision.data_source_id, item["id"]),
                ).fetchone()
                suppressed = connection.execute(
                    """SELECT 1 FROM agent_memory_suppressions
                       WHERE data_source_id=%s AND item_type='query_example' AND item_id=%s""",
                    (revision.data_source_id, item["id"]),
                ).fetchone()
                if (
                    candidate is None or candidate["review_status"] != "approved"
                    or candidate["publication_status"] not in {"active", "queued", "failed"}
                    or candidate["content_hash"] != item.get("content_hash")
                    or self._record_payload(candidate) != item or suppressed is not None
                ):
                    raise QueryMemoryConflict("prepared corpus records changed or were suppressed")
                examples.append(replace(
                    self._example_from_row(candidate),
                    publication_status=PublicationStatus.ACTIVE,
                ))
            if len(examples) != len(manifest["records"]):
                raise QueryMemoryConflict("prepared corpus records are incomplete")
            return tuple(examples)
        except psycopg.Error as exc:
            raise QueryMemoryUnavailable("prepared query corpus is unavailable") from exc
        finally:
            connection.close()

    def _read_canonical_file(self, path_value: str, expected_hash: str) -> dict[str, Any]:
        try:
            path = Path(path_value).resolve()
            if not path.is_relative_to(self.corpus_root):
                raise ValueError
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
            content_hash = payload.pop("content_hash")
            if content_hash != expected_hash or _digest(_canonical(payload)) != expected_hash:
                raise ValueError
            if not isinstance(payload.get("records"), list):
                raise ValueError
            for item in payload["records"]:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise ValueError
            return {**payload, "content_hash": content_hash}
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise QueryMemoryUnavailable("canonical query corpus is missing or corrupt") from exc

    @staticmethod
    def _revision(row: PostgresRow) -> QueryCorpusRevision:
        try:
            ids = json.loads(row["record_ids_json"])
            if not isinstance(ids, list):
                raise ValueError
            return QueryCorpusRevision(
                data_source_id=row["data_source_id"], corpus_revision=int(row["corpus_revision"]),
                connector_type=row["connector_type"], wren_revision_id=row["wren_revision_id"],
                mdl_digest=row["mdl_digest"], content_hash=row["content_hash"],
                record_ids=tuple(uuid.UUID(hex=value) for value in ids),
                canonical_path=row["canonical_path"], status=row["status"],
                created_at=_parse_time(row["created_at"]),
                activated_at=_parse_time(row["activated_at"]),
                superseded_at=_parse_time(row["superseded_at"]),
                delete_after=_parse_time(row["delete_after"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("stored query corpus revision is invalid") from exc

    def active_revision_identity(
        self, *, data_source_id: str, connector_type: str,
        wren_revision_id: str, mdl_digest: str,
    ) -> str:
        """Return the immutable active-corpus identity for one semantic snapshot."""
        connection = self._connect()
        try:
            state = connection.execute(
                "SELECT active_revision FROM query_corpus_source_state WHERE data_source_id=%s",
                (data_source_id,),
            ).fetchone()
            if state is None or state["active_revision"] is None:
                return "none"
            revision = connection.execute(
                """SELECT * FROM query_corpus_revisions WHERE data_source_id=%s
                   AND corpus_revision=%s AND status='active'""",
                (data_source_id, state["active_revision"]),
            ).fetchone()
            if (
                revision is None
                or revision["connector_type"] != connector_type
                or revision["wren_revision_id"] != wren_revision_id
                or revision["mdl_digest"] != mdl_digest
            ):
                return "none"
            manifest = self._read_canonical_file(
                revision["canonical_path"], revision["content_hash"]
            )
            if (
                manifest.get("data_source_id") != data_source_id
                or manifest.get("corpus_revision") != revision["corpus_revision"]
                or manifest.get("connector_type") != connector_type
                or manifest.get("wren_revision_id") != wren_revision_id
                or manifest.get("mdl_digest") != mdl_digest
            ):
                raise QueryMemoryUnavailable(
                    "active query corpus manifest does not match its semantic snapshot"
                )
            return f"query-corpus:{revision['corpus_revision']}:{revision['content_hash']}"
        finally:
            connection.close()

    def suppressed_ids(self, *, data_source_id: str) -> frozenset[str]:
        """Read the source's durable suppression IDs for all recallable memory kinds."""
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT item_id FROM agent_memory_suppressions
                   WHERE data_source_id=%s AND item_type IN ('business_rule','query_example')""",
                (data_source_id,),
            ).fetchall()
            return frozenset(str(row["item_id"]) for row in rows)
        finally:
            connection.close()

    def active_examples(self, *, data_source_id: str, mdl_digest: str) -> tuple[QueryExample, ...]:
        connection = self._connect()
        try:
            state = connection.execute(
                "SELECT active_revision FROM query_corpus_source_state WHERE data_source_id=%s",
                (data_source_id,),
            ).fetchone()
            if state is None or state["active_revision"] is None:
                return ()
            revision = connection.execute(
                """SELECT * FROM query_corpus_revisions WHERE data_source_id=%s
                   AND corpus_revision=%s AND status='active'""",
                (data_source_id, state["active_revision"]),
            ).fetchone()
            if revision is None or revision["mdl_digest"] != mdl_digest:
                return ()
            manifest = self._read_canonical_file(revision["canonical_path"], revision["content_hash"])
            if (
                manifest.get("data_source_id") != data_source_id
                or manifest.get("corpus_revision") != revision["corpus_revision"]
                or manifest.get("connector_type") != revision["connector_type"]
                or manifest.get("wren_revision_id") != revision["wren_revision_id"]
                or manifest.get("mdl_digest") != revision["mdl_digest"]
            ):
                raise QueryMemoryUnavailable("active query corpus manifest does not match its registry")
            result: list[QueryExample] = []
            for item in manifest["records"]:
                row = connection.execute(
                    """SELECT * FROM query_example_candidates
                       WHERE data_source_id=%s AND query_example_id=%s""",
                    (data_source_id, item["id"]),
                ).fetchone()
                if (
                    row is None or row["review_status"] != "approved"
                    or row["publication_status"] != "active"
                    or row["content_hash"] != item["content_hash"]
                    or self._record_payload(row) != item
                ):
                    continue
                suppressed = connection.execute(
                    """SELECT 1 FROM agent_memory_suppressions WHERE data_source_id=%s
                       AND item_type='query_example' AND item_id=%s LIMIT 1""",
                    (data_source_id, item["id"]),
                ).fetchone()
                if suppressed is None:
                    result.append(self._example_from_row(row))
            return tuple(result)
        finally:
            connection.close()

    def list_revisions(self, *, actor_id: str, data_source_id: str, limit: int = 30) -> tuple[QueryCorpusRevision, ...]:
        connection = self._connect()
        try:
            self._assert_actor_source(connection, actor_id=actor_id, source_id=data_source_id, admin_only=True)
            rows = connection.execute(
                """SELECT * FROM query_corpus_revisions WHERE data_source_id=%s
                   ORDER BY corpus_revision DESC LIMIT %s""", (data_source_id, limit),
            ).fetchall()
            return tuple(self._revision(row) for row in rows)
        finally:
            connection.close()

    def prune_expired_revisions(self, *, in_flight: Any = None, limit: int = 100) -> int:
        now = self._now()
        paths_to_delete: list[Path] = []
        connection = self._connect()
        try:
            connection.acquire_write_lock()
            rows = connection.execute(
                """SELECT * FROM query_corpus_revisions
                   WHERE status IN ('superseded','expired') AND delete_after<=%s
                   ORDER BY delete_after LIMIT %s""", (_iso(now), limit),
            ).fetchall()
            changed = 0
            for row in rows:
                revision = self._revision(row)
                if in_flight and in_flight(revision):
                    continue
                connection.execute(
                    """UPDATE query_corpus_revisions SET status='expired'
                       WHERE data_source_id=%s AND corpus_revision=%s AND status='superseded'""",
                    (revision.data_source_id, revision.corpus_revision),
                )
                paths_to_delete.append(Path(revision.canonical_path).resolve())
                changed += 1
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        for path in paths_to_delete:
            if path.is_relative_to(self.corpus_root):
                path.unlink(missing_ok=True)
        return changed

    def assert_corpus_files_consistent(self) -> None:
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT * FROM query_corpus_revisions
                   WHERE status IN ('prepared','active','superseded','expired')"""
            ).fetchall()
            registered_paths: set[Path] = set()
            for row in rows:
                path = Path(row["canonical_path"]).resolve()
                if not path.is_relative_to(self.corpus_root):
                    raise QueryMemoryUnavailable("query corpus path escaped configured root")
                registered_paths.add(path)
                if row["status"] == "expired" and not path.exists():
                    continue
                manifest = self._read_canonical_file(str(path), row["content_hash"])
                if (
                    manifest.get("data_source_id") != row["data_source_id"]
                    or manifest.get("corpus_revision") != row["corpus_revision"]
                    or manifest.get("connector_type") != row["connector_type"]
                    or manifest.get("wren_revision_id") != row["wren_revision_id"]
                    or manifest.get("mdl_digest") != row["mdl_digest"]
                ):
                    raise QueryMemoryUnavailable("query corpus manifest does not match its registry")
            states = connection.execute("SELECT * FROM query_corpus_source_state").fetchall()
            for state in states:
                for field, required_status in (("active_revision", "active"), ("prepared_revision", "prepared")):
                    revision_number = state[field]
                    if revision_number is None:
                        continue
                    revision = connection.execute(
                        """SELECT status FROM query_corpus_revisions
                           WHERE data_source_id=%s AND corpus_revision=%s""",
                        (state["data_source_id"], revision_number),
                    ).fetchone()
                    if revision is None or revision["status"] != required_status:
                        raise QueryMemoryUnavailable("query corpus source pointer is inconsistent")
            if self.corpus_root.exists():
                for path in self.corpus_root.glob("sources/*/revisions/*"):
                    resolved = path.resolve()
                    if not resolved.is_relative_to(self.corpus_root):
                        raise QueryMemoryUnavailable("query corpus artifact escaped configured root")
                    if resolved in registered_paths:
                        continue
                    if path.is_file() and (
                        re.fullmatch(r"\d{8}\.json", path.name)
                        or re.fullmatch(r"\.\d{8}\.[0-9a-f]{32}\.tmp", path.name)
                    ):
                        path.unlink(missing_ok=True)
        finally:
            connection.close()
