from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from application.chart_context import QueryArtifactContext
from domain.query_policy import MAX_QUERY_ROWS, validate_read_query


def create_guarded_query_tool(
    toolkit: Any,
    context: QueryArtifactContext | None = None,
    dialect: str = "mysql",
):
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
        artifact = (context or QueryArtifactContext()).store_query(table, sql, limit)
        return {
            "ok": True,
            "data": {**artifact.to_dict(), "planned_sql": planned_sql},
        }

    return wren_query
