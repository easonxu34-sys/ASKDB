import pytest
from pydantic import ValidationError


def compile_operations(operations, **changes):
    from domain.chart_edit_operations import ChartEditModelIntent
    from application.chart_edit_operations import compile_chart_edit_operations
    payload = dict(operations=operations, query_operation=None, clarification_code=None)
    payload.update(changes)
    return compile_chart_edit_operations(ChartEditModelIntent.model_validate(payload))


def test_short_intent_compiles_only_requested_fields_and_not_sort():
    result = compile_operations([
        {'kind': 'set_chart_type', 'value': 'bar'},
        {'kind': 'set_metric_color', 'field': 'revenue', 'color': 'blue'},
    ])
    assert result.status == 'apply'
    assert result.patch.chart_type == 'bar'
    assert result.patch.color_by_metric == {'revenue': 'blue'}
    assert result.patch.sort is None
    assert result.patch.title is None


@pytest.mark.parametrize(('operation', 'field', 'expected'), [
    ({'kind': 'set_dimension', 'field': 'region'}, 'dimension_field', 'region'),
    ({'kind': 'set_metrics', 'fields': ['revenue']}, 'metric_fields', ['revenue']),
    ({'kind': 'set_hidden_metrics', 'fields': []}, 'hidden_metric_fields', []),
    ({'kind': 'set_orientation', 'value': 'horizontal'}, 'bar_orientation', 'horizontal'),
    ({'kind': 'set_title', 'value': '收入'}, 'title', '收入'),
    ({'kind': 'set_field_label', 'field': 'region', 'value': '地区'}, 'field_labels', {'region': '地区'}),
    ({'kind': 'set_data_labels', 'value': True}, 'show_data_labels', True),
    ({'kind': 'set_legend', 'value': False}, 'show_legend', False),
])
def test_existing_display_capabilities_compile(operation, field, expected):
    assert getattr(compile_operations([operation]).patch, field) == expected


@pytest.mark.parametrize(('kind', 'parameters', 'mode'), [
    ('set_raw_format', {}, 'raw'),
    ('set_suffix_format', {'suffix': '人'}, 'suffix'),
    ('set_unit_format', {'source_unit': 'yuan', 'display_unit': 'ten_thousand_yuan'}, 'unit_scale'),
    ('set_percent_format', {'encoding': 'ratio_0_1'}, 'percent'),
])
def test_format_operations_need_only_mode_specific_parameters(kind, parameters, mode):
    result = compile_operations([{'kind': kind, 'field': 'revenue', **parameters},
        {'kind': 'set_precision', 'field': 'revenue', 'value': 2}])
    assert result.patch.format_by_field['revenue'].mode == mode
    assert result.patch.format_by_field['revenue'].decimal_places == 2


def test_explicit_result_order_has_no_field_or_direction_parameters():
    result = compile_operations([{'kind': 'restore_result_order'}])
    assert result.patch.sort.mode == 'original'
    assert result.patch.sort.field is None
    assert result.patch.sort.direction is None


def test_rank_and_category_operations_keep_typed_internal_contract():
    result = compile_operations([
        {'kind': 'top_n', 'field': 'revenue', 'count': 3, 'direction': 'desc'},
        {'kind': 'set_category_color', 'category_label': '华东', 'color': 'red'},
    ])
    assert result.current_result_operation.scope == 'current_result'
    assert result.current_result_operation.count == 3
    assert result.category_color_operations[0].category_label == '华东'
    assert result.patch is None


def test_query_and_clarification_use_existing_status_contract():
    mixed = compile_operations([{'kind': 'set_title', 'value': '月度收入'}], query_operation='aggregation')
    assert mixed.status == 'query_required'
    assert mixed.query_proposal.operation == 'aggregation'
    assert mixed.patch.title == '月度收入'
    clarified = compile_operations([], clarification_code='source_unit_required')
    assert clarified.status == 'clarify'
    assert clarified.patch is None


def test_conflicting_operations_clarify_and_identical_operations_deduplicate():
    conflict = compile_operations([
        {'kind': 'set_title', 'value': 'A'}, {'kind': 'set_title', 'value': 'B'},
    ])
    assert conflict.status == 'clarify'
    assert conflict.patch is None
    same = compile_operations([
        {'kind': 'set_title', 'value': 'A'}, {'kind': 'set_title', 'value': 'A'},
    ])
    assert same.patch.title == 'A'


@pytest.mark.parametrize('operations', [
    [], [{'kind': 'set_title', 'value': 'A', 'sql': 'secret'}],
    [{'kind': 'set_echarts', 'value': {}}],
    [{'kind': 'set_metrics', 'fields': ['revenue', 'revenue']}],
    [{'kind': 'top_n', 'field': 'revenue', 'count': True, 'direction': 'desc'}],
    [{'kind': 'set_title', 'value': 'A'}] * 25,
])
def test_invalid_short_protocol_is_rejected(operations):
    with pytest.raises(ValidationError):
        compile_operations(operations)


def test_model_schema_does_not_expose_patch_or_irrelevant_format_fields():
    from domain.chart_edit_operations import ChartEditModelIntent
    schema = ChartEditModelIntent.model_json_schema()
    assert set(schema['properties']) == {'operations', 'query_operation', 'clarification_code'}
    for variant in schema['$defs'].values():
        if variant.get('properties', {}).get('kind', {}).get('const') == 'set_raw_format':
            assert set(variant['properties']) == {'kind', 'field'}


def test_precision_only_edit_preserves_existing_format_mode_and_units():
    from domain.chart_edit import ChartEditContext
    from domain.chart_edit_operations import ChartEditModelIntent
    from application.chart_edit_operations import compile_chart_edit_operations
    ctx = ChartEditContext(source_result_id='r', view={'format_by_field': {'revenue': {
        'mode': 'suffix', 'decimal_places': 3, 'suffix': '人'}}},
        columns=('revenue',), column_types=('double',), row_count=1, truncated=False)
    value = ChartEditModelIntent.model_validate(dict(operations=[
        {'kind': 'set_precision', 'field': 'revenue', 'value': 1}],
        query_operation=None, clarification_code=None))
    result = compile_chart_edit_operations(value, ctx)
    formatted = result.patch.format_by_field['revenue']
    assert formatted.mode == 'suffix'
    assert formatted.suffix == '人'
    assert formatted.decimal_places == 1


def test_format_mode_edit_preserves_current_precision_without_model_repeating_it():
    from dataclasses import replace
    from test_chart_edit_interpreter import context
    from domain.chart_edit_operations import ChartEditModelIntent
    from application.chart_edit_operations import compile_chart_edit_operations
    ctx = replace(context(), view={**context().view, 'format_by_field': {
        'revenue': {'mode': 'raw', 'decimal_places': 3}}})
    value = ChartEditModelIntent.model_validate(dict(operations=[
        {'kind': 'set_suffix_format', 'field': 'revenue', 'suffix': '人'}],
        query_operation=None, clarification_code=None))
    assert compile_chart_edit_operations(value, ctx).patch.format_by_field['revenue'].decimal_places == 3
