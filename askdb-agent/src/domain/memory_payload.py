"""Bounded, category-specific personal-memory payloads (no executable text)."""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

Scalar = StrictStr | StrictBool | StrictInt | StrictFloat
Column = Annotated[str, Field(min_length=3, max_length=256, pattern=r'^[\w]+\.[\w]+$')]


class PayloadModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class FilterSpec(PayloadModel):
    column: Column
    op: Literal['eq', 'in', 'gte', 'lt']
    value: Scalar | tuple[Scalar, ...]

    @model_validator(mode='after')
    def check_value(self):
        values = self.value if isinstance(self.value, tuple) else (self.value,)
        if self.op == 'in' and (not isinstance(self.value, tuple) or not 1 <= len(values) <= 100):
            raise ValueError('in needs a bounded nonempty list')
        if self.op != 'in' and isinstance(self.value, tuple):
            raise ValueError('scalar operator needs one value')
        if any((isinstance(v, float) and not math.isfinite(v)) or
               (isinstance(v, str) and len(v) > 256) for v in values):
            raise ValueError('unbounded filter value')
        return self


class MetricSpec(PayloadModel):
    column: Column
    aggregation: Literal['sum', 'count', 'avg', 'min', 'max']
    alias: str | None = Field(default=None, max_length=128)


class TimeSpec(PayloadModel):
    column: Column
    relative: Literal['last_30_days', 'this_month', 'last_month'] | None = None
    start: str | None = Field(default=None, max_length=32)
    end: str | None = Field(default=None, max_length=32)

    @model_validator(mode='after')
    def check_range(self):
        if self.relative and (self.start or self.end):
            raise ValueError('relative and absolute time cannot be mixed')
        if not self.relative:
            from datetime import date
            if not self.start or not self.end or date.fromisoformat(self.start) >= date.fromisoformat(self.end):
                raise ValueError('absolute time needs ordered ISO dates')
        return self


class DisplaySpec(PayloadModel):
    language: Literal['中文', '英文', '简体中文', '繁体中文', 'Chinese', 'English', 'zh', 'en', 'zh-CN'] | None = None
    address: str | None = Field(default=None, max_length=80)
    organization: str | None = Field(default=None, max_length=160)
    unit: Literal['元', '千元', '万元', '亿元'] | None = None
    chart_type: Literal['bar', 'line', 'pie', '柱状图', '折线图', '饼图'] | None = None


class BindingSpec(PayloadModel):
    source_id: str = Field(min_length=1, max_length=128)
    revision: str = Field(min_length=1, max_length=128)
    digest: str = Field(min_length=1, max_length=128)


class TriggerSpec(PayloadModel):
    task: Literal['any', 'query', 'analysis'] = 'any'
    terms: tuple[Annotated[str, Field(min_length=1, max_length=80)], ...] = Field(default=(), max_length=20)


class MemoryPayload(DisplaySpec):
    filters: tuple[FilterSpec, ...] = Field(default=(), max_length=30)
    metric: MetricSpec | None = None
    time_rule: TimeSpec | None = None
    binding: BindingSpec | None = None
    trigger: TriggerSpec | None = None
    steps: tuple[Literal['query', 'group', 'sort', 'compare', 'trend'], ...] = Field(default=(), max_length=30)
    references: tuple[Column, ...] = Field(default=(), max_length=40)
    display: DisplaySpec | None = None


PRESENTATION_KEYS = frozenset({'language', 'address', 'organization', 'unit', 'chart_type', 'display', 'trigger'})
SEMANTIC_KEYS = frozenset({'filters', 'metric', 'time_rule', 'binding'})
ALLOWED_KEYS = {
    'expression': frozenset({'language', 'address', 'organization', 'trigger'}),
    # Older records called language a display preference. Keep that harmless mapping.
    'display': PRESENTATION_KEYS,
    'default_filter': frozenset({'filters', 'time_rule', 'binding', 'trigger'}),
    'metric_definition': frozenset({'metric', 'binding', 'trigger'}),
    'analysis_steps': frozenset({'steps', 'references', 'trigger'}),
    'analysis_recipe': PRESENTATION_KEYS | SEMANTIC_KEYS | {'steps', 'references'},
}


def normalize_payload(kind: str, payload: dict) -> dict:
    if not isinstance(payload, dict) or kind not in ALLOWED_KEYS or set(payload) - ALLOWED_KEYS[kind]:
        raise ValueError('payload exceeds memory category capability')
    return MemoryPayload.model_validate(payload).model_dump(mode='json', exclude_unset=True, exclude_none=True)


def payload_schemas() -> dict:
    schema = MemoryPayload.model_json_schema()
    return {'$defs': schema.get('$defs', {}), 'by_kind': {
        kind: {'type': 'object', 'additionalProperties': False,
               'properties': {key: value for key, value in schema['properties'].items() if key in allowed}}
        for kind, allowed in ALLOWED_KEYS.items()}}
