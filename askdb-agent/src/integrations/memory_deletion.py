from __future__ import annotations

import sqlite3
from typing import Protocol

from integrations.deletion_journal import JournalEvent


class BusinessRuleDeletionParticipant(Protocol):
    """Source-thread rule lineage joined to the thread deletion transaction."""

    def preview(
        self, connection: sqlite3.Connection, thread_id: str, source_id: str
    ) -> tuple[tuple[str, ...], tuple[str, ...]]: ...

    def apply(self, connection: sqlite3.Connection, event: JournalEvent) -> None: ...
