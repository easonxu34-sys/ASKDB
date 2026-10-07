from __future__ import annotations

from typing import Protocol

from integrations.database import PostgresConnection
from integrations.deletion_journal import JournalEvent


class BusinessRuleDeletionParticipant(Protocol):
    """Source-thread rule lineage joined to the thread deletion transaction."""

    def preview(
        self, connection: PostgresConnection, thread_id: str, source_id: str
    ) -> tuple[tuple[str, ...], tuple[str, ...]]: ...

    def apply(self, connection: PostgresConnection, event: JournalEvent) -> None: ...
