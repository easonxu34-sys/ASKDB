from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from domain.chart_edit import (
    ChartEditContext, ChartEditSort, ChartValueFormatPatch, FieldName,
    FieldLabel, ChartTitle, ChartType, ChartPaletteToken,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class DisplaySort(ChartEditSort):
    field: FieldName | None = None
    direction: Literal['asc', 'desc'] | None = None


class DisplayFormat(ChartValueFormatPatch):
    suffix: Annotated[str, Field(max_length=24, strict=True)] | None = None
    unit_family: Literal['CNY'] | None = None
    source_unit: Literal['yuan', 'thousand_yuan', 'ten_thousand_yuan', 'hundred_million_yuan'] | None = None
    display_unit: Literal['yuan', 'thousand_yuan', 'ten_thousand_yuan', 'hundred_million_yuan'] | None = None
    encoding: Literal['ratio_0_1', 'percent_0_100'] | None = None


class DisplayTopN(_Strict):
    field: FieldName
    count: Annotated[StrictInt, Field(ge=1, le=100)]
    direction: Literal['asc', 'desc']


class DisplaySolidColor(_Strict):
    mode: Literal['solid']
    hex: Annotated[str, Field(pattern=r'^#[0-9a-fA-F]{6}$')]
    opacity: Annotated[StrictInt, Field(ge=0, le=100)]


class DisplayGradientColor(_Strict):
    mode: Literal['linear_gradient']
    start_hex: Annotated[str, Field(pattern=r'^#[0-9a-fA-F]{6}$')]
    end_hex: Annotated[str, Field(pattern=r'^#[0-9a-fA-F]{6}$')]
    direction: Literal['horizontal', 'vertical', 'diagonal_down', 'diagonal_up']
    opacity: Annotated[StrictInt, Field(ge=0, le=100)]


DisplayColorSpec = DisplaySolidColor | DisplayGradientColor | ChartPaletteToken
ChartPaletteId = Literal[
    'system_default', 'classic', 'ocean', 'warm', 'earth', 'pastel', 'high_contrast',
    'color_vision_friendly'
]


class ChartDisplayView(_Strict):
    chart_type: ChartType | None = None
    dimension_field: FieldName | None = None
    metric_fields: Annotated[list[FieldName], Field(min_length=1, max_length=4)] | None = None
    hidden_metric_fields: Annotated[list[FieldName], Field(max_length=4)] | None = None
    bar_orientation: Literal['vertical', 'horizontal'] | None = None
    title: ChartTitle | None = None
    field_labels: Annotated[dict[FieldName, FieldLabel], Field(max_length=100)] | None = None
    sort: DisplaySort | None = None
    format_by_field: Annotated[dict[FieldName, DisplayFormat], Field(max_length=4)] | None = None
    show_data_labels: StrictBool | None = None
    show_legend: StrictBool | None = None
    color_palette_id: ChartPaletteId | None = None
    color_by_metric: Annotated[dict[FieldName, DisplayColorSpec], Field(max_length=4)] | None = None
    current_result_top_n: DisplayTopN | None = None

    @model_validator(mode='after')
    def reject_null_display_fields(self):
        if any(getattr(self, key) is None for key in self.model_fields_set if key != 'current_result_top_n'):
            raise ValueError('display fields cannot be null')
        for key in ('metric_fields', 'hidden_metric_fields'):
            items = getattr(self, key)
            if items is not None and len(items) != len(set(items)):
                raise ValueError('duplicate fields')
        if self.color_by_metric is not None:
            if self.chart_type not in ('line', 'bar') or self.metric_fields is None:
                raise ValueError('metric colors require a line or bar chart')
            visible_metrics = set(self.metric_fields) - set(self.hidden_metric_fields or [])
            if not set(self.color_by_metric).issubset(visible_metrics):
                raise ValueError('metric colors must target visible metrics')
        return self


class ChartEditRequest(_Strict):
    thread_id: Annotated[str, Field(min_length=1, max_length=128)]
    model_profile_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    instruction: Annotated[str, Field(min_length=1, max_length=2048)]
    source_result_id: Annotated[str, Field(min_length=1, max_length=256)]
    view: ChartDisplayView
    columns: Annotated[list[FieldName], Field(min_length=1, max_length=100)]
    column_types: Annotated[list[Annotated[str, Field(min_length=1, max_length=64)]], Field(min_length=1, max_length=100)]
    row_count: Annotated[StrictInt, Field(ge=0, le=1000)]
    truncated: StrictBool

    @model_validator(mode='after')
    def validate_context(self):
        if not self.instruction.strip() or len(self.columns) != len(self.column_types):
            raise ValueError('invalid context')
        columns = set(self.columns)
        view_fields = [
            self.view.dimension_field,
            *(self.view.metric_fields or []),
            *(self.view.color_by_metric or {}).keys(),
        ]
        if any(field is not None and field not in columns for field in view_fields):
            raise ValueError('view fields must exist in the query result')
        return self

    def to_context(self) -> ChartEditContext:
        return ChartEditContext(source_result_id=self.source_result_id,
            view=self.view.model_dump(mode='python', exclude_none=True),
            columns=tuple(self.columns), column_types=tuple(self.column_types),
            row_count=self.row_count, truncated=self.truncated)
