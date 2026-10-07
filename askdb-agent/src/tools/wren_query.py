from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from application.chart_context import QueryArtifactContext
from domain.query_policy import MAX_QUERY_ROWS, validate_read_query
from integrations.wren import serialized_wren_operation
from application.personal_memory_query import enforce_bound_query, successful_descriptor


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
        turn = context.interpretation if context else None
        constraints = turn.semantics.constraints() if turn else {}
        if turn:
            if turn.analysis.steps and turn.semantics.runtime != context.runtime_ref:
                from domain.personal_memory import PersonalMemoryError
                raise PersonalMemoryError('PERSONAL_MEMORY_VERSION_CHANGED', '分析方法与本轮数据源版本不一致，请重试。')
            enforce_bound_query(sql, dialect, turn.semantics, context.runtime_ref)
        if limit < 1 or limit > MAX_QUERY_ROWS:
            raise ValueError(f"limit must be between 1 and {MAX_QUERY_ROWS}.")

        with serialized_wren_operation(toolkit):
            planned_sql = toolkit.dry_plan(sql)
            toolkit.dry_run(sql)
            table = toolkit.query(sql, limit=limit)
        if context is not None:
            context.successful_analysis.append(successful_descriptor(sql,dialect,context.current_question))
            if constraints.get('filters') or constraints.get('metrics'):
                context.applied_constraints.append(constraints)
        artifact = (context or QueryArtifactContext()).store_query(table, sql, limit)
        from application.personal_memory_display import projection_formats
        formats=projection_formats(sql,dialect,getattr(context,'display_units',{}),getattr(context,'display_unit',None))
        if context is not None and formats:
            context.display_applied=True
        return {
            "ok": True,
            "data": {**artifact.to_dict(), "planned_sql": planned_sql, **({"display_formats": formats} if formats else {})},
        }

    return wren_query
