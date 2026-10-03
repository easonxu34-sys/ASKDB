from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any


_FENCED_BLOCK = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")
_RESULT_BLOCK = re.compile(
    r"<(result|tool_result|tool|observation|think|analysis)[^>]*>[\s\S]*?</\1\s*>",
    re.IGNORECASE,
)
_RESULT_LABEL = re.compile(
    r"(?im)^\s*(?:\*\*)?(?:SQL\s*(?:语句|statement)|查询结果(?:\s*[·|｜].*)?|"
    r"query\s+results?)(?:\*\*)?\s*$"
)
_SECRET = re.compile(
    r"\b(?:api[_ -]?key|password|passwd|token|secret|access[_ -]?key)\b\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(
    r"(?<![\w])(?:\+?86[- ]?)?1[3-9]\d{9}(?![\w])"
    r"|(?<![\w])(?:\+?\d{1,3}[- ])?(?:\(?\d{2,4}\)?[- ])?\d{3,4}[- ]\d{4}(?![\w])"
)
_LABELED_RECORD_ID = re.compile(
    r"(?i)(?:(?:customer|client|user|member|account|order)[_ -]?(?:id|no|number)"
    r"|(?:客户|用户|会员|账号|订单)(?:id|ID|编号|号码|号))\s*[:=：#]?\s*"
    r"[\"']?[A-Z0-9][A-Z0-9_-]{2,}[\"']?"
)
_SQL_LINE = re.compile(
    r"^\s*(?:SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|EXPLAIN)\b",
    re.IGNORECASE,
)
_SQL_CONTINUATION = re.compile(
    r"^\s*(?:FROM\b|JOIN\b|LEFT\b|RIGHT\b|INNER\b|FULL\b|CROSS\b|ON\b|WHERE\b|GROUP\s+BY\b|ORDER\s+BY\b|HAVING\b|LIMIT\b|OFFSET\b|UNION\b|INTERSECT\b|EXCEPT\b|AND\b|OR\b|AS\b|ASC\b|DESC\b|,|\)|;|\.)",
    re.IGNORECASE,
)


def sanitize_turn_text(value: str, *, max_chars: int = 12_000) -> str:
    """Keep bounded conversational text while dropping common payload artifacts."""
    text = value[: max_chars * 4]
    text = _FENCED_BLOCK.sub(" ", text)
    text = _RESULT_BLOCK.sub(" ", text)
    text = _RESULT_LABEL.sub(" ", text)
    kept_lines: list[str] = []
    inside_sql = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            continue
        if _SQL_LINE.match(line):
            inside_sql = ";" not in line
            continue
        if inside_sql:
            if not stripped or _SQL_CONTINUATION.match(line):
                if ";" in line:
                    inside_sql = False
                continue
            inside_sql = False
        kept_lines.append(line)
    text = "\n".join(kept_lines)
    text = _SECRET.sub("[已脱敏]", text)
    text = _EMAIL.sub("[已脱敏标识]", text)
    text = _PHONE.sub("[已脱敏标识]", text)
    # Keep ordinary dates and filters for same-thread follow-ups; mask explicit
    # record identifiers because they are rarely needed as conversational context.
    text = _LABELED_RECORD_ID.sub("[已脱敏记录标识]", text)
    text = "\n".join(part.rstrip() for part in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars]


class ConversationMemoryApplication:
    """Application-level entry point for thread persistence and retention."""

    def __init__(self, store: Any, *, clock: Callable[[], datetime] | None = None):
        self.store = store
        self.clock = clock or (lambda: datetime.now(UTC))

    def create_thread(self, *, owner_user_id: str, source_id: str) -> Any:
        return self.store.create_thread(owner_user_id=owner_user_id, source_id=source_id)

    def expire_inactive_threads(self, *, limit: int = 100) -> int:
        # Expiry is run by the single-process startup/periodic coordinator. The
        # deletion journal integration supplies durable cascade behavior before
        # this method is enabled in the runtime path.
        expire = getattr(self.store, "expire_inactive_threads", None)
        if expire is None:
            return 0
        now = self.clock()
        return expire(now=now, limit=limit)
