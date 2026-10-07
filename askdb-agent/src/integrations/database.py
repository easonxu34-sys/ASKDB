from __future__ import annotations

import os
import re
import threading
from collections.abc import Sequence
from typing import Any

import psycopg
from psycopg import pq


_POSTGRES_WRITE_LOCK_KEY = (1_095_985_988, 1_111_577_413)


class PostgresRow(dict[str, Any]):
    """A row that supports named and positional access for existing store code."""

    def __init__(self, columns: Sequence[str], values: Sequence[Any]) -> None:
        super().__init__(zip(columns, values, strict=True))
        self._values = tuple(values)

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def _row_factory(cursor: Any):
    columns = tuple(column.name for column in (cursor.description or ()))

    def make_row(values: Sequence[Any]) -> PostgresRow:
        return PostgresRow(columns, values)

    return make_row


class PostgresConnection:
    """Small synchronous connection boundary used by the Agent stores."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    def execute(
        self,
        statement: str,
        parameters: Sequence[Any] | None = None,
    ) -> psycopg.Cursor[Any]:
        if not parameters:
            return self._connection.execute(statement)
        normalized = tuple(int(value) if isinstance(value, bool) else value for value in parameters)
        return self._connection.execute(statement, normalized)

    def executemany(self, statement: str, parameters: Any):
        normalized = (
            tuple(int(value) if isinstance(value, bool) else value for value in row)
            for row in parameters
        )
        return self._connection.cursor().executemany(statement, normalized)

    def acquire_write_lock(self) -> None:
        """Serialize application write transactions across Agent processes."""
        if self._connection.info.transaction_status == pq.TransactionStatus.IDLE:
            self._connection.execute("BEGIN")
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)", _POSTGRES_WRITE_LOCK_KEY
        )

    def execute_script(self, script: str) -> None:
        for statement in _split_sql_script(script):
            self.execute(statement)

    def commit(self) -> None:
        if self._connection.info.transaction_status != pq.TransactionStatus.IDLE:
            self._connection.commit()

    def rollback(self) -> None:
        if self._connection.info.transaction_status != pq.TransactionStatus.IDLE:
            self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> PostgresConnection:
        if self._connection.info.transaction_status == pq.TransactionStatus.IDLE:
            self._connection.execute("BEGIN")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()


class PostgresDatabase:
    def __init__(self, dsn: str | None = None) -> None:
        configured_dsn = (dsn or os.environ.get("ASKDB_DATABASE_DSN", "")).strip()
        self.dsn = configured_dsn
        self._migration_lock = threading.Lock()
        self._migrated = False

    def connect(self) -> PostgresConnection:
        if not self.dsn:
            raise RuntimeError("ASKDB_DATABASE_DSN must configure PostgreSQL")
        raw_connection = psycopg.connect(
            self.dsn,
            row_factory=_row_factory,
            autocommit=True,
        )
        connection = PostgresConnection(raw_connection)
        if self._migrated:
            return connection
        try:
            with self._migration_lock:
                if not self._migrated:
                    from integrations.postgres_migrations import apply_postgres_migrations

                    apply_postgres_migrations(connection)
                    connection.commit()
                    self._migrated = True
        except BaseException:
            connection.rollback()
            connection.close()
            raise
        return connection


def _split_sql_script(script: str) -> tuple[str, ...]:
    """Split SQL statements while respecting quoted text, comments, and dollar blocks."""
    statements: list[str] = []
    start = 0
    index = 0
    state = "normal"
    block_depth = 0
    dollar_tag: str | None = None
    while index < len(script):
        char = script[index]
        following = script[index + 1] if index + 1 < len(script) else ""
        if state == "single":
            if char == "'":
                if following == "'":
                    index += 2
                    continue
                state = "normal"
        elif state == "double":
            if char == '"':
                if following == '"':
                    index += 2
                    continue
                state = "normal"
        elif state == "line_comment":
            if char in "\r\n":
                state = "normal"
        elif state == "block_comment":
            if char == "/" and following == "*":
                block_depth += 1
                index += 2
                continue
            if char == "*" and following == "/":
                block_depth -= 1
                index += 2
                if block_depth == 0:
                    state = "normal"
                continue
        elif state == "dollar":
            assert dollar_tag is not None
            if script.startswith(dollar_tag, index):
                index += len(dollar_tag)
                state = "normal"
                dollar_tag = None
                continue
        elif char == "'":
            state = "single"
        elif char == '"':
            state = "double"
        elif char == "-" and following == "-":
            state = "line_comment"
            index += 2
            continue
        elif char == "/" and following == "*":
            state = "block_comment"
            block_depth = 1
            index += 2
            continue
        elif char == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z_0-9]*\$|\$\$", script[index:])
            if match:
                dollar_tag = match.group(0)
                state = "dollar"
                index += len(dollar_tag)
                continue
        elif char == ";":
            statement = script[start:index].strip()
            if statement:
                statements.append(statement)
            start = index + 1
        index += 1
    if state in {"single", "double", "block_comment", "dollar"}:
        raise ValueError("unterminated SQL literal or comment")
    tail = script[start:].strip()
    if tail:
        statements.append(tail)
    return tuple(statements)
