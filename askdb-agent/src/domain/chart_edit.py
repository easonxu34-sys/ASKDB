from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


ChartType = Literal["line", "bar", "pie"]
ChartPaletteToken = Literal["blue", "teal", "green", "amber", "orange", "red", "purple", "slate"]
ChartEditQueryOperation = Literal[
    "database_filter", "full_data_top_n", "aggregation", "period_comparison"
]
ChartEditClarificationCode = Literal[
    "top_n_scope_required",
    "top_n_metric_required",
    "source_unit_required",
    "field_not_in_result",
    "category_not_in_result",
    "category_ambiguous",
    "conflicting_category_color",
    "chart_type_incompatible",
    "conflicting_sort",
    "operation_unsupported",
]
FieldName = Annotated[str, Field(min_length=1, max_length=128, strict=True)]
ChartTitle = Annotated[str, Field(min_length=1, max_length=120, strict=True)]
FieldLabel = Annotated[str, Field(max_length=80, strict=True)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ChartEditSort(_StrictModel):
    mode: Literal["original", "dimension", "metric"]
    field: FieldName | None = Field(...)
    direction: Literal["asc", "desc"] | None = Field(...)

    @model_validator(mode="after")
    def validate_mode_fields(self) -> ChartEditSort:
        if self.mode == "original":
            if self.field is not None or self.direction is not None:
                raise ValueError("original sort cannot name a field or direction")
        elif self.field is None or self.direction is None:
            raise ValueError("dimension and metric sort require a field and direction")
        return self


class ChartValueFormatPatch(_StrictModel):
    mode: Literal["raw", "suffix", "unit_scale", "percent"]
    decimal_places: Literal["auto"] | Annotated[StrictInt, Field(ge=0, le=6)]
    suffix: Annotated[str, Field(max_length=24, strict=True)] | None = Field(...)
    unit_family: Literal["CNY"] | None = Field(...)
    source_unit: Literal[
        "yuan", "thousand_yuan", "ten_thousand_yuan", "hundred_million_yuan"
    ] | None = Field(...)
    display_unit: Literal[
        "yuan", "thousand_yuan", "ten_thousand_yuan", "hundred_million_yuan"
    ] | None = Field(...)
    encoding: Literal["ratio_0_1", "percent_0_100"] | None = Field(...)

    @model_validator(mode="before")
    @classmethod
    def default_omitted_unused_fields(cls, value: object) -> object:
        # Some structured-output providers omit nullable fields whose value is
        # null. Normalize those omissions before the strict mode-specific check.
        if not isinstance(value, Mapping):
            return value
        normalized = dict(value)
        for name in ("suffix", "unit_family", "source_unit", "display_unit", "encoding"):
            normalized.setdefault(name, None)
        return normalized

    @model_validator(mode="after")
    def validate_mode_fields(self) -> ChartValueFormatPatch:
        present = {
            "suffix": self.suffix is not None,
            "unit_family": self.unit_family is not None,
            "source_unit": self.source_unit is not None,
            "display_unit": self.display_unit is not None,
            "encoding": self.encoding is not None,
        }
        expected = {
            "raw": set(),
            "suffix": {"suffix"},
            "unit_scale": {"unit_family", "source_unit", "display_unit"},
            "percent": {"encoding"},
        }[self.mode]
        actual = {field for field, value in present.items() if value}
        if actual != expected:
            raise ValueError("format fields do not match format mode")
        if self.suffix is not None and not self.suffix.strip():
            raise ValueError("suffix must not be blank")
        return self


class ChartEditPatch(_StrictModel):
    chart_type: ChartType | None = Field(...)
    dimension_field: FieldName | None = Field(...)
    metric_fields: Annotated[list[FieldName], Field(min_length=1, max_length=4)] | None = Field(...)
    hidden_metric_fields: Annotated[list[FieldName], Field(max_length=4)] | None = Field(...)
    bar_orientation: Literal["vertical", "horizontal"] | None = Field(...)
    title: ChartTitle | None = Field(...)
    field_labels: Annotated[dict[FieldName, FieldLabel], Field(max_length=100)] | None = Field(...)
    sort: ChartEditSort | None = Field(...)
    format_by_field: Annotated[
        dict[FieldName, ChartValueFormatPatch], Field(max_length=4)
    ] | None = Field(...)
    show_data_labels: StrictBool | None = Field(...)
    show_legend: StrictBool | None = Field(...)
    color_by_metric: Annotated[
        dict[FieldName, ChartPaletteToken], Field(max_length=4)
    ] | None = Field(...)

    @model_validator(mode="after")
    def validate_patch(self) -> ChartEditPatch:
        if not any(getattr(self, name) is not None for name in type(self).model_fields):
            raise ValueError("patch must contain at least one view change")
        for field_name in ("metric_fields", "hidden_metric_fields"):
            value = getattr(self, field_name)
            if value is not None and len(value) != len(set(value)):
                raise ValueError(f"{field_name} must not contain duplicates")
        return self


class ChartEditTopNOperation(_StrictModel):
    kind: Literal["top_n"]
    field: FieldName
    count: Annotated[StrictInt, Field(ge=1, le=100)]
    direction: Literal["asc", "desc"]
    scope: Literal["current_result"]


class ChartEditCategoryColorOperation(_StrictModel):
    category_label: Annotated[str, Field(min_length=1, max_length=128, strict=True)]
    color: ChartPaletteToken


class ChartEditQueryProposal(_StrictModel):
    operation: ChartEditQueryOperation


class ChartEditClarification(_StrictModel):
    code: ChartEditClarificationCode


class ChartEditIntent(_StrictModel):
    status: Literal["apply", "query_required", "clarify"]
    patch: ChartEditPatch | None = Field(...)
    current_result_operation: ChartEditTopNOperation | None = Field(...)
    category_color_operations: Annotated[
        list[ChartEditCategoryColorOperation], Field(max_length=8)
    ]
    query_proposal: ChartEditQueryProposal | None = Field(...)
    clarification: ChartEditClarification | None = Field(...)

    @model_validator(mode="after")
    def validate_status_payload(self) -> ChartEditIntent:
        if self.status == "apply":
            if self.patch is None and self.current_result_operation is None and not self.category_color_operations:
                raise ValueError("apply requires at least one supported operation")
            if self.query_proposal is not None or self.clarification is not None:
                raise ValueError("apply cannot include query or clarification fields")
        elif self.status == "query_required":
            if self.query_proposal is None or self.clarification is not None:
                raise ValueError("query_required requires a query proposal only")
        else:
            if self.clarification is None:
                raise ValueError("clarify requires a clarification code")
            if (
                self.patch is not None
                or self.current_result_operation is not None
                or self.category_color_operations
                or self.query_proposal is not None
            ):
                raise ValueError("clarify cannot include operations")
        return self


@dataclass(frozen=True)
class ChartEditContext:
    """Bounded client context for interpretation; never contains SQL or result rows."""

    source_result_id: str
    view: Mapping[str, object]
    columns: tuple[str, ...]
    column_types: tuple[str, ...]
    row_count: int
    truncated: bool
