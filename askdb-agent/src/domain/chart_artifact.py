from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ChartType = Literal["line", "bar", "pie"]


@dataclass(frozen=True)
class QueryResultArtifact:
    result_id: str
    sql: str
    columns: tuple[str, ...]
    column_types: tuple[str, ...]
    rows: tuple[dict[str, object], ...]
    row_count: int
    truncated: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "result_id": self.result_id,
            "sql": self.sql,
            "columns": list(self.columns),
            "column_types": list(self.column_types),
            "rows": [dict(row) for row in self.rows],
            "row_count": self.row_count,
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class ChartArtifact:
    source_result_id: str
    chart_type: ChartType
    x_field: str
    series_fields: tuple[str, ...]
    title: str
    schema_version: int = 1
    kind: Literal["echarts_chart"] = "echarts_chart"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "schema_version": self.schema_version,
            "source_result_id": self.source_result_id,
            "chart_type": self.chart_type,
            "x_field": self.x_field,
            "series_fields": list(self.series_fields),
            "title": self.title,
        }


@dataclass(frozen=True)
class ChartRequest:
    latest_user_text: str
    requested_chart_type: ChartType | None
    should_render: bool = False


def chart_unavailable(reason: str) -> dict[str, object]:
    return {"kind": "chart_unavailable", "reason": reason}
