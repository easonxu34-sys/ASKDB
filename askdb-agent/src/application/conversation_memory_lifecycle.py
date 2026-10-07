from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import timedelta
from typing import Any, Awaitable, Callable

from integrations.database import PostgresConnection, PostgresDatabase


logger = logging.getLogger(__name__)


def initialize_memory_before_serving(store: Any) -> None:
    """Replay deletions and recover stale work before the API accepts traffic."""
    store.assert_journal_ready()
    store.assert_deletion_participant_ready()
    store.fail_stale_turns(stale_after=timedelta(minutes=15))
    store.purge_expired_tombstones()
    participant = getattr(store, "deletion_participant", None)
    expire_candidates = getattr(participant, "expire_candidates", None)
    if callable(expire_candidates):
        while expire_candidates(limit=500):
            pass
    purge_origins = getattr(participant, "purge_expired_origins", None)
    if callable(purge_origins):
        while purge_origins(limit=500):
            pass


def try_acquire_sweeper_lease(database: PostgresDatabase) -> PostgresConnection | None:
    """Hold a session advisory lock so only one process runs periodic memory cleanup."""
    connection = database.connect()
    try:
        acquired = connection.execute(
            "SELECT pg_try_advisory_lock(1095985988, 1111577414) AS acquired"
        ).fetchone()["acquired"]
        if acquired:
            return connection
    except BaseException:
        connection.close()
        raise
    connection.close()
    return None


def release_sweeper_lease(connection: PostgresConnection) -> None:
    try:
        connection.execute("SELECT pg_advisory_unlock(1095985988, 1111577414)")
    finally:
        connection.close()


async def _sweep_once(
    store: Any,
    publication_sweeper: Callable[[], Awaitable[Any]] | None = None,
) -> None:
    await asyncio.to_thread(store.assert_journal_ready)
    await asyncio.to_thread(
        store.fail_stale_turns, stale_after=timedelta(minutes=15), limit=500
    )
    await asyncio.to_thread(store.purge_expired_tombstones)
    participant = getattr(store, "deletion_participant", None)
    expire_candidates = getattr(participant, "expire_candidates", None)
    if callable(expire_candidates):
        await asyncio.to_thread(expire_candidates, limit=100)
    purge_origins = getattr(participant, "purge_expired_origins", None)
    if callable(purge_origins):
        await asyncio.to_thread(purge_origins, limit=500)
    if publication_sweeper is not None:
        await publication_sweeper()


async def run_memory_sweeper(
    store: Any,
    *,
    interval_seconds: int = 300,
    publication_sweeper: Callable[[], Awaitable[Any]] | None = None,
) -> None:
    """Run memory cleanup and deletion recovery serially in the app process."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await _sweep_once(store, publication_sweeper)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Never log thread text, SQL, linked rule labels, or exception details.
            logger.error("Agent memory sweep failed (%s)", type(exc).__name__)


async def stop_memory_sweeper(task: asyncio.Task[None]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
