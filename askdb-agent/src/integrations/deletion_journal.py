from __future__ import annotations

import fcntl
import base64
import hashlib
import hmac
import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from cryptography.fernet import Fernet, InvalidToken


class DeletionJournalUnavailable(RuntimeError):
    """The independently durable deletion journal is missing or invalid."""


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


@dataclass(frozen=True)
class JournalEvent:
    sequence: int
    event_id: str
    event_type: str
    source_id: str
    thread_id: str | None
    item_type: str
    item_ids: tuple[str, ...]
    request_hash: str
    created_at: datetime
    previous_hash: str
    record_hash: str
    actor_id: str | None = None

    def record(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source_id": self.source_id,
            "thread_id": self.thread_id,
            "item_type": self.item_type,
            "item_ids": list(self.item_ids),
            "request_hash": self.request_hash,
            "created_at": self.created_at.isoformat(),
            "previous_hash": self.previous_hash,
            "record_hash": self.record_hash,
            "actor_id": self.actor_id,
        }


class EncryptedDeletionJournal:
    """Fernet-encrypted, append-only journal with a global sequence/hash chain."""

    def __init__(self, path: Path, key: str):
        try:
            self._cipher = Fernet(key.encode("ascii"))
            key_material = base64.urlsafe_b64decode(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise DeletionJournalUnavailable("journal key is invalid") from exc
        self._audit_hash_key = hmac.new(
            key_material,
            b"askdb-deletion-journal-audit-hash-v1",
            hashlib.sha256,
        ).digest()
        self.path = Path(path).expanduser().resolve()
        self.identity_path = self.path.with_name(self.path.name + ".identity")
        self.watermark_path = self.path.with_name(self.path.name + ".watermark")
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self._cached_signature: tuple[int, int, int, int] | None = None
        self._cached_events: tuple[JournalEvent, ...] = ()
        self._journal_id: str | None = None

    @classmethod
    def from_environment(
        cls, *, storage_root: Path, corpus_path: Path | None = None
    ) -> EncryptedDeletionJournal:
        storage_root = storage_root.expanduser().resolve()
        configured_path = os.environ.get("ASKDB_MEMORY_JOURNAL_PATH", "").strip()
        path = (
            Path(configured_path).expanduser().resolve()
            if configured_path
            else storage_root / "agent-memory" / "deletion-journal.jsonl"
        )
        journal_root = path.parent
        if path == storage_root or journal_root == storage_root:
            raise DeletionJournalUnavailable(
                "journal must use a separate directory from the application data root"
            )
        if corpus_path is not None:
            corpus_root = corpus_path.expanduser().resolve()
            if (
                journal_root == corpus_root
                or journal_root.is_relative_to(corpus_root)
                or corpus_root.is_relative_to(journal_root)
            ):
                raise DeletionJournalUnavailable(
                    "journal and query corpus must use separate directories"
                )
        key = os.environ.get("ASKDB_MEMORY_JOURNAL_KEY", "").strip()
        if not key:
            key = cls._load_or_create_key(path.with_name("deletion-journal.key"))
        return cls(path, key)

    @staticmethod
    def _load_or_create_key(path: Path) -> str:
        path = Path(os.path.abspath(path.expanduser()))
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = path.with_name(path.name + ".lock")
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(lock_descriptor, 0o600)
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            if path.exists():
                try:
                    if path.is_symlink():
                        raise DeletionJournalUnavailable(
                            "journal key file must not be a symbolic link"
                        )
                    os.chmod(path, 0o600)
                    return path.read_text(encoding="ascii").strip()
                except (OSError, UnicodeError) as exc:
                    raise DeletionJournalUnavailable(
                        "journal key file is unavailable"
                    ) from exc

            key = Fernet.generate_key()
            temporary_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                descriptor = os.open(
                    temporary_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
                try:
                    os.fchmod(descriptor, 0o600)
                    with os.fdopen(descriptor, "wb", closefd=False) as key_file:
                        key_file.write(key)
                        key_file.flush()
                        os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.replace(temporary_path, path)
                os.chmod(path, 0o600)
            finally:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
            directory_descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            return key.decode("ascii")
        except OSError as exc:
            raise DeletionJournalUnavailable(
                "journal key could not be loaded or persisted"
            ) from exc
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def keyed_audit_hash(self, value: str) -> str:
        """Return a domain-separated non-reversible audit hash for a deleted ID."""
        if not isinstance(value, str) or not value or len(value) > 512:
            raise ValueError("audit hash input is invalid")
        return hmac.new(
            self._audit_hash_key,
            b"thread-origin\0" + value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def initialize(
        self, *, expected_journal_id: str | None = None, allow_create: bool = False
    ) -> str:
        """Create a fresh journal only when the caller has proved it is first use.

        The database stores the returned identity. If either durable file later
        disappears, or a different journal is mounted at the path, startup fails
        closed instead of interpreting it as an empty history.
        """
        with self._locked():
            has_journal = self.path.exists()
            has_identity = self.identity_path.exists()
            has_watermark = self.watermark_path.exists()
            if not has_journal and not has_identity and not has_watermark:
                if not allow_create or expected_journal_id is not None:
                    raise DeletionJournalUnavailable("journal is missing")
                journal_id = uuid.uuid4().hex
                self._create_durable_file(self.identity_path, journal_id.encode("ascii") + b"\n")
                self._create_durable_file(self.path, b"")
                self._write_watermark_unlocked(journal_id, 0, "0" * 64)
            elif not (has_journal and has_identity and has_watermark):
                raise DeletionJournalUnavailable("journal initialization is incomplete")

            journal_id = self._read_identity_unlocked()
            if expected_journal_id is not None and journal_id != expected_journal_id:
                raise DeletionJournalUnavailable("journal identity does not match database")
            if self._journal_id is not None and self._journal_id != journal_id:
                raise DeletionJournalUnavailable("journal identity changed")
            self._journal_id = journal_id
            self._read_unlocked()
            return journal_id

    def _create_durable_file(self, path: Path, content: bytes) -> None:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _read_identity_unlocked(self) -> str:
        try:
            identity = self.identity_path.read_text(encoding="ascii").strip()
            if len(identity) != 32 or uuid.UUID(hex=identity).hex != identity:
                raise ValueError("invalid journal identity")
            return identity
        except (OSError, UnicodeError, ValueError) as exc:
            raise DeletionJournalUnavailable("journal identity is missing or invalid") from exc

    def _read_watermark_unlocked(self, journal_id: str) -> tuple[int, str]:
        try:
            payload = json.loads(
                self._cipher.decrypt(self.watermark_path.read_bytes()).decode("utf-8")
            )
            sequence = payload["sequence"]
            record_hash = payload["record_hash"]
            if (
                payload.get("journal_id") != journal_id
                or not isinstance(sequence, int)
                or sequence < 0
                or not isinstance(record_hash, str)
                or len(record_hash) != 64
            ):
                raise ValueError("invalid journal watermark")
            return sequence, record_hash
        except (OSError, UnicodeError, ValueError, KeyError, TypeError, json.JSONDecodeError, InvalidToken) as exc:
            raise DeletionJournalUnavailable("journal watermark is missing or invalid") from exc

    def _write_watermark_unlocked(
        self, journal_id: str, sequence: int, record_hash: str
    ) -> None:
        token = self._cipher.encrypt(
            _canonical(
                {
                    "journal_id": journal_id,
                    "sequence": sequence,
                    "record_hash": record_hash,
                }
            )
        )
        temporary_path = self.watermark_path.with_name(
            f"{self.watermark_path.name}.{uuid.uuid4().hex}.tmp"
        )
        descriptor = os.open(
            temporary_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(token)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
        os.replace(temporary_path, self.watermark_path)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _read_unlocked(self) -> tuple[JournalEvent, ...]:
        if not self.path.exists():
            self._cached_signature = None
            self._cached_events = ()
            raise DeletionJournalUnavailable("journal is missing")
        try:
            journal_id = self._read_identity_unlocked()
            if self._journal_id is not None and journal_id != self._journal_id:
                raise DeletionJournalUnavailable("journal identity changed")
            self._journal_id = journal_id
            stat = self.path.stat()
            signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if signature == self._cached_signature:
                observed_sequence = len(self._cached_events)
                observed_hash = (
                    self._cached_events[-1].record_hash
                    if self._cached_events
                    else "0" * 64
                )
                watermark_sequence, watermark_hash = self._read_watermark_unlocked(journal_id)
                if watermark_sequence > observed_sequence or (
                    watermark_sequence == observed_sequence and watermark_hash != observed_hash
                ):
                    raise DeletionJournalUnavailable("journal is behind its durable watermark")
                if watermark_sequence < observed_sequence:
                    self._write_watermark_unlocked(journal_id, observed_sequence, observed_hash)
                return self._cached_events
            raw = self.path.read_bytes()
            if raw and not raw.endswith(b"\n"):
                raise DeletionJournalUnavailable("journal has a partial trailing record")
            events: list[JournalEvent] = []
            expected_sequence = 1
            previous_hash = "0" * 64
            for line in raw.splitlines():
                if not line:
                    raise DeletionJournalUnavailable("journal contains an empty record")
                payload = json.loads(self._cipher.decrypt(line).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise DeletionJournalUnavailable("journal event shape is invalid")
                record_hash = payload.pop("record_hash", None)
                if not isinstance(record_hash, str) or hashlib.sha256(_canonical(payload)).hexdigest() != record_hash:
                    raise DeletionJournalUnavailable("journal integrity check failed")
                if payload.get("sequence") != expected_sequence:
                    raise DeletionJournalUnavailable("journal sequence is discontinuous")
                if payload.get("previous_hash") != previous_hash:
                    raise DeletionJournalUnavailable("journal hash chain is discontinuous")
                item_ids = payload.get("item_ids")
                if not isinstance(item_ids, list) or any(not isinstance(item, str) for item in item_ids):
                    raise DeletionJournalUnavailable("journal event shape is invalid")
                request_hash = payload.get("request_hash", "")
                if not isinstance(request_hash, str):
                    raise DeletionJournalUnavailable("journal event shape is invalid")
                event_type = payload.get("event_type")
                item_type = payload.get("item_type")
                actor_id = payload.get("actor_id")
                if event_type not in {
                    "thread_delete",
                    "business_rule_revoke",
                    "query_example_revoke",
                }:
                    raise DeletionJournalUnavailable("journal event type is unsupported")
                if item_type not in {"thread", "business_rule", "query_example"}:
                    raise DeletionJournalUnavailable("journal item type is unsupported")
                if actor_id is not None and (
                    not isinstance(actor_id, str) or not actor_id or len(actor_id) > 128
                ):
                    raise DeletionJournalUnavailable("journal actor is invalid")
                if not isinstance(payload.get("event_id"), str) or not payload["event_id"]:
                    raise DeletionJournalUnavailable("journal event shape is invalid")
                if not isinstance(payload.get("source_id"), str) or not payload["source_id"]:
                    raise DeletionJournalUnavailable("journal event shape is invalid")
                event = JournalEvent(
                    sequence=expected_sequence,
                    event_id=str(payload["event_id"]),
                    event_type=event_type,
                    source_id=str(payload["source_id"]),
                    thread_id=(str(payload["thread_id"]) if payload.get("thread_id") is not None else None),
                    item_type=item_type,
                    item_ids=tuple(item_ids),
                    request_hash=request_hash,
                    created_at=datetime.fromisoformat(str(payload["created_at"])).astimezone(UTC),
                    previous_hash=previous_hash,
                    record_hash=record_hash,
                    actor_id=actor_id,
                )
                events.append(event)
                previous_hash = record_hash
                expected_sequence += 1
            result = tuple(events)
            observed_sequence = len(result)
            observed_hash = result[-1].record_hash if result else "0" * 64
            watermark_sequence, watermark_hash = self._read_watermark_unlocked(journal_id)
            if watermark_sequence > observed_sequence or (
                watermark_sequence == observed_sequence and watermark_hash != observed_hash
            ):
                raise DeletionJournalUnavailable("journal is behind its durable watermark")
            if watermark_sequence < observed_sequence:
                # The log fsync completed but a process may have stopped before
                # advancing the sidecar. Verified records are safe to advance.
                self._write_watermark_unlocked(journal_id, observed_sequence, observed_hash)
            self._cached_signature = signature
            self._cached_events = result
            return result
        except DeletionJournalUnavailable:
            raise
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, InvalidToken) as exc:
            raise DeletionJournalUnavailable("journal cannot be read or verified") from exc

    def read_all(self) -> tuple[JournalEvent, ...]:
        with self._locked():
            return self._read_unlocked()

    def append(
        self,
        *,
        event_id: str,
        event_type: str,
        source_id: str,
        thread_id: str | None = None,
        item_type: str = "thread",
        item_ids: tuple[str, ...] = (),
        request_hash: str = "",
        actor_id: str | None = None,
        created_at: datetime | None = None,
    ) -> JournalEvent:
        normalized_ids = tuple(sorted(set(item_ids)))
        if actor_id is not None and (
            not isinstance(actor_id, str) or not actor_id or len(actor_id) > 128
            or any(ord(character) < 32 for character in actor_id)
        ):
            raise ValueError("journal actor is invalid")
        if (
            self._journal_id is None
            and not self.path.exists()
            and not self.identity_path.exists()
            and not self.watermark_path.exists()
        ):
            # Standalone journal callers may initialize on first append. The
            # conversation store always initializes with its persisted DB ID.
            self.initialize(allow_create=True)
        with self._locked():
            events = self._read_unlocked()
            for existing in events:
                if existing.event_id == event_id:
                    same = (
                        existing.event_type == event_type
                        and existing.source_id == source_id
                        and existing.thread_id == thread_id
                        and existing.item_type == item_type
                        and existing.item_ids == normalized_ids
                        and existing.request_hash == request_hash
                        and (
                            existing.actor_id == actor_id
                            or (
                                existing.actor_id is None
                                and actor_id is not None
                                and event_type == "thread_delete"
                            )
                        )
                    )
                    if not same:
                        raise DeletionJournalUnavailable("journal event ID was reused")
                    return existing
            previous_hash = events[-1].record_hash if events else "0" * 64
            timestamp = created_at or datetime.now(UTC)
            timestamp = (
                timestamp.replace(tzinfo=UTC)
                if timestamp.tzinfo is None
                else timestamp.astimezone(UTC)
            )
            payload: dict[str, object] = {
                "sequence": len(events) + 1,
                "event_id": event_id,
                "event_type": event_type,
                "source_id": source_id,
                "thread_id": thread_id,
                "item_type": item_type,
                "item_ids": list(normalized_ids),
                "request_hash": request_hash,
                "created_at": timestamp.isoformat(),
                "previous_hash": previous_hash,
            }
            if actor_id is not None:
                payload["actor_id"] = actor_id
            record_hash = hashlib.sha256(_canonical(payload)).hexdigest()
            complete = {**payload, "record_hash": record_hash}
            token = self._cipher.encrypt(_canonical(complete)) + b"\n"
            descriptor = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "ab", closefd=False) as stream:
                    stream.write(token)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                os.close(descriptor)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            journal_id = self._read_identity_unlocked()
            self._write_watermark_unlocked(journal_id, len(events) + 1, record_hash)
            event = JournalEvent(
                sequence=len(events) + 1,
                event_id=event_id,
                event_type=event_type,
                source_id=source_id,
                thread_id=thread_id,
                item_type=item_type,
                item_ids=normalized_ids,
                request_hash=request_hash,
                created_at=timestamp,
                previous_hash=previous_hash,
                record_hash=record_hash,
                actor_id=actor_id,
            )
            stat = self.path.stat()
            self._cached_signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            self._cached_events = (*events, event)
            return event

    def replay_after(self, sequence: int) -> tuple[JournalEvent, ...]:
        events = self.read_all()
        if sequence < 0 or sequence > len(events):
            raise DeletionJournalUnavailable("snapshot journal watermark is invalid")
        return tuple(event for event in events if event.sequence > sequence)

    def high_water_mark(self) -> int:
        events = self.read_all()
        return events[-1].sequence if events else 0
