from __future__ import annotations

import uuid
from typing import Any

from domain.chart_artifact import QueryResultArtifact
from domain.turn_interpretation import ResolvedTurnInterpretation, RuntimeRef


_TYPE_ALIASES = (
    ("line", ("折线图", "line chart")),
    ("bar", ("柱状图", "bar chart")),
    ("pie", ("饼图", "pie chart")),
)
_PIE_NEGATIONS = (
    "不要饼图",
    "不需要饼图",
    "别用饼图",
    "不要使用饼图",
    "不画饼图",
    "no pie chart",
    "not a pie chart",
    "don't use a pie chart",
    "do not use a pie chart",
    "don't make a pie chart",
    "do not show a pie chart",
)


def parse_requested_chart_type(text: str) -> str | None:
    normalized = text.casefold()
    for chart_type, aliases in _TYPE_ALIASES:
        if any(alias.casefold() in normalized for alias in aliases):
            if chart_type == "pie" and any(marker in normalized for marker in _PIE_NEGATIONS):
                continue
            return chart_type
    return None


class QueryArtifactContext:
    """In-memory query artifacts scoped to one Agent turn."""

    def __init__(self, *, interpretation: ResolvedTurnInterpretation | None = None,
                 runtime_ref: RuntimeRef | None = None, display_units: dict | None = None,
                 apply_presentation: bool = True) -> None:
        self._results: dict[str, QueryResultArtifact] = {}
        self._interpretation = interpretation
        self._runtime_ref = runtime_ref
        self._apply_presentation = apply_presentation
        self.display_units = dict(display_units or {})
        self.successful_analysis: list[dict] = []
        self.applied_constraints: list[dict] = []
        self.display_applied = False

    @property
    def interpretation(self) -> ResolvedTurnInterpretation | None:
        return self._interpretation

    @property
    def runtime_ref(self) -> RuntimeRef | None:
        return self._runtime_ref

    @property
    def display_unit(self) -> str | None:
        return self.interpretation.presentation.display_unit if self.interpretation and self._apply_presentation else None

    @property
    def current_question(self) -> str:
        return self.interpretation.question if self.interpretation else ''

    def store_query(self, table: Any, sql: str, limit: int) -> QueryResultArtifact:
        columns = tuple(str(column) for column in table.column_names)
        schema = table.schema
        column_types = tuple(str(schema.field(index).type) for index, _ in enumerate(columns))
        rows = tuple(dict(row) for row in table.to_pylist())
        result = QueryResultArtifact(
            result_id=uuid.uuid4().hex,
            sql=sql,
            columns=columns,
            column_types=column_types,
            rows=rows,
            row_count=int(table.num_rows),
            truncated=int(table.num_rows) >= limit,
        )
        self._results[result.result_id] = result
        return result

    def get_query(self, result_id: str) -> QueryResultArtifact | None:
        return self._results.get(result_id)

    def clear(self) -> None:
        self._results.clear()
