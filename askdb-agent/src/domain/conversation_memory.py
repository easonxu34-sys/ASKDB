from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class ThreadNotFound(LookupError):
    """The thread does not exist, is unavailable, or belongs to another owner."""


class ThreadCreateIdempotencyConflict(ValueError):
    """A thread creation key was reused with different source or history."""


class ThreadHistoryImportConflict(ValueError):
    """A legacy-history import chunk conflicts with its frozen descriptor."""


class ThreadHistoryImportIncomplete(RuntimeError):
    """A thread is not readable until its staged history import is complete."""


class ThreadGrantRevoked(PermissionError):
    """The owner no longer has access to the thread's data source."""


class TurnIdempotencyConflict(ValueError):
    """A turn ID was reused for a different user message."""


class TurnSequenceConflict(ValueError):
    """The client attempted to append against a stale conversation sequence."""


class TurnAlreadyRunning(RuntimeError):
    """Another distinct turn is already being processed for this thread."""


class TurnNotFound(LookupError):
    """The requested in-progress turn does not exist."""


class ThreadDeletionConflict(ValueError):
    """The thread deletion impact changed or its confirmation expired."""


class ThreadDeletionJournalRequired(RuntimeError):
    """Destructive memory changes are disabled without the durable journal."""


class ThreadDeletionParticipantUnavailable(RuntimeError):
    """Linked memory exists but its deletion participant is not registered."""


@dataclass(frozen=True)
class ConversationThread:
    thread_id: str
    source_id: str
    created_at: datetime
    last_user_turn_at: datetime
    expires_at: datetime
    history_import_pending: bool = False


@dataclass(frozen=True)
class ConversationTurn:
    turn_id: str
    sequence: int
    role: str
    content: str
    created_at: datetime


@dataclass(frozen=True)
class ThreadContext:
    thread_id: str
    source_id: str
    current_sequence: int
    summary: str | None
    summary_version: int
    turns: tuple[ConversationTurn, ...]
    expires_at: datetime


@dataclass(frozen=True)
class TurnStart:
    is_new: bool
    status: str
    assistant_content: str | None = None
    user_sequence: int | None = None
    assistant_sequence: int | None = None


@dataclass(frozen=True)
class LegacyHistoryImportDescriptor:
    import_id: str
    chunk_hashes: tuple[str, ...]
    turn_count: int
    content_bytes: int


@dataclass(frozen=True)
class HistoryImportChunkResult:
    received_chunks: int
    expected_chunks: int
    completed: bool
    replayed: bool


@dataclass(frozen=True)
class ThreadDeletionImpact:
    impact_version: str
    thread_id: str
    source_id: str
    rule_count: int
    rule_labels: tuple[str, ...]
    query_example_candidate_count: int
    expires_at: datetime


@dataclass(frozen=True)
class ThreadDeletionOperation:
    operation_id: str
    thread_id: str
    status: str
    journal_sequence: int
    historical_content_expires_at: datetime


@dataclass(frozen=True)
class MemoryRevocationOperation:
    operation_id: str
    data_source_id: str
    item_id: str
    status: str
    journal_sequence: int
