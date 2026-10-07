"""Content-free diagnostics for comparing chat execution paths."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

# Inherit the server's console handler and INFO level without changing logging config.
logger = logging.getLogger('uvicorn.error.askdb.chat_diagnostics')


def fingerprint(value: Any) -> dict[str, Any]:
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return {'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(), 'chars': len(text)}


def reference(value: Any) -> str | None:
    return fingerprint(str(value))['sha256'][:16] if value is not None else None


def log_chat_diagnostic(event: str, thread_id: str, turn_id: str | None, **metadata: Any) -> None:
    """Callers supply counts, enums and fingerprints only; never raw content."""
    logger.info('ASKDB_CHAT_DIAG %s', json.dumps(
        {**metadata, 'schema': 1, 'event': event,
         'thread_ref': reference(thread_id), 'turn_ref': reference(turn_id)},
        ensure_ascii=True, sort_keys=True, separators=(',', ':')))
