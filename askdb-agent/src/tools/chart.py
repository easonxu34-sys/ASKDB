from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from application.chart_context import QueryArtifactContext
from domain.chart_artifact import ChartArtifact, ChartType, chart_unavailable

_MAX_SERIES = 4
_MAX_PIE_CATEGORIES = 8


def _type_group(type_name: str) -> str:
    normalized = type_name.casefold()
    if normalized.startswith(("date", "time", "timestamp", "duration")):
        return "temporal"
    if normalized.startswith(("int", "uint", "float", "double", "decimal", "numeric")):
        return "numeric"
    if any(marker in normalized for marker in ("struct", "list<", "map<", "binary", "null")):
        return "unsupported"
    return "categorical"


def _choose_chart(context: QueryArtifactContext, result_id: str, requested_type: str | None):
    result = context.get_query(result_id)
    if result is None:
        return chart_unavailable("查询结果已失效，请重新查询后再生成图表。")
    if len(result.columns) != len(set(result.columns)):
        return chart_unavailable("查询结果包含重复字段名，无法安全生成图表，已保留查询表格。")

    groups = {name: _type_group(type_name) for name, type_name in zip(result.columns, result.column_types)}
    temporal = [name for name in result.columns if groups[name] == "temporal"]
    categorical = [name for name in result.columns if groups[name] == "categorical"]
    numeric = [name for name in result.columns if groups[name] == "numeric"]
    if not numeric:
        return chart_unavailable("查询结果没有可绘制的数值指标，已保留查询表格。")

    if requested_type == "pie":
        if len(categorical) == 0 or len(numeric) != 1:
            return chart_unavailable("饼图需要一个分类字段和一个数值指标，已保留查询表格。")
        categories = {row.get(categorical[0]) for row in result.rows}
        if len(categories) > _MAX_PIE_CATEGORIES:
            return chart_unavailable("饼图最多显示 8 个分类，已保留查询表格。")
        chart_type: ChartType = "pie"
        x_field = categorical[0]
        series = numeric
    elif requested_type == "line":
        x_field = (temporal or categorical or [None])[0]
        if x_field is None:
            return chart_unavailable("折线图需要时间或分类维度，已保留查询表格。")
        chart_type = "line"
        series = numeric
    elif requested_type == "bar":
        x_field = (categorical or temporal or [None])[0]
        if x_field is None:
            return chart_unavailable("柱状图需要分类维度，已保留查询表格。")
        chart_type = "bar"
        series = numeric
    elif temporal:
        x_field = temporal[0]
        chart_type = "line"
        series = numeric
    elif categorical:
        x_field = categorical[0]
        chart_type = "bar"
        series = numeric
    else:
        return chart_unavailable("查询结果没有可绘制的维度字段，已保留查询表格。")

    selected_series = tuple(series[:_MAX_SERIES])
    if not selected_series:
        return chart_unavailable("查询结果没有可绘制的数值指标，已保留查询表格。")
    title = f"{', '.join(selected_series)} by {x_field}"
    return ChartArtifact(
        source_result_id=result.result_id,
        chart_type=chart_type,
        x_field=x_field,
        series_fields=selected_series,
        title=title,
    ).to_dict()


def create_chart_tool(context: QueryArtifactContext, requested_type: str | None = None):
    @tool("render_chart")
    def render_chart(result_id: str) -> dict[str, Any]:
        """Create a deterministic chart from a successful query result in this turn."""
        try:
            selected = _choose_chart(context, result_id, requested_type)
        except Exception:
            selected = chart_unavailable("图表暂不可用，已保留查询表格。")
        if selected.get("kind") == "chart_unavailable":
            return {"ok": False, "data": selected}
        return {"ok": True, "data": selected}

    return render_chart
