"""Small model-facing operations, independent of the persisted chart view."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from domain.chart_edit import (
    _StrictModel, ChartEditClarificationCode, ChartEditQueryOperation,
    ChartPaletteToken, ChartTitle, ChartType, FieldLabel, FieldName,
)


class SetChartType(_StrictModel):
    kind: Literal['set_chart_type']
    value: ChartType


class SetOrientation(_StrictModel):
    kind: Literal['set_orientation']
    value: Literal['vertical', 'horizontal']


class SetDimension(_StrictModel):
    kind: Literal['set_dimension']
    field: FieldName


class SetMetrics(_StrictModel):
    kind: Literal['set_metrics']
    fields: Annotated[list[FieldName], Field(min_length=1, max_length=4)]

    @model_validator(mode='after')
    def unique_fields(self):
        if len(self.fields) != len(set(self.fields)):
            raise ValueError('duplicate fields')
        return self


class SetHiddenMetrics(_StrictModel):
    kind: Literal['set_hidden_metrics']
    fields: Annotated[list[FieldName], Field(max_length=4)]

    @model_validator(mode='after')
    def unique_fields(self):
        if len(self.fields) != len(set(self.fields)):
            raise ValueError('duplicate fields')
        return self


class SetTitle(_StrictModel):
    kind: Literal['set_title']
    value: ChartTitle


class SetFieldLabel(_StrictModel):
    kind: Literal['set_field_label']
    field: FieldName
    value: FieldLabel


class SetSort(_StrictModel):
    kind: Literal['set_sort']
    mode: Literal['dimension', 'metric']
    field: FieldName
    direction: Literal['asc', 'desc']


class RestoreResultOrder(_StrictModel):
    kind: Literal['restore_result_order']


class _FieldFormat(_StrictModel):
    field: FieldName


class SetPrecision(_FieldFormat):
    kind: Literal['set_precision']
    value: Literal['auto'] | Annotated[StrictInt, Field(ge=0, le=6)]


class SetRawFormat(_FieldFormat):
    kind: Literal['set_raw_format']


class SetSuffixFormat(_FieldFormat):
    kind: Literal['set_suffix_format']
    suffix: Annotated[str, Field(min_length=1, max_length=24, strict=True)]


Unit = Literal['yuan', 'thousand_yuan', 'ten_thousand_yuan', 'hundred_million_yuan']


class SetUnitFormat(_FieldFormat):
    kind: Literal['set_unit_format']
    source_unit: Unit
    display_unit: Unit


class SetPercentFormat(_FieldFormat):
    kind: Literal['set_percent_format']
    encoding: Literal['ratio_0_1', 'percent_0_100']


class SetDataLabels(_StrictModel):
    kind: Literal['set_data_labels']
    value: StrictBool


class SetLegend(_StrictModel):
    kind: Literal['set_legend']
    value: StrictBool


class SetMetricColor(_StrictModel):
    kind: Literal['set_metric_color']
    field: FieldName
    color: ChartPaletteToken


class SetCategoryColor(_StrictModel):
    kind: Literal['set_category_color']
    category_label: Annotated[str, Field(min_length=1, max_length=128, strict=True)]
    color: ChartPaletteToken


class TopN(_StrictModel):
    kind: Literal['top_n']
    field: FieldName
    count: Annotated[StrictInt, Field(ge=1, le=100)]
    direction: Literal['asc', 'desc']


# Plain union emits anyOf; do not add discriminator/oneOf requirements to providers.
ChartEditOperation = (
    SetChartType | SetOrientation | SetDimension | SetMetrics | SetHiddenMetrics
    | SetTitle | SetFieldLabel | SetSort | RestoreResultOrder
    | SetRawFormat | SetSuffixFormat | SetUnitFormat | SetPercentFormat | SetPrecision
    | SetDataLabels | SetLegend | SetMetricColor | SetCategoryColor | TopN
)


class ChartEditModelIntent(_StrictModel):
    operations: Annotated[list[ChartEditOperation], Field(max_length=24)]
    query_operation: ChartEditQueryOperation | None = Field(...)
    clarification_code: ChartEditClarificationCode | None = Field(...)

    @model_validator(mode='after')
    def validate_intent(self):
        if self.clarification_code is not None:
            if self.operations or self.query_operation is not None:
                raise ValueError('clarification cannot include operations')
        elif not self.operations and self.query_operation is None:
            raise ValueError('intent requires an operation')
        if sum(op.kind == 'set_category_color' for op in self.operations) > 8:
            raise ValueError('too many category colors')
        return self
