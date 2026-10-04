from __future__ import annotations

from types import SimpleNamespace

from application.chart_context import QueryArtifactContext, parse_requested_chart_type
from tools.chart import create_chart_tool


def query_result(context: QueryArtifactContext, *, columns, types, rows):
    table = SimpleNamespace(
        column_names=columns,
        schema=SimpleNamespace(
            field=lambda index: SimpleNamespace(type=types[index])
        ),
        to_pylist=lambda: rows,
        num_rows=len(rows),
    )
    return context.store_query(table, "SELECT ...", 100)


def test_chart_tool_cannot_read_another_turns_query_artifact():
    first_turn = QueryArtifactContext()
    second_turn = QueryArtifactContext()
    result = query_result(
        first_turn,
        columns=["category", "count"],
        types=["string", "int64"],
        rows=[{"category": "A", "count": 2}],
    )

    outcome = create_chart_tool(second_turn).invoke({"result_id": result.result_id})

    assert outcome["ok"] is False
    assert outcome["data"]["kind"] == "chart_unavailable"


def test_missing_query_artifact_returns_typed_unavailable_result():
    outcome = create_chart_tool(QueryArtifactContext()).invoke({"result_id": "missing"})

    assert outcome["ok"] is False
    assert outcome["data"]["kind"] == "chart_unavailable"


def test_clearing_turn_context_expires_every_query_artifact():
    context = QueryArtifactContext()
    result = query_result(
        context,
        columns=["region", "revenue"],
        types=["string", "double"],
        rows=[{"region": "east", "revenue": 2.5}],
    )

    context.clear()

    assert context.get_query(result.result_id) is None


def test_temporal_and_numeric_fields_select_line_chart_deterministically():
    context = QueryArtifactContext()
    result = query_result(
        context,
        columns=["month", "revenue", "orders"],
        types=["date32[day]", "double", "int64"],
        rows=[{"month": "2026-02-01", "revenue": 3.5, "orders": 1}],
    )

    outcome = create_chart_tool(context).invoke({"result_id": result.result_id})

    assert outcome["data"]["chart_type"] == "line"
    assert outcome["data"]["x_field"] == "month"
    assert outcome["data"]["series_fields"] == ["revenue", "orders"]


def test_categorical_and_numeric_fields_select_bar_chart_deterministically():
    context = QueryArtifactContext()
    result = query_result(
        context,
        columns=["region", "revenue"],
        types=["string", "decimal128(12, 2)"],
        rows=[{"region": "east", "revenue": 3.5}],
    )

    outcome = create_chart_tool(context).invoke({"result_id": result.result_id})

    assert outcome["data"]["chart_type"] == "bar"
    assert outcome["data"]["x_field"] == "region"


def test_chart_aliases_and_negated_pie_request_are_parsed():
    assert parse_requested_chart_type("按月画折线图") == "line"
    assert parse_requested_chart_type("line chart by month") == "line"
    assert parse_requested_chart_type("按地区做柱状图") == "bar"
    assert parse_requested_chart_type("bar chart") == "bar"
    assert parse_requested_chart_type("饼图展示占比") == "pie"
    assert parse_requested_chart_type("pie chart") == "pie"
    assert parse_requested_chart_type("不要饼图") is None
    assert parse_requested_chart_type("don't use a pie chart") is None


def test_explicit_pie_requires_one_numeric_series_and_eight_or_fewer_categories():
    context = QueryArtifactContext()
    valid = query_result(
        context,
        columns=["region", "revenue"],
        types=["string", "double"],
        rows=[{"region": "east", "revenue": 3.5}],
    )
    invalid = query_result(
        context,
        columns=["region", "revenue", "orders"],
        types=["string", "double", "int64"],
        rows=[{"region": "east", "revenue": 3.5, "orders": 1}],
    )

    valid_outcome = create_chart_tool(context, "pie").invoke({"result_id": valid.result_id})
    invalid_outcome = create_chart_tool(context, "pie").invoke({"result_id": invalid.result_id})

    assert valid_outcome["data"]["chart_type"] == "pie"
    assert invalid_outcome["data"]["kind"] == "chart_unavailable"


def test_unsupported_result_shape_returns_chart_unavailable():
    context = QueryArtifactContext()
    result = query_result(
        context,
        columns=["label", "note"],
        types=["string", "string"],
        rows=[{"label": "A", "note": "not numeric"}],
    )

    outcome = create_chart_tool(context).invoke({"result_id": result.result_id})

    assert outcome["ok"] is False
    assert outcome["data"]["kind"] == "chart_unavailable"


def test_duplicate_column_names_keep_query_artifact_and_disable_chart():
    context = QueryArtifactContext()
    result = query_result(
        context,
        columns=["id", "id"],
        types=["int64", "string"],
        rows=[{"id": 1}],
    )

    outcome = create_chart_tool(context).invoke({"result_id": result.result_id})

    assert len(result.column_types) == 2
    assert result.column_types == ("int64", "string")
    assert outcome["data"]["kind"] == "chart_unavailable"
