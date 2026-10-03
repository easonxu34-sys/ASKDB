from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from domain.query_policy import MAX_QUERY_ROWS, validate_read_query


def create_guarded_query_tool(toolkit: Any, dialect: str = "mysql"):
    """Create a Wren query tool with deterministic validation before execution."""

    @tool("wren_query")
    def wren_query(sql: str, limit: int = 100) -> dict[str, Any]:
        """Run a read-only SQL query through Wren after plan and database validation."""
        validate_read_query(sql, dialect)
        if limit < 1 or limit > MAX_QUERY_ROWS:
            raise ValueError(f"limit must be between 1 and {MAX_QUERY_ROWS}.")

        planned_sql = toolkit.dry_plan(sql)
        toolkit.dry_run(sql)
        table = toolkit.query(sql, limit=limit)
        return {
            "ok": True,
            "data": {
                "sql": sql,
                "planned_sql": planned_sql,
                "columns": table.column_names,
                "rows": table.to_pylist(),
                "row_count": table.num_rows,
                "truncated": table.num_rows >= limit,
            },
        }

    return wren_query
