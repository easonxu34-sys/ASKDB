from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal


ChartType = Literal["line", "bar", "pie"]
_MAX_SAFE_JS_INTEGER = 2**53 - 1


def _wire_value(value: object) -> object:
    """Convert query cells to loss-aware values supported by JSON and browsers."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f") if value.is_finite() else None
    if isinstance(value, int):
        return value if abs(value) <= _MAX_SAFE_JS_INTEGER else str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, bytes):
        return "hex:" + value.hex()
    if isinstance(value, Mapping):
        return {str(key): _wire_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire_value(item) for item in value]
    raise TypeError(f"Unsupported query result value type: {type(value).__name__}")


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
            "rows": [_wire_value(row) for row in self.rows],
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
