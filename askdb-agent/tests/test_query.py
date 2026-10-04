from __future__ import annotations

import pytest

from application.chart_context import QueryArtifactContext
from query import create_guarded_query_tool, validate_read_query


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "DROP TABLE orders",
        "SELECT 1; DELETE FROM orders",
        "SELECT * FROM orders FOR UPDATE",
        "SELECT GET_LOCK('askdb-test', 1)",
        "SELECT SLEEP(10)",
        "SELECT LOAD_FILE('/etc/passwd')",
    ],
)
def test_read_query_rejects_mutations_and_multiple_statements(sql: str) -> None:
    with pytest.raises(ValueError):
        validate_read_query(sql)


def test_read_query_accepts_select_and_cte() -> None:
    validate_read_query("WITH totals AS (SELECT 1 AS n) SELECT n FROM totals")


def test_query_tool_runs_wren_validation_before_execution() -> None:
    class FakeTable:
        column_names = ["total"]
        schema = type("Schema", (), {"field": staticmethod(lambda name: type("Field", (), {"type": "int64"})())})()
        num_rows = 1

        @staticmethod
        def to_pylist():
            return [{"total": 42}]

    class FakeToolkit:
        calls: list[str]

        def __init__(self) -> None:
            self.calls = []

        def dry_plan(self, sql: str) -> str:
            self.calls.append("dry_plan")
            return sql

        def dry_run(self, sql: str) -> None:
            self.calls.append("dry_run")

        def query(self, sql: str, limit: int) -> FakeTable:
            self.calls.append("query")
            return FakeTable()

    toolkit = FakeToolkit()
    context = QueryArtifactContext()
    tool = create_guarded_query_tool(toolkit, context)

    result = tool.invoke({"sql": "SELECT 42 AS total", "limit": 10})

    assert toolkit.calls == ["dry_plan", "dry_run", "query"]
    assert result["data"]["rows"] == [{"total": 42}]
    assert result["data"]["column_types"] == ["int64"]
    assert context.get_query(result["data"]["result_id"]) is not None


def test_query_tool_rejects_invalid_sql_before_wren_calls() -> None:
    class FakeToolkit:
        calls: list[str]

        def __init__(self) -> None:
            self.calls = []

    toolkit = FakeToolkit()
    tool = create_guarded_query_tool(toolkit)

    with pytest.raises(ValueError):
        tool.invoke({"sql": "UPDATE orders SET total = 0"})

    assert toolkit.calls == []


def test_query_tool_does_not_execute_when_database_dry_run_fails() -> None:
    class FakeToolkit:
        calls: list[str]

        def __init__(self) -> None:
            self.calls = []

        def dry_plan(self, sql: str) -> str:
            self.calls.append("dry_plan")
            return sql

        def dry_run(self, sql: str) -> None:
            self.calls.append("dry_run")
            raise RuntimeError("database rejected the query")

        def query(self, sql: str, limit: int):
            self.calls.append("query")
            raise AssertionError("query must not run after a failed dry-run")

    toolkit = FakeToolkit()
    tool = create_guarded_query_tool(toolkit)

    with pytest.raises(RuntimeError):
        tool.invoke({"sql": "SELECT 1"})

    assert toolkit.calls == ["dry_plan", "dry_run"]


def test_query_tool_caps_result_limit_at_one_thousand_rows() -> None:
    class FakeToolkit:
        calls: list[str]

        def __init__(self) -> None:
            self.calls = []

    toolkit = FakeToolkit()
    tool = create_guarded_query_tool(toolkit)

    with pytest.raises(ValueError, match="between 1 and 1000"):
        tool.invoke({"sql": "SELECT 1", "limit": 1001})

    assert toolkit.calls == []
