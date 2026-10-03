from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import unicodedata
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from domain.business_rules import (
    BusinessRuleCandidate,
    BusinessRuleConflict,
    BusinessRuleForbidden,
    BusinessRuleNotFound,
    BusinessRulePublicationStatus,
    BusinessRuleQuotaExceeded,
    BusinessRuleReviewStatus,
    BusinessRuleStaleSource,
    BusinessRuleValidationError,
)
from integrations.deletion_journal import (
    EncryptedDeletionJournal,
    JournalEvent,
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _opaque_key(value: str) -> str:
    if not isinstance(value, str) or not 16 <= len(value) <= 128:
        raise BusinessRuleValidationError("idempotency key is invalid")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


class _SourceSuppressionBarrier:
    """Writer-preferring source barrier for suppression mutations and SSE sends."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    def acquire_writer(self) -> None:
        with self._condition:
            self._waiting_writers += 1
            try:
                while self._writer or self._readers:
                    self._condition.wait()
                self._writer = True
            finally:
                self._waiting_writers -= 1

    def release_writer(self) -> None:
        with self._condition:
            if not self._writer:
                raise RuntimeError("source suppression writer lock is not held")
            self._writer = False
            self._condition.notify_all()

    def try_acquire_reader(self) -> bool:
        with self._condition:
            if self._writer or self._waiting_writers:
                return False
            self._readers += 1
            return True

    def release_reader(self) -> None:
        with self._condition:
            if self._readers <= 0:
                raise RuntimeError("source suppression reader lock is not held")
            self._readers -= 1
            if self._readers == 0:
                self._condition.notify_all()


class BusinessRuleMemoryStore:
    """Source-scoped candidate workflow and thread deletion participant."""

    MAX_PENDING_PER_THREAD = 20
    MAX_PENDING_PER_SUBMITTER_SOURCE = 20
    MAX_NEW_CANDIDATES_PER_HOUR = 10

    def __init__(
        self,
        database_path: Path,
        *,
        deletion_journal: EncryptedDeletionJournal,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.deletion_journal = deletion_journal
        self.clock = clock or (lambda: datetime.now(UTC))
        self._source_suppression_barriers: dict[str, _SourceSuppressionBarrier] = {}
        self._source_suppression_barriers_guard = threading.Lock()

    def _source_suppression_barrier(self, data_source_id: str) -> _SourceSuppressionBarrier:
        with self._source_suppression_barriers_guard:
            return self._source_suppression_barriers.setdefault(
                data_source_id, _SourceSuppressionBarrier()
            )

    def acquire_suppression_mutation(self, data_source_id: str) -> None:
        self._source_suppression_barrier(data_source_id).acquire_writer()

    def release_suppression_mutation(self, data_source_id: str) -> None:
        self._source_suppression_barrier(data_source_id).release_writer()

    def try_acquire_online_emission(self, data_source_id: str) -> bool:
        return self._source_suppression_barrier(data_source_id).try_acquire_reader()

    def release_online_emission(self, data_source_id: str) -> None:
        self._source_suppression_barrier(data_source_id).release_reader()

    def _now(self) -> datetime:
        value = self.clock()
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @staticmethod
    def _candidate(row: sqlite3.Row) -> BusinessRuleCandidate:
        raw_references = row["mdl_references_json"]
        try:
            references = tuple(json.loads(raw_references)) if raw_references else ()
        except (TypeError, json.JSONDecodeError) as exc:
            raise sqlite3.DatabaseError("stored business-rule reference list is invalid") from exc
        return BusinessRuleCandidate(
            business_rule_id=row["business_rule_id"],
            data_source_id=row["data_source_id"],
            term=row["term"],
            definition=row["definition"],
            mdl_references=references,
            base_wren_revision_id=row["base_wren_revision_id"],
            base_mdl_digest=row["base_mdl_digest"],
            content_hash=row["content_hash"],
            review_status=BusinessRuleReviewStatus(row["review_status"]),
            publication_status=BusinessRulePublicationStatus(row["publication_status"]),
            has_exact_term_conflict=bool(row["has_exact_term_conflict"]),
            clarification_question=row["clarification_question"],
            submitted_by=row["submitted_by"],
            reviewed_by=row["reviewed_by"],
            review_reason_code=row["review_reason_code"],
            version=row["version"],
            created_at=_time(row["created_at"]),
            updated_at=_time(row["updated_at"]),
            expires_at=_time(row["expires_at"]),
        )

    @staticmethod
    def content_hash(
        *,
        data_source_id: str,
        term: str,
        definition: str,
        mdl_references: tuple[str, ...],
        wren_revision_id: str,
        mdl_digest: str,
        active_rule_terms: tuple[str, ...] = (),
    ) -> str:
        return _hash(
            _canonical(
                {
                    "data_source_id": data_source_id,
                    "term": term,
                    "definition": definition,
                    "mdl_references": list(mdl_references),
                    "wren_revision_id": wren_revision_id,
                    "mdl_digest": mdl_digest,
                }
            )
        )

    @staticmethod
    def _record_event(
        connection: sqlite3.Connection,
        *,
        business_rule_id: str,
        data_source_id: str,
        actor_user_id: str,
        event_type: str,
        previous_review_status: str | None,
        review_status: str,
        previous_publication_status: str | None,
        publication_status: str,
        content_hash: str,
        reason_code: str,
        created_at: datetime,
        event_id: str | None = None,
    ) -> None:
        connection.execute(
            """INSERT OR IGNORE INTO business_rule_candidate_events
               (event_id, business_rule_id, data_source_id, actor_user_id, event_type,
                previous_review_status, review_status, previous_publication_status,
                publication_status, content_hash, reason_code, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id or uuid.uuid4().hex,
                business_rule_id,
                data_source_id,
                actor_user_id,
                event_type,
                previous_review_status,
                review_status,
                previous_publication_status,
                publication_status,
                content_hash,
                reason_code,
                created_at.isoformat(),
            ),
        )

    def _require_active_source_and_thread(
        self,
        connection: sqlite3.Connection,
        *,
        data_source_id: str,
        thread_id: str,
        owner_user_id: str,
        revision_id: str,
        mdl_digest: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """SELECT b.data_source_id, b.owner_user_id, t.status, t.expires_at,
                      u.role, u.is_active, s.enabled, s.active_revision_id,
                      s.runtime_status, r.status AS revision_status, r.mdl_digest
               FROM chat_thread_data_sources AS b
               JOIN agent_conversation_threads AS t USING(thread_id)
               JOIN auth_users AS u ON u.id=b.owner_user_id
               JOIN wren_data_sources AS s ON s.id=b.data_source_id
               LEFT JOIN wren_revisions AS r
                 ON r.source_id=b.data_source_id AND r.id=s.active_revision_id
               WHERE b.thread_id=? AND b.owner_user_id=? AND b.data_source_id=?""",
            (thread_id, owner_user_id, data_source_id),
        ).fetchone()
        if row is None or row["status"] != "active" or not row["is_active"]:
            raise BusinessRuleNotFound("thread or source is unavailable")
        if _time(row["expires_at"]) <= self._now():
            raise BusinessRuleNotFound("thread has expired")
        if not row["enabled"] or row["runtime_status"] != "ready":
            raise BusinessRuleNotFound("source is unavailable")
        if row["role"] not in {"admin", "member"}:
            raise BusinessRuleForbidden("active account required")
        if row["role"] == "member" and connection.execute(
            """SELECT 1 FROM auth_user_data_sources
               WHERE user_id=? AND data_source_id=?""",
            (owner_user_id, data_source_id),
        ).fetchone() is None:
            raise BusinessRuleForbidden("source grant required")
        if (
            row["active_revision_id"] != revision_id
            or row["revision_status"] != "active"
            or row["mdl_digest"] != mdl_digest
        ):
            raise BusinessRuleStaleSource("active Wren revision changed")
        return row

    def submit(
        self,
        *,
        data_source_id: str,
        thread_id: str,
        submitter_id: str,
        term: str,
        definition: str,
        mdl_references: tuple[str, ...],
        wren_revision_id: str,
        mdl_digest: str,
        active_rule_terms: tuple[str, ...] = (),
        idempotency_key: str,
    ) -> BusinessRuleCandidate:
        if not term or len(term) > 120 or not definition or len(definition) > 4000:
            raise BusinessRuleValidationError("rule term or definition is invalid")
        if len(mdl_references) > 30 or any(len(item) > 256 for item in mdl_references):
            raise BusinessRuleValidationError("MDL reference list is invalid")
        now = self._now()
        idempotency_hash = _opaque_key(idempotency_key)
        references_json = json.dumps(mdl_references, ensure_ascii=False, separators=(",", ":"))
        semantic_hash = self.content_hash(
            data_source_id=data_source_id,
            term=term,
            definition=definition,
            mdl_references=mdl_references,
            wren_revision_id=wren_revision_id,
            mdl_digest=mdl_digest,
        )
        request_hash = _hash(
            _canonical(
                {
                    "thread_id": thread_id,
                    "content_hash": semantic_hash,
                }
            )
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_active_source_and_thread(
                connection,
                data_source_id=data_source_id,
                thread_id=thread_id,
                owner_user_id=submitter_id,
                revision_id=wren_revision_id,
                mdl_digest=mdl_digest,
            )
            prior = connection.execute(
                """SELECT * FROM business_rule_candidates
                   WHERE submitted_by=? AND idempotency_hash=?""",
                (submitter_id, idempotency_hash),
            ).fetchone()
            if prior is not None:
                if prior["request_hash"] != request_hash:
                    raise BusinessRuleConflict("idempotency key was used for different rule content")
                connection.commit()
                return self._candidate(prior)

            unresolved = "review_status IN ('pending','needs_clarification','needs_revalidation','approved') " \
                "AND publication_status IN ('not_published','queued','publishing','failed')"
            thread_count = connection.execute(
                f"""SELECT COUNT(*) FROM business_rule_candidates
                    WHERE source_thread_id=? AND {unresolved}""",
                (thread_id,),
            ).fetchone()[0]
            source_user_count = connection.execute(
                f"""SELECT COUNT(*) FROM business_rule_candidates
                    WHERE data_source_id=? AND submitted_by=? AND {unresolved}""",
                (data_source_id, submitter_id),
            ).fetchone()[0]
            if (
                thread_count >= self.MAX_PENDING_PER_THREAD
                or source_user_count >= self.MAX_PENDING_PER_SUBMITTER_SOURCE
            ):
                raise BusinessRuleQuotaExceeded(3600)
            cutoff = (now - timedelta(hours=1)).isoformat()
            recent_rules = connection.execute(
                """SELECT COUNT(*), MIN(created_at) FROM business_rule_candidates
                   WHERE submitted_by=? AND created_at>=?""",
                (submitter_id, cutoff),
            ).fetchone()
            recent_examples = connection.execute(
                """SELECT COUNT(*), MIN(created_at) FROM query_example_candidates
                   WHERE submitted_by=? AND created_at>=?""",
                (submitter_id, cutoff),
            ).fetchone()
            if int(recent_rules[0]) + int(recent_examples[0]) >= self.MAX_NEW_CANDIDATES_PER_HOUR:
                oldest = min(
                    _time(value)
                    for value in (recent_rules[1], recent_examples[1])
                    if value is not None
                )
                retry_after = max(
                    1, math.ceil((oldest + timedelta(hours=1) - now).total_seconds())
                )
                raise BusinessRuleQuotaExceeded(retry_after)

            normalized_term = unicodedata.normalize("NFKC", term).casefold().strip()
            candidate_terms = connection.execute(
                """SELECT term FROM business_rule_candidates
                   WHERE data_source_id=? AND term IS NOT NULL
                     AND review_status IN ('pending','needs_clarification','needs_revalidation','approved')""",
                (data_source_id,),
            ).fetchall()
            origin_terms = connection.execute(
                """SELECT term_label FROM business_rule_origins
                   WHERE data_source_id=? AND term_label IS NOT NULL
                     AND publication_status IN ('active','removal_pending')""",
                (data_source_id,),
            ).fetchall()
            exact_term_conflict = any(
                unicodedata.normalize("NFKC", item[0]).casefold().strip() == normalized_term
                for item in (*candidate_terms, *origin_terms)
            ) or any(
                unicodedata.normalize("NFKC", item).casefold().strip() == normalized_term
                for item in active_rule_terms
            )

            rule_id = uuid.uuid4().hex
            expires_at = min(
                now + timedelta(days=90),
                _time(
                    connection.execute(
                        "SELECT expires_at FROM agent_conversation_threads WHERE thread_id=?",
                        (thread_id,),
                    ).fetchone()[0]
                ),
            )
            connection.execute(
                """INSERT INTO business_rule_candidates
                   (business_rule_id, data_source_id, term, definition, mdl_references_json,
                    source_thread_id, base_wren_revision_id, base_mdl_digest, content_hash,
                    submitted_by, idempotency_hash, request_hash, has_exact_term_conflict, review_status,
                    publication_status, version, created_at, updated_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending',
                           'not_published', 1, ?, ?, ?)""",
                (
                    rule_id,
                    data_source_id,
                    term,
                    definition,
                    references_json,
                    thread_id,
                    wren_revision_id,
                    mdl_digest,
                    semantic_hash,
                    submitter_id,
                    idempotency_hash,
                    request_hash,
                    int(exact_term_conflict),
                    now.isoformat(),
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            self._record_event(
                connection,
                business_rule_id=rule_id,
                data_source_id=data_source_id,
                actor_user_id=submitter_id,
                event_type="submitted",
                previous_review_status=None,
                review_status="pending",
                previous_publication_status=None,
                publication_status="not_published",
                content_hash=semantic_hash,
                reason_code="explicit_user_save",
                created_at=now,
            )
            row = connection.execute(
                "SELECT * FROM business_rule_candidates WHERE business_rule_id=?",
                (rule_id,),
            ).fetchone()
            connection.commit()
            return self._candidate(row)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _assert_admin_source(
        self, connection: sqlite3.Connection, *, actor_id: str, data_source_id: str
    ) -> None:
        actor = connection.execute(
            "SELECT role, is_active FROM auth_users WHERE id=?", (actor_id,)
        ).fetchone()
        source = connection.execute(
            "SELECT enabled FROM wren_data_sources WHERE id=?", (data_source_id,)
        ).fetchone()
        if actor is None or not actor["is_active"] or actor["role"] != "admin":
            raise BusinessRuleForbidden("admin role required")
        if source is None or not source["enabled"]:
            raise BusinessRuleNotFound("source is unavailable")

    def _assert_candidate_owner(
        self, connection: sqlite3.Connection, row: sqlite3.Row, actor_id: str
    ) -> None:
        if row["submitted_by"] != actor_id:
            raise BusinessRuleNotFound("candidate is unavailable")
        if row["source_thread_id"] is None:
            raise BusinessRuleNotFound("candidate no longer has an active source thread")
        thread = connection.execute(
            """SELECT b.data_source_id, b.owner_user_id, t.status, t.expires_at,
                      u.role, u.is_active, s.enabled, s.runtime_status
               FROM chat_thread_data_sources AS b
               JOIN agent_conversation_threads AS t USING(thread_id)
               JOIN auth_users AS u ON u.id=b.owner_user_id
               JOIN wren_data_sources AS s ON s.id=b.data_source_id
               WHERE b.thread_id=? AND b.owner_user_id=? AND b.data_source_id=?""",
            (row["source_thread_id"], actor_id, row["data_source_id"]),
        ).fetchone()
        if (
            thread is None
            or thread["status"] != "active"
            or not thread["is_active"]
            or thread["role"] not in {"admin", "member"}
            or not thread["enabled"]
            or thread["runtime_status"] != "ready"
            or _time(thread["expires_at"]) <= self._now()
        ):
            raise BusinessRuleNotFound("candidate is unavailable")
        if thread["role"] == "member" and connection.execute(
            """SELECT 1 FROM auth_user_data_sources
               WHERE user_id=? AND data_source_id=?""",
            (actor_id, row["data_source_id"]),
        ).fetchone() is None:
            raise BusinessRuleForbidden("source grant required")

    def get_candidate(self, business_rule_id: str) -> BusinessRuleCandidate:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM business_rule_candidates WHERE business_rule_id=?",
                (business_rule_id,),
            ).fetchone()
            if row is None:
                raise BusinessRuleNotFound("candidate is unavailable")
            return self._candidate(row)
        finally:
            connection.close()

    def list_candidates(
        self,
        *,
        actor_id: str,
        is_admin: bool,
        data_source_id: str,
        status: str | None = None,
        limit: int = 50,
    ) -> tuple[BusinessRuleCandidate, ...]:
        if not 1 <= limit <= 100:
            raise BusinessRuleValidationError("candidate page size is invalid")
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            source = connection.execute(
                "SELECT enabled FROM wren_data_sources WHERE id=?", (data_source_id,)
            ).fetchone()
            if source is None or not source["enabled"]:
                raise BusinessRuleNotFound("source is unavailable")
            actor = connection.execute(
                "SELECT role, is_active FROM auth_users WHERE id=?", (actor_id,)
            ).fetchone()
            if actor is None or not actor["is_active"]:
                raise BusinessRuleForbidden("active account required")
            if is_admin:
                if actor["role"] != "admin":
                    raise BusinessRuleForbidden("admin role required")
            else:
                if actor["role"] != "member" or connection.execute(
                    """SELECT 1 FROM auth_user_data_sources
                       WHERE user_id=? AND data_source_id=?""",
                    (actor_id, data_source_id),
                ).fetchone() is None:
                    raise BusinessRuleForbidden("source grant required")
            clauses = ["data_source_id=?"]
            parameters: list[object] = [data_source_id]
            if not is_admin:
                clauses.append("submitted_by=?")
                parameters.append(actor_id)
            if status:
                if status not in {item.value for item in BusinessRuleReviewStatus}:
                    raise BusinessRuleValidationError("candidate status is invalid")
                clauses.append("review_status=?")
                parameters.append(status)
            parameters.append(limit)
            rows = connection.execute(
                f"""SELECT * FROM business_rule_candidates WHERE {' AND '.join(clauses)}
                    ORDER BY created_at DESC, business_rule_id LIMIT ?""",
                parameters,
            ).fetchall()
            connection.commit()
            return tuple(self._candidate(row) for row in rows)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _transition(
        self,
        business_rule_id: str,
        *,
        actor_id: str,
        expected_version: int,
        event_type: str,
        reason_code: str,
        target_review: str,
        target_publication: str,
        actor_role: str,
        allowed_review_statuses: frozenset[str] | None = None,
        current_source_context: tuple[str, str] | None = None,
        unique_term_required: bool = False,
        active_rule_terms: tuple[str, ...] = (),
        clarification_question: str | None = None,
        replacement: tuple[str, str, tuple[str, ...], str, str, str] | None = None,
    ) -> BusinessRuleCandidate:
        now = self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM business_rule_candidates WHERE business_rule_id=?",
                (business_rule_id,),
            ).fetchone()
            if row is None:
                raise BusinessRuleNotFound("candidate is unavailable")
            if _time(row["expires_at"]) <= now:
                raise BusinessRuleConflict("candidate has expired")
            if actor_role in {"admin", "system"}:
                if actor_role == "admin":
                    self._assert_admin_source(
                        connection, actor_id=actor_id, data_source_id=row["data_source_id"]
                    )
                elif not actor_id.startswith("system:"):
                    raise BusinessRuleForbidden("system transition actor is invalid")
                allowed = allowed_review_statuses or frozenset(
                    {"pending", "needs_clarification", "needs_revalidation"}
                )
                if row["review_status"] not in allowed:
                    raise BusinessRuleConflict("candidate is no longer reviewable")
            else:
                actor = connection.execute(
                    "SELECT role, is_active FROM auth_users WHERE id=?", (actor_id,)
                ).fetchone()
                if (
                    actor is None
                    or not actor["is_active"]
                    or actor["role"] not in {"admin", "member"}
                ):
                    raise BusinessRuleForbidden("active account required")
                self._assert_candidate_owner(connection, row, actor_id)
                allowed = allowed_review_statuses or frozenset(
                    {"pending", "needs_clarification", "needs_revalidation", "approved"}
                )
                if row["review_status"] not in allowed:
                    raise BusinessRuleConflict("candidate can no longer be changed")
            if row["version"] != expected_version:
                raise BusinessRuleConflict("candidate changed; refresh before retrying")
            if row["publication_status"] in {"publishing", "active", "removal_pending", "removed"}:
                raise BusinessRuleConflict("candidate publication has already started")

            if current_source_context is not None:
                revision_id, mdl_digest = current_source_context
                source = connection.execute(
                    """SELECT s.enabled, s.active_revision_id, s.runtime_status,
                              r.status AS revision_status, r.mdl_digest
                       FROM wren_data_sources AS s
                       LEFT JOIN wren_revisions AS r
                         ON r.source_id=s.id AND r.id=s.active_revision_id
                       WHERE s.id=?""",
                    (row["data_source_id"],),
                ).fetchone()
                if (
                    source is None
                    or not source["enabled"]
                    or source["runtime_status"] != "ready"
                    or source["active_revision_id"] != revision_id
                    or source["revision_status"] != "active"
                    or source["mdl_digest"] != mdl_digest
                    or row["base_wren_revision_id"] != revision_id
                    or row["base_mdl_digest"] != mdl_digest
                ):
                    raise BusinessRuleStaleSource("source semantics changed during review")

            if unique_term_required:
                normalized_term = unicodedata.normalize("NFKC", row["term"] or "").casefold().strip()
                candidate_terms = connection.execute(
                    """SELECT term FROM business_rule_candidates
                       WHERE data_source_id=? AND business_rule_id<>? AND term IS NOT NULL
                         AND review_status IN ('pending','needs_clarification','needs_revalidation','approved')""",
                    (row["data_source_id"], business_rule_id),
                ).fetchall()
                origin_terms = connection.execute(
                    """SELECT term_label FROM business_rule_origins
                       WHERE data_source_id=? AND business_rule_id<>? AND term_label IS NOT NULL
                         AND publication_status IN ('active','removal_pending')""",
                    (row["data_source_id"], business_rule_id),
                ).fetchall()
                if any(
                    unicodedata.normalize("NFKC", item[0]).casefold().strip() == normalized_term
                    for item in (*candidate_terms, *origin_terms)
                ) or any(
                    unicodedata.normalize("NFKC", item).casefold().strip() == normalized_term
                    for item in active_rule_terms
                ):
                    raise BusinessRuleConflict(
                        "an exact term conflict must be resolved before approval"
                    )

            if replacement is not None:
                self._require_active_source_and_thread(
                    connection,
                    data_source_id=row["data_source_id"],
                    thread_id=row["source_thread_id"],
                    owner_user_id=actor_id,
                    revision_id=replacement[3],
                    mdl_digest=replacement[4],
                )

            term = row["term"]
            definition = row["definition"]
            refs_json = row["mdl_references_json"]
            content_hash = row["content_hash"]
            base_revision = row["base_wren_revision_id"]
            base_digest = row["base_mdl_digest"]
            exact_term_conflict = bool(row["has_exact_term_conflict"])
            if replacement is not None:
                term, definition, references, base_revision, base_digest, content_hash = replacement
                refs_json = json.dumps(references, ensure_ascii=False, separators=(",", ":"))
                normalized_term = unicodedata.normalize("NFKC", term).casefold().strip()
                other_candidates = connection.execute(
                    """SELECT term FROM business_rule_candidates
                       WHERE data_source_id=? AND business_rule_id<>? AND term IS NOT NULL
                         AND review_status IN ('pending','needs_clarification','needs_revalidation','approved')""",
                    (row["data_source_id"], business_rule_id),
                ).fetchall()
                origins = connection.execute(
                    """SELECT term_label FROM business_rule_origins
                       WHERE data_source_id=? AND term_label IS NOT NULL
                         AND publication_status IN ('active','removal_pending')""",
                    (row["data_source_id"],),
                ).fetchall()
                exact_term_conflict = any(
                    unicodedata.normalize("NFKC", item[0]).casefold().strip() == normalized_term
                    for item in (*other_candidates, *origins)
                ) or any(
                    unicodedata.normalize("NFKC", item).casefold().strip() == normalized_term
                    for item in active_rule_terms
                )
            publication = target_publication
            if event_type == "withdrawn" and row["publication_status"] == "queued":
                publication = "not_published"
            version = expected_version + 1
            connection.execute(
                """UPDATE business_rule_candidates SET term=?, definition=?, mdl_references_json=?,
                       base_wren_revision_id=?, base_mdl_digest=?, content_hash=?,
                       has_exact_term_conflict=?,
                       review_status=?, publication_status=?, clarification_question=?,
                       reviewed_by=CASE WHEN ? IN ('admin','system') THEN ? ELSE reviewed_by END,
                       reviewed_at=CASE WHEN ? IN ('admin','system') THEN ? ELSE reviewed_at END,
                       review_reason_code=?, version=?, updated_at=?
                   WHERE business_rule_id=? AND version=?""",
                (
                    term,
                    definition,
                    refs_json,
                    base_revision,
                    base_digest,
                    content_hash,
                    int(exact_term_conflict),
                    target_review,
                    publication,
                    clarification_question,
                    actor_role,
                    actor_id,
                    actor_role,
                    now.isoformat(),
                    reason_code,
                    version,
                    now.isoformat(),
                    business_rule_id,
                    expected_version,
                ),
            )
            self._record_event(
                connection,
                business_rule_id=business_rule_id,
                data_source_id=row["data_source_id"],
                actor_user_id=actor_id,
                event_type=event_type,
                previous_review_status=row["review_status"],
                review_status=target_review,
                previous_publication_status=row["publication_status"],
                publication_status=publication,
                content_hash=content_hash,
                reason_code=reason_code,
                created_at=now,
            )
            updated = connection.execute(
                "SELECT * FROM business_rule_candidates WHERE business_rule_id=?",
                (business_rule_id,),
            ).fetchone()
            connection.commit()
            return self._candidate(updated)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def request_clarification(
        self,
        business_rule_id: str,
        *,
        actor_id: str,
        expected_version: int,
        question: str,
    ) -> BusinessRuleCandidate:
        return self._transition(
            business_rule_id,
            actor_id=actor_id,
            expected_version=expected_version,
            event_type="clarification_requested",
            reason_code="admin_requires_more_detail",
            target_review="needs_clarification",
            target_publication="not_published",
            actor_role="admin",
            allowed_review_statuses=frozenset(
                {"pending", "needs_clarification", "needs_revalidation", "approved"}
            ),
            clarification_question=question,
        )

    def respond_to_clarification(
        self,
        business_rule_id: str,
        *,
        actor_id: str,
        expected_version: int,
        term: str,
        definition: str,
        mdl_references: tuple[str, ...],
        wren_revision_id: str,
        mdl_digest: str,
        active_rule_terms: tuple[str, ...] = (),
    ) -> BusinessRuleCandidate:
        semantic_hash = self.content_hash(
            data_source_id=self.get_candidate(business_rule_id).data_source_id,
            term=term,
            definition=definition,
            mdl_references=mdl_references,
            wren_revision_id=wren_revision_id,
            mdl_digest=mdl_digest,
        )
        return self._transition(
            business_rule_id,
            actor_id=actor_id,
            expected_version=expected_version,
            event_type="clarification_responded",
            reason_code="submitter_updated_definition",
            target_review="pending",
            target_publication="not_published",
            actor_role="submitter",
            allowed_review_statuses=frozenset(
                {"needs_clarification", "needs_revalidation"}
            ),
            clarification_question=None,
            replacement=(term, definition, mdl_references, wren_revision_id, mdl_digest, semantic_hash),
            active_rule_terms=active_rule_terms,
        )

    def mark_needs_revalidation(
        self,
        business_rule_id: str,
        *,
        actor_id: str,
        expected_version: int,
        actor_role: str = "admin",
    ) -> BusinessRuleCandidate:
        return self._transition(
            business_rule_id,
            actor_id=actor_id,
            expected_version=expected_version,
            event_type="needs_revalidation",
            reason_code="active_semantics_changed",
            target_review="needs_revalidation",
            target_publication="not_published",
            actor_role=actor_role,
            allowed_review_statuses=frozenset(
                {"pending", "needs_clarification", "needs_revalidation", "approved"}
            ),
            clarification_question=(
                "数据源语义版本已变化，请基于当前 MDL 重新确认定义和引用。"
            ),
        )

    def approve(
        self,
        business_rule_id: str,
        *,
        actor_id: str,
        expected_version: int,
        wren_revision_id: str,
        mdl_digest: str,
        active_rule_terms: tuple[str, ...] = (),
    ) -> BusinessRuleCandidate:
        candidate = self.get_candidate(business_rule_id)
        if candidate.base_wren_revision_id != wren_revision_id or candidate.base_mdl_digest != mdl_digest:
            self.mark_needs_revalidation(
                business_rule_id,
                actor_id=actor_id,
                expected_version=expected_version,
            )
            raise BusinessRuleStaleSource("source semantics changed; candidate must be revalidated")
        try:
            return self._transition(
                business_rule_id,
                actor_id=actor_id,
                expected_version=expected_version,
                event_type="approved",
                reason_code="admin_approved",
                target_review="approved",
                target_publication="queued",
                actor_role="admin",
                allowed_review_statuses=frozenset({"pending"}),
                current_source_context=(wren_revision_id, mdl_digest),
                unique_term_required=True,
                active_rule_terms=active_rule_terms,
            )
        except BusinessRuleStaleSource:
            # The active revision may have changed between application validation and
            # the compare-and-set transaction. Persist the revalidation state too.
            self.mark_needs_revalidation(
                business_rule_id,
                actor_id=actor_id,
                expected_version=expected_version,
            )
            raise

    def reject(
        self,
        business_rule_id: str,
        *,
        actor_id: str,
        expected_version: int,
        reason_code: str,
    ) -> BusinessRuleCandidate:
        if reason_code not in {
            "duplicate_term", "ambiguous_definition", "unsupported_scope",
            "invalid_reference", "conflicting_rule", "other",
        }:
            raise BusinessRuleValidationError("review reason code is invalid")
        return self._transition(
            business_rule_id,
            actor_id=actor_id,
            expected_version=expected_version,
            event_type="rejected",
            reason_code=reason_code,
            target_review="rejected",
            target_publication="not_published",
            actor_role="admin",
            allowed_review_statuses=frozenset(
                {"pending", "needs_clarification", "needs_revalidation"}
            ),
        )

    def withdraw(
        self, business_rule_id: str, *, actor_id: str, expected_version: int
    ) -> BusinessRuleCandidate:
        return self._transition(
            business_rule_id,
            actor_id=actor_id,
            expected_version=expected_version,
            event_type="withdrawn",
            reason_code="submitter_withdrew",
            target_review="withdrawn",
            target_publication="not_published",
            actor_role="submitter",
            allowed_review_statuses=frozenset(
                {"pending", "needs_clarification", "needs_revalidation", "approved"}
            ),
        )

    def expire_candidates(self, *, limit: int = 100) -> int:
        now = self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT * FROM business_rule_candidates
                   WHERE expires_at<=? AND review_status IN
                     ('pending','needs_clarification','needs_revalidation','approved')
                     AND publication_status IN ('not_published','queued','failed')
                   ORDER BY expires_at LIMIT ?""",
                (now.isoformat(), limit),
            ).fetchall()
            for row in rows:
                updated = connection.execute(
                    """UPDATE business_rule_candidates SET review_status='expired',
                           publication_status='removed', term=NULL, definition=NULL,
                           mdl_references_json=NULL, clarification_question=NULL,
                           version=version+1, updated_at=?
                       WHERE business_rule_id=? AND version=?""",
                    (now.isoformat(), row["business_rule_id"], row["version"]),
                )
                if updated.rowcount:
                    self._record_event(
                        connection,
                        business_rule_id=row["business_rule_id"],
                        data_source_id=row["data_source_id"],
                        actor_user_id="system:candidate-expiry",
                        event_type="expired",
                        previous_review_status=row["review_status"],
                        review_status="expired",
                        previous_publication_status=row["publication_status"],
                        publication_status="removed",
                        content_hash=row["content_hash"],
                        reason_code="candidate_ttl_elapsed",
                        created_at=now,
                    )
            connection.commit()
            return len(rows)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def purge_expired_origins(
        self, *, now: datetime | None = None, limit: int = 500
    ) -> int:
        """Remove redacted lineage after 30 days, once Wren confirms removal."""
        if not 1 <= limit <= 5000:
            raise BusinessRuleValidationError("origin purge page size is invalid")
        moment = now or self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT data_source_id, business_rule_id FROM business_rule_origins
                   WHERE publication_status='removed' AND purge_after IS NOT NULL
                     AND purge_after<=?
                   ORDER BY purge_after, data_source_id, business_rule_id LIMIT ?""",
                (moment.isoformat(), limit),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """DELETE FROM business_rule_origins
                       WHERE data_source_id=? AND business_rule_id=?
                         AND publication_status='removed' AND purge_after<=?""",
                    (row["data_source_id"], row["business_rule_id"], moment.isoformat()),
                )
            connection.commit()
            return len(rows)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_published(
        self,
        business_rule_id: str,
        *,
        data_source_id: str,
        operation_id: str,
        base_wren_revision_id: str,
        base_mdl_digest: str,
        wren_revision_id: str,
        actor_id: str = "system:publisher",
    ) -> None:
        """Move provenance only when the durable operation proves target ancestry."""
        candidate = self.get_candidate(business_rule_id)
        if (
            candidate.publication_status == BusinessRulePublicationStatus.ACTIVE
            and candidate.data_source_id == data_source_id
        ):
            with self._connect() as connection:
                origin = connection.execute(
                    "SELECT active_wren_revision_id, publication_status FROM business_rule_origins "
                    "WHERE data_source_id=? AND business_rule_id=?",
                    (data_source_id, business_rule_id),
                ).fetchone()
            if (
                origin is not None
                and origin["active_wren_revision_id"] == wren_revision_id
                and origin["publication_status"] in {"active", "removal_pending"}
            ):
                return
        if not operation_id or len(operation_id) > 128:
            raise BusinessRuleValidationError("publication operation is invalid")
        if (
            candidate.base_wren_revision_id != base_wren_revision_id
            or candidate.base_mdl_digest != base_mdl_digest
        ):
            self.mark_needs_revalidation(
                business_rule_id,
                actor_id=actor_id,
                expected_version=candidate.version,
                actor_role="system",
            )
            raise BusinessRuleStaleSource(
                "candidate base revision changed before Wren publication"
            )
        now = self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                """SELECT operation.*, revision.parent_revision_id,
                          revision.publication_operation_id, revision.mdl_digest AS revision_digest,
                          revision.status AS revision_status, source.active_revision_id
                   FROM wren_operations AS operation
                   JOIN wren_revisions AS revision
                     ON revision.source_id=operation.source_id
                    AND revision.id=operation.target_revision_id
                   JOIN wren_data_sources AS source ON source.id=operation.source_id
                   WHERE operation.id=? AND operation.source_id=?""",
                (operation_id, data_source_id),
            ).fetchone()
            try:
                operation_payload = json.loads(operation["payload_json"] or "{}") if operation else {}
            except (TypeError, json.JSONDecodeError) as exc:
                raise BusinessRuleConflict("publisher operation record is invalid") from exc
            if (
                operation is None
                or operation["operation_type"] != "business_rule_publish"
                or operation["status"] != "running"
                or operation["phase"] not in {"generation_persisted", "runtime_activated"}
                or operation["base_revision_id"] != base_wren_revision_id
                or operation["base_mdl_digest"] != base_mdl_digest
                or operation["target_revision_id"] != wren_revision_id
                or operation["target_mdl_digest"] != operation["revision_digest"]
                or operation["parent_revision_id"] != base_wren_revision_id
                or operation["publication_operation_id"] != operation_id
                or operation["active_revision_id"] != wren_revision_id
                or operation["revision_status"] != "active"
                or operation_payload.get("business_rule_id") != business_rule_id
            ):
                raise BusinessRuleConflict("target Wren revision is not bound to this publication operation")
            row = connection.execute(
                """SELECT candidate.*, thread.status AS thread_status,
                          thread.expires_at AS thread_expires_at,
                          source.enabled AS source_enabled,
                          source.runtime_status AS source_runtime_status,
                          source.active_revision_id AS active_revision_id,
                          revision.status AS active_revision_status
                   FROM business_rule_candidates AS candidate
                   JOIN agent_conversation_threads AS thread
                     ON thread.thread_id=candidate.source_thread_id
                   JOIN wren_data_sources AS source ON source.id=candidate.data_source_id
                   LEFT JOIN wren_revisions AS revision
                     ON revision.source_id=source.id AND revision.id=source.active_revision_id
                   WHERE business_rule_id=? AND data_source_id=?""",
                (business_rule_id, data_source_id),
            ).fetchone()
            if (
                row is None
                or row["review_status"] != "approved"
                or row["publication_status"] != "publishing"
                or not row["source_thread_id"]
                or row["thread_status"] != "active"
                or _time(row["thread_expires_at"]) <= now
                or not row["source_enabled"]
                or row["source_runtime_status"] != "ready"
                or row["active_revision_id"] != wren_revision_id
                or row["active_revision_status"] != "active"
            ):
                raise BusinessRuleConflict("candidate is not publishable")
            if (
                row["base_wren_revision_id"] != base_wren_revision_id
                or row["base_mdl_digest"] != base_mdl_digest
            ):
                raise BusinessRuleStaleSource(
                    "candidate base revision changed before Wren publication"
                )
            connection.execute(
                """INSERT INTO business_rule_origins
                   (data_source_id, business_rule_id, source_thread_id, term_label,
                    content_hash, active_wren_revision_id, publication_status, published_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
                   ON CONFLICT(data_source_id, business_rule_id) DO UPDATE SET
                     source_thread_id=excluded.source_thread_id,
                     term_label=excluded.term_label,
                     content_hash=excluded.content_hash,
                     active_wren_revision_id=excluded.active_wren_revision_id,
                     publication_status='active', published_at=excluded.published_at,
                     redacted_at=NULL, purge_after=NULL""",
                (
                    data_source_id,
                    business_rule_id,
                    row["source_thread_id"],
                    row["term"],
                    row["content_hash"],
                    wren_revision_id,
                    now.isoformat(),
                ),
            )
            connection.execute(
                """UPDATE business_rule_candidates SET term=NULL, definition=NULL,
                       mdl_references_json=NULL, source_thread_id=NULL,
                       clarification_question=NULL, publication_status='active',
                       version=version+1, updated_at=? WHERE business_rule_id=?""",
                (now.isoformat(), business_rule_id),
            )
            self._record_event(
                connection,
                business_rule_id=business_rule_id,
                data_source_id=data_source_id,
                actor_user_id=actor_id,
                event_type="published",
                previous_review_status="approved",
                review_status="approved",
                previous_publication_status=row["publication_status"],
                publication_status="active",
                content_hash=row["content_hash"],
                reason_code="runtime_activated",
                created_at=now,
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_removed(
        self,
        *,
        data_source_id: str,
        business_rule_ids: tuple[str, ...],
        operation_id: str,
        wren_revision_id: str,
        actor_id: str = "system:publisher",
    ) -> None:
        """Mark journal-suppressed rules offline after the target Wren revision is active."""
        normalized_ids = tuple(sorted(set(business_rule_ids)))
        if not normalized_ids or len(normalized_ids) > 500 or any(
            len(rule_id) != 32
            or any(character not in "0123456789abcdef" for character in rule_id)
            for rule_id in normalized_ids
        ):
            raise BusinessRuleValidationError("business rule removal batch is invalid")
        if not actor_id or len(actor_id) > 128:
            raise BusinessRuleValidationError("publication actor is invalid")
        if not operation_id or len(operation_id) > 128:
            raise BusinessRuleValidationError("removal operation is invalid")
        now = self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                """SELECT operation.*, revision.parent_revision_id,
                          revision.publication_operation_id, revision.mdl_digest AS revision_digest,
                          revision.status AS revision_status, source.active_revision_id
                   FROM wren_operations AS operation
                   JOIN wren_revisions AS revision
                     ON revision.source_id=operation.source_id
                    AND revision.id=operation.target_revision_id
                   JOIN wren_data_sources AS source ON source.id=operation.source_id
                   WHERE operation.id=? AND operation.source_id=?""",
                (operation_id, data_source_id),
            ).fetchone()
            try:
                operation_payload = json.loads(operation["payload_json"] or "{}") if operation else {}
                payload_ids = tuple(sorted(set(operation_payload.get("business_rule_ids", ()))))
            except (TypeError, json.JSONDecodeError) as exc:
                raise BusinessRuleConflict("removal operation record is invalid") from exc
            if (
                operation is None
                or operation["operation_type"] != "business_rule_remove"
                or operation["status"] != "running"
                or operation["phase"] not in {"generation_persisted", "runtime_activated"}
                or operation["target_revision_id"] != wren_revision_id
                or operation["target_mdl_digest"] != operation["revision_digest"]
                or operation["parent_revision_id"] != operation["base_revision_id"]
                or operation["publication_operation_id"] != operation_id
                or operation["active_revision_id"] != wren_revision_id
                or operation["revision_status"] != "active"
                or payload_ids != normalized_ids
            ):
                raise BusinessRuleConflict("target Wren revision is not bound to this removal operation")
            active = connection.execute(
                """SELECT source.active_revision_id, source.enabled, source.runtime_status,
                          revision.status AS revision_status
                   FROM wren_data_sources AS source
                   LEFT JOIN wren_revisions AS revision
                     ON revision.source_id=source.id AND revision.id=source.active_revision_id
                   WHERE source.id=?""",
                (data_source_id,),
            ).fetchone()
            if (
                active is None
                or active["active_revision_id"] != wren_revision_id
                or active["revision_status"] != "active"
                or (
                    active["enabled"] and active["runtime_status"] != "ready"
                )
                or (
                    not active["enabled"] and active["runtime_status"] != "disabled"
                )
            ):
                raise BusinessRuleConflict("rule removal revision is not active")
            for rule_id in normalized_ids:
                origin = connection.execute(
                    """SELECT * FROM business_rule_origins
                       WHERE data_source_id=? AND business_rule_id=?""",
                    (data_source_id, rule_id),
                ).fetchone()
                if origin is None:
                    raise BusinessRuleNotFound("suppressed rule origin is unavailable")
                if origin["publication_status"] == "removed":
                    continue
                if origin["publication_status"] != "removal_pending":
                    raise BusinessRuleConflict("rule has no pending removal")
                source_thread_id = origin["source_thread_id"]
                source_thread_hash = origin["source_thread_hash"]
                if source_thread_id:
                    source_thread_hash = self.deletion_journal.keyed_audit_hash(
                        source_thread_id
                    )
                connection.execute(
                    """UPDATE business_rule_origins SET source_thread_id=NULL,
                           source_thread_hash=?, term_label=NULL,
                           publication_status='removed',
                           redacted_at=COALESCE(redacted_at, ?),
                           purge_after=COALESCE(purge_after, ?),
                           active_wren_revision_id=?
                       WHERE data_source_id=? AND business_rule_id=?""",
                    (
                        source_thread_hash,
                        now.isoformat(),
                        (now + timedelta(days=30)).isoformat(),
                        wren_revision_id,
                        data_source_id,
                        rule_id,
                    ),
                )
                candidate = connection.execute(
                    """SELECT review_status, publication_status, content_hash
                       FROM business_rule_candidates WHERE business_rule_id=?""",
                    (rule_id,),
                ).fetchone()
                review_status = candidate["review_status"] if candidate else "revoked"
                content_hash = candidate["content_hash"] if candidate else origin["content_hash"]
                previous_publication = (
                    candidate["publication_status"] if candidate else "removal_pending"
                )
                if candidate:
                    connection.execute(
                        """UPDATE business_rule_candidates SET publication_status='removed',
                               version=version+1, updated_at=? WHERE business_rule_id=?""",
                        (now.isoformat(), rule_id),
                    )
                self._record_event(
                    connection,
                    business_rule_id=rule_id,
                    data_source_id=data_source_id,
                    actor_user_id=actor_id,
                    event_type="removal_published",
                    previous_review_status=review_status,
                    review_status=review_status,
                    previous_publication_status=previous_publication,
                    publication_status="removed",
                    content_hash=content_hash,
                    reason_code="runtime_removal_activated",
                    created_at=now,
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_publishing(
        self,
        business_rule_id: str,
        *,
        actor_id: str,
        expected_version: int,
        base_wren_revision_id: str,
        base_mdl_digest: str,
    ) -> BusinessRuleCandidate:
        now = self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_admin_source(
                connection,
                actor_id=actor_id,
                data_source_id=self._candidate_source_id(connection, business_rule_id),
            )
            row = connection.execute(
                """SELECT candidate.*, source.enabled, source.runtime_status,
                          source.active_revision_id, revision.status AS revision_status,
                          revision.mdl_digest AS active_mdl_digest,
                          thread.status AS thread_status, thread.expires_at
                   FROM business_rule_candidates AS candidate
                   JOIN wren_data_sources AS source ON source.id=candidate.data_source_id
                   JOIN wren_revisions AS revision
                     ON revision.source_id=source.id AND revision.id=source.active_revision_id
                   JOIN agent_conversation_threads AS thread
                     ON thread.thread_id=candidate.source_thread_id
                   WHERE candidate.business_rule_id=?""",
                (business_rule_id,),
            ).fetchone()
            if (
                row is None
                or row["review_status"] != "approved"
                or row["publication_status"] not in {"queued", "failed"}
                or row["version"] != expected_version
                or row["active_revision_id"] != base_wren_revision_id
                or row["active_mdl_digest"] != base_mdl_digest
                or row["base_wren_revision_id"] != base_wren_revision_id
                or row["base_mdl_digest"] != base_mdl_digest
                or row["revision_status"] != "active"
                or not row["enabled"]
                or row["runtime_status"] != "ready"
                or row["thread_status"] != "active"
                or _time(row["expires_at"]) <= now
            ):
                raise BusinessRuleConflict("candidate is not ready for publication")
            connection.execute(
                """UPDATE business_rule_candidates SET publication_status='publishing',
                       version=version+1, updated_at=? WHERE business_rule_id=? AND version=?""",
                (now.isoformat(), business_rule_id, expected_version),
            )
            self._record_event(
                connection,
                business_rule_id=business_rule_id,
                data_source_id=row["data_source_id"],
                actor_user_id=actor_id,
                event_type="publication_started",
                previous_review_status="approved",
                review_status="approved",
                previous_publication_status=row["publication_status"],
                publication_status="publishing",
                content_hash=row["content_hash"],
                reason_code="admin_started_wren_publication",
                created_at=now,
            )
            updated = connection.execute(
                "SELECT * FROM business_rule_candidates WHERE business_rule_id=?",
                (business_rule_id,),
            ).fetchone()
            connection.commit()
            return self._candidate(updated)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _candidate_source_id(connection: sqlite3.Connection, business_rule_id: str) -> str:
        row = connection.execute(
            "SELECT data_source_id FROM business_rule_candidates WHERE business_rule_id=?",
            (business_rule_id,),
        ).fetchone()
        if row is None:
            raise BusinessRuleNotFound("candidate is unavailable")
        return row["data_source_id"]

    def mark_publication_failed(
        self, business_rule_id: str, *, actor_id: str = "system:publisher", reason_code: str
    ) -> None:
        now = self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM business_rule_candidates WHERE business_rule_id=?",
                (business_rule_id,),
            ).fetchone()
            if row is None or row["publication_status"] != "publishing":
                connection.commit()
                return
            connection.execute(
                """UPDATE business_rule_candidates SET publication_status='failed',
                       version=version+1, updated_at=? WHERE business_rule_id=? AND version=?""",
                (now.isoformat(), business_rule_id, row["version"]),
            )
            self._record_event(
                connection,
                business_rule_id=business_rule_id,
                data_source_id=row["data_source_id"],
                actor_user_id=actor_id,
                event_type="publication_failed",
                previous_review_status=row["review_status"],
                review_status=row["review_status"],
                previous_publication_status="publishing",
                publication_status="failed",
                content_hash=row["content_hash"],
                reason_code=reason_code,
                created_at=now,
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_pending_removals(self, *, limit: int = 500) -> dict[str, tuple[str, ...]]:
        if not 1 <= limit <= 5000:
            raise BusinessRuleValidationError("removal page size is invalid")
        self._repair_missing_removal_origins(limit=limit)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT data_source_id, business_rule_id FROM business_rule_origins
                   WHERE publication_status='removal_pending'
                   ORDER BY data_source_id, redacted_at, business_rule_id LIMIT ?""",
                (limit,),
            ).fetchall()
        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(row["data_source_id"], []).append(row["business_rule_id"])
        return {source_id: tuple(sorted(set(ids))) for source_id, ids in grouped.items()}

    def has_pending_removals(self, data_source_id: str) -> bool:
        return self.blocks_runtime_revision(data_source_id, ())

    def blocks_runtime_revision(
        self, data_source_id: str, business_rule_ids: tuple[str, ...]
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM business_rule_origins
                   WHERE data_source_id=? AND publication_status='removal_pending'
                   UNION ALL
                   SELECT 1 FROM business_rule_candidates
                   WHERE data_source_id=? AND publication_status='removal_pending'
                   LIMIT 1""",
                (data_source_id, data_source_id),
            ).fetchone()
            if row is not None:
                return True
            if not business_rule_ids:
                return False
            suppressed = connection.execute(
                """SELECT item_id FROM agent_memory_suppressions
                   WHERE data_source_id=? AND item_type='business_rule'""",
                (data_source_id,),
            ).fetchall()
        return bool(set(business_rule_ids) & {item["item_id"] for item in suppressed})

    def blocks_suppressed_memory_documents(
        self,
        data_source_id: str,
        document_keys: tuple[tuple[str, str], ...],
    ) -> bool:
        """Check selected recalled documents against the durable suppression ledger."""
        keys = tuple(
            (kind, item_id)
            for kind, item_id in document_keys
            if kind in {"business_rule", "query_example"} and item_id
        )
        if not keys:
            return False
        conditions = " OR ".join("(item_type=? AND item_id=?)" for _ in keys)
        parameters: list[str] = [data_source_id]
        for kind, item_id in keys:
            parameters.extend((kind, item_id))
        with self._connect() as connection:
            row = connection.execute(
                f"""SELECT 1 FROM agent_memory_suppressions
                    WHERE data_source_id=? AND ({conditions}) LIMIT 1""",
                tuple(parameters),
            ).fetchone()
        return row is not None

    def _repair_missing_removal_origins(self, *, limit: int) -> None:
        now = self._now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT candidate.* FROM business_rule_candidates AS candidate
                   LEFT JOIN business_rule_origins AS origin
                     ON origin.data_source_id=candidate.data_source_id
                    AND origin.business_rule_id=candidate.business_rule_id
                   WHERE candidate.publication_status='removal_pending'
                     AND origin.business_rule_id IS NULL
                   ORDER BY candidate.data_source_id, candidate.updated_at,
                            candidate.business_rule_id LIMIT ?""",
                (limit,),
            ).fetchall()
            for row in rows:
                source_thread_id = row["source_thread_id"]
                thread_hash = (
                    self.deletion_journal.keyed_audit_hash(source_thread_id)
                    if source_thread_id
                    else self.deletion_journal.keyed_audit_hash(
                        f"business-rule:{row['data_source_id']}:{row['business_rule_id']}"
                    )
                )
                connection.execute(
                    """INSERT OR IGNORE INTO business_rule_origins
                       (data_source_id, business_rule_id, source_thread_id,
                        source_thread_hash, term_label, content_hash,
                        active_wren_revision_id, publication_status, published_at,
                        redacted_at, purge_after)
                       VALUES (?, ?, NULL, ?, NULL, ?, ?, 'removal_pending', ?, ?, ?)""",
                    (
                        row["data_source_id"],
                        row["business_rule_id"],
                        thread_hash,
                        row["content_hash"],
                        row["base_wren_revision_id"],
                        now.isoformat(),
                        now.isoformat(),
                        (now + timedelta(days=30)).isoformat(),
                    ),
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def is_removal_pending(self, data_source_id: str, business_rule_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM business_rule_origins WHERE data_source_id=? "
                "AND business_rule_id=? AND publication_status='removal_pending'",
                (data_source_id, business_rule_id),
            ).fetchone()
        return row is not None

    def preview(
        self, connection: sqlite3.Connection, thread_id: str, source_id: str
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        candidates = connection.execute(
            """SELECT business_rule_id, term FROM business_rule_candidates
               WHERE data_source_id=? AND source_thread_id=? AND term IS NOT NULL""",
            (source_id, thread_id),
        ).fetchall()
        origins = connection.execute(
            """SELECT business_rule_id, term_label FROM business_rule_origins
               WHERE data_source_id=? AND source_thread_id=?""",
            (source_id, thread_id),
        ).fetchall()
        labels_by_id = {
            row["business_rule_id"]: row["term"] or "待审核业务规则"
            for row in candidates
        }
        labels_by_id.update(
            {
                row["business_rule_id"]: row["term_label"] or "已发布业务规则"
                for row in origins
            }
        )
        rule_ids = tuple(sorted(labels_by_id))
        labels = tuple(labels_by_id[rule_id] for rule_id in rule_ids)
        return rule_ids, labels

    def apply(self, connection: sqlite3.Connection, event: JournalEvent) -> None:
        now = event.created_at
        if event.event_type in {"thread_delete", "thread_expire"} and event.thread_id:
            candidates = connection.execute(
                """SELECT * FROM business_rule_candidates
                   WHERE data_source_id=? AND source_thread_id=?""",
                (event.source_id, event.thread_id),
            ).fetchall()
            actor_id = event.actor_id or "system:thread-deletion"
            target_status = "expired" if event.event_type == "thread_expire" else "withdrawn"
            for row in candidates:
                if row["publication_status"] == "publishing":
                    # A Wren build may have reached the activation boundary while
                    # this journal event was waiting for SQLite. Preserve a minimal
                    # removal origin so the durable publisher can remove that rule
                    # after the thread content is deleted.
                    thread_hash = self.deletion_journal.keyed_audit_hash(event.thread_id)
                    connection.execute(
                        """INSERT INTO business_rule_origins
                           (data_source_id, business_rule_id, source_thread_id,
                            source_thread_hash, term_label, content_hash,
                            active_wren_revision_id, publication_status, published_at,
                            redacted_at, purge_after)
                           VALUES (?, ?, NULL, ?, NULL, ?, ?, 'removal_pending', ?, ?, ?)
                           ON CONFLICT(data_source_id, business_rule_id) DO UPDATE SET
                             source_thread_id=NULL, source_thread_hash=excluded.source_thread_hash,
                             term_label=NULL, publication_status='removal_pending',
                             redacted_at=excluded.redacted_at, purge_after=excluded.purge_after""",
                        (
                            row["data_source_id"],
                            row["business_rule_id"],
                            thread_hash,
                            row["content_hash"],
                            row["base_wren_revision_id"],
                            now.isoformat(),
                            now.isoformat(),
                            (now + timedelta(days=30)).isoformat(),
                        ),
                    )
                self._record_event(
                    connection,
                    business_rule_id=row["business_rule_id"],
                    data_source_id=row["data_source_id"],
                    actor_user_id=actor_id,
                    event_type=event.event_type,
                    previous_review_status=row["review_status"],
                    review_status=target_status,
                    previous_publication_status=row["publication_status"],
                    publication_status="removed",
                    content_hash=row["content_hash"],
                    reason_code=event.event_type,
                    created_at=now,
                    event_id=f"journal-{event.sequence}-{row['business_rule_id']}",
                )
            connection.execute(
                "DELETE FROM business_rule_candidates WHERE data_source_id=? AND source_thread_id=?",
                (event.source_id, event.thread_id),
            )
            thread_hash = self.deletion_journal.keyed_audit_hash(event.thread_id)
            for rule_id in event.item_ids:
                origin = connection.execute(
                    """SELECT * FROM business_rule_origins
                       WHERE data_source_id=? AND business_rule_id=? AND source_thread_id=?""",
                    (event.source_id, rule_id, event.thread_id),
                ).fetchone()
                if origin is None:
                    continue
                target_publication = (
                    "removed"
                    if origin["publication_status"] == "removed"
                    else "removal_pending"
                )
                connection.execute(
                    """UPDATE business_rule_origins SET source_thread_id=NULL,
                           source_thread_hash=?, term_label=NULL,
                           publication_status=CASE WHEN publication_status='removed'
                             THEN 'removed' ELSE 'removal_pending' END,
                           redacted_at=?, purge_after=?
                       WHERE data_source_id=? AND business_rule_id=?""",
                    (
                        thread_hash,
                        now.isoformat(),
                        (now + timedelta(days=30)).isoformat(),
                        event.source_id,
                        rule_id,
                    ),
                )
                self._record_event(
                    connection,
                    business_rule_id=rule_id,
                    data_source_id=event.source_id,
                    actor_user_id=actor_id,
                    event_type=event.event_type,
                    previous_review_status="approved",
                    review_status="revoked",
                    previous_publication_status=origin["publication_status"],
                    publication_status=target_publication,
                    content_hash=origin["content_hash"],
                    reason_code=event.event_type,
                    created_at=now,
                    event_id=f"journal-{event.sequence}-{rule_id}",
                )
        elif event.event_type == "business_rule_revoke":
            actor_id = event.actor_id or "system:journal-replay"
            for rule_id in event.item_ids:
                candidate = connection.execute(
                    """SELECT * FROM business_rule_candidates
                       WHERE data_source_id=? AND business_rule_id=?""",
                    (event.source_id, rule_id),
                ).fetchone()
                origin = connection.execute(
                    """SELECT * FROM business_rule_origins
                       WHERE data_source_id=? AND business_rule_id=?""",
                    (event.source_id, rule_id),
                ).fetchone()
                if candidate is not None:
                    old_publication = candidate["publication_status"]
                    new_publication = (
                        "removal_pending"
                        if origin is not None or old_publication in {"publishing", "active"}
                        else "removed"
                    )
                    connection.execute(
                        """UPDATE business_rule_candidates SET review_status='revoked',
                               publication_status=?, term=NULL, definition=NULL,
                               mdl_references_json=NULL, clarification_question=NULL,
                               reviewed_by=?, reviewed_at=?, review_reason_code='admin_revoked',
                               version=version+1, updated_at=?
                           WHERE business_rule_id=?""",
                        (
                            new_publication,
                            actor_id,
                            now.isoformat(),
                            now.isoformat(),
                            rule_id,
                        ),
                    )
                    self._record_event(
                        connection,
                        business_rule_id=rule_id,
                        data_source_id=event.source_id,
                        actor_user_id=actor_id,
                        event_type="revoked",
                        previous_review_status=candidate["review_status"],
                        review_status="revoked",
                        previous_publication_status=old_publication,
                        publication_status=new_publication,
                        content_hash=candidate["content_hash"],
                        reason_code="admin_revoked",
                        created_at=now,
                        event_id=f"journal-{event.sequence}-{rule_id}",
                    )
                    if new_publication == "removal_pending" and origin is None:
                        source_thread_id = candidate["source_thread_id"]
                        thread_hash = (
                            self.deletion_journal.keyed_audit_hash(source_thread_id)
                            if source_thread_id
                            else self.deletion_journal.keyed_audit_hash(
                                f"business-rule:{event.source_id}:{rule_id}"
                            )
                        )
                        connection.execute(
                            """INSERT INTO business_rule_origins
                               (data_source_id, business_rule_id, source_thread_id,
                                source_thread_hash, term_label, content_hash,
                                active_wren_revision_id, publication_status, published_at,
                                redacted_at, purge_after)
                               VALUES (?, ?, NULL, ?, NULL, ?, ?, 'removal_pending', ?, ?, ?)""",
                            (
                                event.source_id,
                                rule_id,
                                thread_hash,
                                candidate["content_hash"],
                                candidate["base_wren_revision_id"],
                                now.isoformat(),
                                now.isoformat(),
                                (now + timedelta(days=30)).isoformat(),
                            ),
                        )
                if origin is not None:
                    source_thread_hash = origin["source_thread_hash"]
                    if origin["source_thread_id"]:
                        source_thread_hash = self.deletion_journal.keyed_audit_hash(
                            origin["source_thread_id"]
                        )
                    connection.execute(
                        """UPDATE business_rule_origins SET source_thread_id=NULL,
                               source_thread_hash=?, term_label=NULL,
                               publication_status=CASE WHEN publication_status='removed'
                                 THEN 'removed' ELSE 'removal_pending' END,
                               redacted_at=COALESCE(redacted_at, ?),
                               purge_after=COALESCE(purge_after, ?)
                           WHERE data_source_id=? AND business_rule_id=?""",
                        (
                            source_thread_hash,
                            now.isoformat(),
                            (now + timedelta(days=30)).isoformat(),
                            event.source_id,
                            rule_id,
                        ),
                    )
