from __future__ import annotations

import asyncio
import fcntl
import logging
import os
from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable


logger = logging.getLogger(__name__)


def initialize_memory_before_serving(store: Any) -> None:
    """Replay deletions and run overdue cleanup before the API accepts traffic."""
    store.assert_journal_ready()
    store.assert_deletion_participant_ready()
    store.fail_stale_turns(stale_after=timedelta(minutes=15))
    while store.expire_inactive_threads(limit=500):
        pass
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


def try_acquire_sweeper_lease(database_path: Path) -> int | None:
    """Allow only one process on this host to run the periodic cleanup loop."""
    path = database_path.with_name(database_path.name + ".memory-sweeper.lock")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    return descriptor


def release_sweeper_lease(descriptor: int) -> None:
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


async def _sweep_once(
    store: Any,
    publication_sweeper: Callable[[], Awaitable[Any]] | None = None,
) -> None:
    await asyncio.to_thread(store.assert_journal_ready)
    await asyncio.to_thread(
        store.fail_stale_turns, stale_after=timedelta(minutes=15), limit=500
    )
    await asyncio.to_thread(store.expire_inactive_threads, limit=100)
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
    """Run retention and deletion recovery serially in the app process."""
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
