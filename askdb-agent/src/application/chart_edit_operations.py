"""Deterministic conversion to the existing, validated internal API intent."""
from __future__ import annotations

from collections.abc import Mapping
from domain.chart_edit import ChartEditContext, ChartEditIntent, ChartEditPatch
from domain.chart_edit_operations import ChartEditModelIntent


_SCALARS = {
    'set_chart_type': 'chart_type', 'set_orientation': 'bar_orientation',
    'set_dimension': 'dimension_field', 'set_metrics': 'metric_fields',
    'set_hidden_metrics': 'hidden_metric_fields', 'set_title': 'title',
    'set_data_labels': 'show_data_labels', 'set_legend': 'show_legend',
}
_FORMAT_MODES = {
    'set_raw_format': 'raw', 'set_suffix_format': 'suffix',
    'set_unit_format': 'unit_scale', 'set_percent_format': 'percent',
}


def compile_chart_edit_operations(
    intent: ChartEditModelIntent, context: ChartEditContext | None = None,
) -> ChartEditIntent:
    patch: dict[str, object] = dict.fromkeys(ChartEditPatch.model_fields)
    top_n = None
    categories: dict[str, str] = {}
    targets: dict[tuple[str, str], object] = {}
    precisions: dict[str, object] = {}
    current_formats = context.view.get('format_by_field', {}) if context else {}
    if not isinstance(current_formats, Mapping):
        current_formats = {}

    def assign(target: tuple[str, str], value: object) -> bool:
        if target in targets and targets[target] != value:
            return False
        targets[target] = value
        return True

    for operation in intent.operations:
        data = operation.model_dump(mode='python')
        kind = operation.kind
        if kind in _SCALARS:
            key = _SCALARS[kind]
            value = data.get('value', data.get('field', data.get('fields')))
            valid = assign((key, ''), value)
            patch[key] = value
        elif kind in {'set_field_label', 'set_metric_color'} or kind in _FORMAT_MODES:
            field = data['field']
            if kind == 'set_field_label':
                key, value = 'field_labels', data['value']
            elif kind == 'set_metric_color':
                key, value = 'color_by_metric', data['color']
            else:
                key = 'format_by_field'
                current = current_formats.get(field, {})
                precision = current.get('decimal_places', 'auto') if isinstance(current, Mapping) else 'auto'
                value = {'mode': _FORMAT_MODES[kind], 'decimal_places': precision}
                for name in ('suffix', 'source_unit', 'display_unit', 'encoding'):
                    if name in data:
                        value[name] = data[name]
                if kind == 'set_unit_format':
                    value['unit_family'] = 'CNY'
            valid = assign((key, field), value)
            if patch[key] is None:
                patch[key] = {}
            patch[key][field] = value
        elif kind == 'set_precision':
            valid = assign(('precision', data['field']), data['value'])
            precisions[data['field']] = data['value']
        elif kind in {'set_sort', 'restore_result_order'}:
            value = (
                {'mode': 'original', 'field': None, 'direction': None}
                if kind == 'restore_result_order'
                else {name: data[name] for name in ('mode', 'field', 'direction')}
            )
            valid = assign(('sort', ''), value)
            patch['sort'] = value
        elif kind == 'top_n':
            top_n = {**{name: data[name] for name in ('field', 'count', 'direction')},
                     'kind': 'top_n', 'scope': 'current_result'}
            valid = assign(('top_n', ''), top_n)
        else:  # SetCategoryColor is the only remaining typed variant.
            valid = assign(('category', data['category_label']), data['color'])
            categories[data['category_label']] = data['color']
        if not valid:
            code = ('conflicting_sort' if kind in {'set_sort', 'restore_result_order', 'top_n'}
                    else 'conflicting_category_color' if kind == 'set_category_color'
                    else 'operation_unsupported')
            return ChartEditIntent(status='clarify', patch=None, current_result_operation=None,
                category_color_operations=[], query_proposal=None, clarification={'code': code})

    for field, precision in precisions.items():
        if patch['format_by_field'] is None:
            patch['format_by_field'] = {}
        formats = patch['format_by_field']
        if field not in formats:
            current = current_formats.get(field, {})
            formats[field] = dict(current) if isinstance(current, Mapping) and current else {'mode': 'raw'}
        formats[field]['decimal_places'] = precision

    status = ('clarify' if intent.clarification_code is not None
              else 'query_required' if intent.query_operation is not None else 'apply')
    return ChartEditIntent(
        status=status,
        patch=patch if any(value is not None for value in patch.values()) else None,
        current_result_operation=top_n,
        category_color_operations=[{'category_label': label, 'color': color}
                                   for label, color in categories.items()],
        query_proposal={'operation': intent.query_operation} if intent.query_operation else None,
        clarification={'code': intent.clarification_code} if intent.clarification_code else None,
    )
