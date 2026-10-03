from __future__ import annotations

import sqlglot
from sqlglot import exp

MAX_QUERY_ROWS = 1000
BLOCKED_FUNCTIONS = {
    "BENCHMARK",
    "DBMS_LOCK",
    "DBMS_SQL",
    "GET_LOCK",
    "LOAD_FILE",
    "OPENQUERY",
    "OPENDATASOURCE",
    "OPENROWSET",
    "PG_READ_BINARY_FILE",
    "PG_READ_FILE",
    "PG_LS_DIR",
    "RELEASE_ALL_LOCKS",
    "RELEASE_LOCK",
    "SLEEP",
    "XP_CMDSHELL",
}

SQLGLOT_DIALECTS = {
    "mssql": "tsql",
    "doris": "mysql",
    "athena": "trino",
}


def validate_read_query(sql: str, dialect: str = "mysql") -> None:
    dialect = SQLGLOT_DIALECTS.get(dialect.lower(), dialect.lower())
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.ParseError as exc:
        raise ValueError("SQL could not be parsed for the selected data source.") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise ValueError("Only one read-only SELECT query is allowed.")
    if isinstance(statements[0], exp.Select) and statements[0].args.get("locks"):
        raise ValueError("Locking SELECT queries are not allowed.")
    for function in statements[0].find_all(exp.Anonymous):
        if function.name.upper() in BLOCKED_FUNCTIONS:
            raise ValueError(f"The function {function.name} is not allowed.")
