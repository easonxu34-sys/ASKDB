import pytest
from pydantic import ValidationError

from application.chart_edit import classify_chart_edit
from domain.chart_edit import (
    ChartEditCategoryColorOperation,
    ChartEditContext,
    ChartEditIntent,
    ChartEditPatch,
    ChartEditQueryProposal,
    ChartEditSort,
    ChartEditTopNOperation,
    ChartValueFormatPatch,
)


def make_patch(**values):
    fields = {
        "chart_type": None,
        "dimension_field": None,
        "metric_fields": None,
        "hidden_metric_fields": None,
        "bar_orientation": None,
        "title": None,
        "field_labels": None,
        "sort": None,
        "format_by_field": None,
        "show_data_labels": None,
        "show_legend": None,
        "color_by_metric": None,
    }
    fields.update(values)
    return ChartEditPatch(**fields)


def make_intent(
    *,
    status="apply",
    patch=None,
    top_n=None,
    category_colors=None,
    query_operation=None,
    clarification=None,
):
    return ChartEditIntent(
        status=status,
        patch=patch,
        current_result_operation=top_n,
        category_color_operations=category_colors or [],
        query_proposal=(
            ChartEditQueryProposal(operation=query_operation)
            if query_operation is not None
            else None
        ),
        clarification={"code": clarification} if clarification else None,
    )


def make_context(*, chart_type="bar", metrics=None, hidden=None, sort=None, row_count=5, truncated=False):
    view = {
        "chart_type": chart_type,
        "dimension_field": "region",
        "metric_fields": metrics if metrics is not None else ["revenue", "orders"],
        "hidden_metric_fields": hidden if hidden is not None else [],
        "sort": sort or {"mode": "original"},
        "field_labels": {"revenue": "收入", "orders": "订单数"},
    }
    return ChartEditContext(
        source_result_id="result-1",
        view=view,
        columns=("region", "revenue", "orders"),
        column_types=("string", "double", "int64"),
        row_count=row_count,
        truncated=truncated,
    )


def top_n(field="revenue", count=3, direction="desc"):
    return ChartEditTopNOperation(
        kind="top_n", field=field, count=count, direction=direction, scope="current_result"
    )


def test_intent_requires_consistent_status_payloads_and_forbids_extra_fields():
    with pytest.raises(ValidationError):
        make_intent()
    with pytest.raises(ValidationError):
        make_intent(status="unknown", patch=make_patch(title="收入"))
    with pytest.raises(ValidationError):
        ChartEditQueryProposal(operation="model_generated_sql")
    with pytest.raises(ValidationError):
        make_intent(clarification="free_text_question")
    with pytest.raises(ValidationError):
        ChartEditIntent(
            status="apply",
            patch=make_patch(title="收入"),
            current_result_operation=None,
            category_color_operations=[],
            query_proposal={"operation": "database_filter"},
            clarification=None,
        )
    with pytest.raises(ValidationError):
        make_patch(current_result_top_n={"field": "revenue"})

    apply_intent = make_intent(patch=make_patch(title="收入"))
    query_intent = make_intent(status="query_required", query_operation="database_filter")
    clarify_intent = make_intent(status="clarify", clarification="operation_unsupported")
    assert apply_intent.status == "apply"
    assert query_intent.query_proposal.operation == "database_filter"
    assert clarify_intent.clarification.code == "operation_unsupported"


@pytest.mark.parametrize("count", [0, -1, 101, 1.5, True])
def test_top_n_count_is_an_integer_between_one_and_one_hundred(count):
    with pytest.raises(ValidationError):
        top_n(count=count)


@pytest.mark.parametrize("count", [1, 100])
def test_top_n_accepts_inclusive_count_boundaries(count):
    assert top_n(count=count).count == count


def test_patch_is_sparse_and_keeps_only_supplied_display_fields():
    sparse = make_patch(field_labels={"revenue": "收入"})
    assert sparse.field_labels == {"revenue": "收入"}
    assert sparse.title is None
    assert sparse.format_by_field is None
    assert sparse.color_by_metric is None


def test_existing_field_display_edit_stays_local_and_preserves_sparse_patch():
    patch = make_patch(field_labels={"revenue": "收入"})
    result = classify_chart_edit("把收入显示名改成收入", make_intent(patch=patch), make_context())
    assert result.status == "apply"
    assert result.patch.field_labels == {"revenue": "收入"}
    assert result.query_proposal is None


@pytest.mark.parametrize(
    "patch",
    [
        make_patch(dimension_field="unknown"),
        make_patch(metric_fields=["revenue", "missing"]),
        make_patch(hidden_metric_fields=["unknown"]),
        make_patch(field_labels={"unknown": "收入"}),
        make_patch(format_by_field={
            "unknown": ChartValueFormatPatch(
                mode="raw", decimal_places="auto", suffix=None,
                unit_family=None, source_unit=None, display_unit=None, encoding=None
            )
        }),
        make_patch(color_by_metric={"unknown": "blue"}),
        make_patch(sort=ChartEditSort(mode="metric", field="unknown", direction="desc")),
    ],
)
def test_display_patch_rejects_fields_not_in_the_result(patch):
    result = classify_chart_edit("改一下图表标题", make_intent(patch=patch), make_context())
    assert result.status == "clarify"
    assert result.clarification.code == "field_not_in_result"


def test_top_n_requires_explicit_current_result_scope():
    result = classify_chart_edit(
        "收入前 3 名",
        make_intent(top_n=top_n()),
        make_context(),
    )
    assert result.status == "clarify"
    assert result.clarification.code == "top_n_scope_required"


def test_top_n_requires_a_selected_visible_numeric_metric():
    context = make_context(metrics=["orders"])
    result = classify_chart_edit(
        "当前返回结果里取前 3 名",
        make_intent(top_n=top_n()),
        context,
    )
    assert result.status == "clarify"
    assert result.clarification.code == "top_n_metric_required"


def test_top_n_requires_the_user_to_name_the_metric_even_if_model_guesses_one():
    result = classify_chart_edit(
        "当前结果前 3 名",
        make_intent(top_n=top_n(field="revenue")),
        make_context(),
    )
    assert result.status == "clarify"
    assert result.clarification.code == "top_n_metric_required"

    hidden = make_context(hidden=["revenue"])
    result = classify_chart_edit(
        "当前返回结果里收入前 3 名",
        make_intent(top_n=top_n()),
        hidden,
    )
    assert result.clarification.code == "top_n_metric_required"

    non_numeric = ChartEditContext(
        source_result_id="result-1",
        view={**make_context().view, "metric_fields": ["region"]},
        columns=("region", "revenue", "orders"),
        column_types=("string", "double", "int64"),
        row_count=5,
        truncated=False,
    )
    result = classify_chart_edit(
        "当前结果里地区前 3 名",
        make_intent(top_n=top_n(field="region")),
        non_numeric,
    )
    assert result.clarification.code == "top_n_metric_required"


def test_top_n_unknown_field_and_duplicate_column_names_fail_closed():
    result = classify_chart_edit(
        "当前返回结果里未知字段前 3 名",
        make_intent(top_n=top_n(field="unknown")),
        make_context(),
    )
    assert result.clarification.code == "field_not_in_result"

    duplicate = ChartEditContext(
        source_result_id="result-1",
        view=make_context().view,
        columns=("region", "revenue", "revenue"),
        column_types=("string", "double", "int64"),
        row_count=5,
        truncated=False,
    )
    result = classify_chart_edit(
        "当前结果里收入前 3 名",
        make_intent(top_n=top_n()),
        duplicate,
    )
    assert result.clarification.code == "field_not_in_result"


def test_top_n_is_descending_for_top_and_ascending_for_bottom_with_matching_sort():
    top = classify_chart_edit(
        "当前返回结果中的收入前 3 名",
        make_intent(top_n=top_n()),
        make_context(),
    )
    assert top.status == "apply"
    assert top.patch.sort.mode == "metric"
    assert top.patch.sort.field == "revenue"
    assert top.patch.sort.direction == "desc"

    bottom = classify_chart_edit(
        "当前返回结果中的收入末 2 名",
        make_intent(top_n=top_n(count=2, direction="asc")),
        make_context(),
    )
    assert bottom.status == "apply"
    assert bottom.patch.sort.direction == "asc"


def test_top_n_rejects_line_charts_and_sort_conflicts():
    line_result = classify_chart_edit(
        "当前结果中收入前 3 名",
        make_intent(top_n=top_n()),
        make_context(chart_type="line"),
    )
    assert line_result.clarification.code == "chart_type_incompatible"

    sort_conflict = classify_chart_edit(
        "当前结果中收入前 3 名并按地区排序",
        make_intent(
            patch=make_patch(sort=ChartEditSort(mode="dimension", field="region", direction="asc")),
            top_n=top_n(),
        ),
        make_context(),
    )
    assert sort_conflict.clarification.code == "conflicting_sort"

    missing_sort_intent = classify_chart_edit(
        "当前结果中收入前 3 名并排序",
        make_intent(top_n=top_n()),
        make_context(),
    )
    assert missing_sort_intent.clarification.code == "conflicting_sort"


def test_top_n_and_category_colors_cannot_change_their_own_eligibility():
    top_n_patch = make_patch(metric_fields=["revenue"], chart_type="bar")
    top_n_result = classify_chart_edit(
        "当前结果中收入前 3 名",
        make_intent(patch=top_n_patch, top_n=top_n()),
        make_context(metrics=["orders"]),
    )
    assert top_n_result.clarification.code == "top_n_metric_required"

    not_currently_pie = classify_chart_edit(
        "把华东设成蓝色",
        make_intent(
            patch=make_patch(chart_type="pie"),
            category_colors=[ChartEditCategoryColorOperation(category_label="华东", color="blue")],
        ),
        make_context(),
    )
    assert not_currently_pie.clarification.code == "chart_type_incompatible"

    pie_metric_color = classify_chart_edit(
        "把收入设成蓝色",
        make_intent(patch=make_patch(color_by_metric={"revenue": "blue"})),
        make_context(chart_type="pie", metrics=["revenue"], row_count=3),
    )
    assert pie_metric_color.clarification.code == "chart_type_incompatible"


@pytest.mark.parametrize(
    ("instruction", "query_operation"),
    [
        ("筛选华东地区", "database_filter"),
        ("按月聚合销售额", "aggregation"),
        ("和去年同期做同比", "period_comparison"),
        ("按全量数据取收入前 10 名", "full_data_top_n"),
    ],
)
def test_database_filter_aggregation_period_comparison_and_full_data_top_n_require_query(
    instruction, query_operation
):
    patch = make_patch(title="筛选后的收入")
    result = classify_chart_edit(
        instruction,
        make_intent(status="query_required", patch=patch, query_operation=query_operation),
        make_context(),
    )
    assert result.status == "query_required"
    assert result.query_proposal.operation == query_operation
    assert result.patch.title == "筛选后的收入"


def test_unrecognized_or_mismatched_query_proposals_are_not_trusted():
    result = classify_chart_edit(
        "把标题改成收入",
        make_intent(status="query_required", query_operation="database_filter"),
        make_context(),
    )
    assert result.status == "clarify"
    assert result.clarification.code == "operation_unsupported"


def test_display_edit_does_not_hide_a_following_data_operation():
    for instruction, operation in (
        ("把标题改成收入并按月聚合", "aggregation"),
        ("把标题改成收入且按月聚合", "aggregation"),
        ("把标题改成‘收入’并做同比", "period_comparison"),
        ("把标题改成‘收入’并和去年同期比较", "period_comparison"),
    ):
        result = classify_chart_edit(
            instruction,
            make_intent(patch=make_patch(title="收入")),
            make_context(),
        )
        assert result.status == "query_required"
        assert result.query_proposal.operation == operation
        assert result.patch.title == "收入"


@pytest.mark.parametrize(
    "instruction",
    ["把标题改成同比收入", "标题改为月度统计", "把标题改成‘并购月度统计’"],
)
def test_query_keywords_inside_display_text_do_not_require_a_query(instruction):
    result = classify_chart_edit(
        instruction,
        make_intent(patch=make_patch(title="月度统计")),
        make_context(),
    )
    assert result.status == "apply"


def test_missing_top_n_metric_and_ambiguous_scope_require_clarification():
    no_metric = classify_chart_edit(
        "当前结果中的前 5 名",
        make_intent(patch=make_patch(title="Top 5")),
        make_context(),
    )
    assert no_metric.clarification.code == "top_n_metric_required"

    ambiguous_scope = classify_chart_edit(
        "前 5 名收入",
        make_intent(top_n=top_n(count=5)),
        make_context(),
    )
    assert ambiguous_scope.clarification.code == "top_n_scope_required"

    title_only_metric = classify_chart_edit(
        "把标题改成收入，并在当前结果前 5 名",
        make_intent(top_n=top_n(count=5)),
        make_context(),
    )
    assert title_only_metric.clarification.code == "top_n_metric_required"


def test_unit_scale_requires_user_declared_source_and_target_units():
    unit_format = ChartValueFormatPatch(
        mode="unit_scale",
        decimal_places="auto",
        suffix=None,
        unit_family="CNY",
        source_unit="yuan",
        display_unit="ten_thousand_yuan",
        encoding=None,
    )
    patch = make_patch(format_by_field={"revenue": unit_format})
    ambiguous = classify_chart_edit("把收入改成万元", make_intent(patch=patch), make_context())
    assert ambiguous.clarification.code == "source_unit_required"

    explicit = classify_chart_edit(
        "收入源单位是元，显示单位改成万元",
        make_intent(patch=patch),
        make_context(),
    )
    assert explicit.status == "apply"
    assert explicit.patch.format_by_field["revenue"].source_unit == "yuan"

    wrong_target = classify_chart_edit(
        "收入源单位是元，显示单位改成万元",
        make_intent(patch=make_patch(format_by_field={
            "revenue": unit_format.model_copy(update={"display_unit": "yuan"})
        })),
        make_context(),
    )
    assert wrong_target.clarification.code == "source_unit_required"

    two_metric_context = ChartEditContext(
        source_result_id="result-1",
        view={**make_context().view,
              "metric_fields": ["revenue", "profit"],
              "field_labels": {"revenue": "收入", "profit": "利润"}},
        columns=("region", "revenue", "orders", "profit"),
        column_types=("string", "double", "int64", "double"),
        row_count=5,
        truncated=False,
    )
    profit_format = unit_format.model_copy(update={
        "source_unit": "thousand_yuan", "display_unit": "yuan"
    })
    both_formats = make_patch(format_by_field={
        "revenue": unit_format, "profit": profit_format
    })
    multi_unit = classify_chart_edit(
        "收入源单位是元，显示单位改成万元；利润源单位是千元，显示单位改成元",
        make_intent(patch=both_formats),
        two_metric_context,
    )
    assert multi_unit.status == "apply"

    mismatched_profit = classify_chart_edit(
        "收入源单位是元，显示单位改成万元；利润源单位是千元，显示单位改成元",
        make_intent(patch=make_patch(format_by_field={
            "revenue": unit_format, "profit": unit_format
        })),
        two_metric_context,
    )
    assert mismatched_profit.clarification.code == "source_unit_required"

    repeated_target = classify_chart_edit(
        "收入源单位是元，显示单位改成万元，最终显示单位改成亿元",
        make_intent(patch=make_patch(format_by_field={"revenue": unit_format})),
        make_context(),
    )
    assert repeated_target.clarification.code == "source_unit_required"

    for instruction in (
        "收入源单位是元，显示单位不是万元而是亿元",
        "收入源单位是元，显示单位不要改成万元",
        "收入源单位是元，禁止改成万元",
    ):
        negated_target = classify_chart_edit(
            instruction,
            make_intent(patch=make_patch(format_by_field={"revenue": unit_format})),
            make_context(),
        )
        assert negated_target.clarification.code == "source_unit_required"


def test_category_colors_are_pie_only_fixed_tokens_and_conflicts_are_atomic():
    operation = ChartEditCategoryColorOperation(category_label="华东", color="blue")
    pie = make_context(chart_type="pie", metrics=["revenue"], row_count=3)
    result = classify_chart_edit(
        "把华东设成蓝色",
        make_intent(category_colors=[operation]),
        pie,
    )
    assert result.status == "apply"
    assert result.category_color_operations == [operation]

    not_pie = classify_chart_edit(
        "把华东设成蓝色",
        make_intent(category_colors=[operation]),
        make_context(),
    )
    assert not_pie.clarification.code == "chart_type_incompatible"

    conflict = classify_chart_edit(
        "把华东设成蓝色或红色",
        make_intent(
            category_colors=[
                operation,
                ChartEditCategoryColorOperation(category_label="华东", color="red"),
            ]
        ),
        pie,
    )
    assert conflict.clarification.code == "conflicting_category_color"
    assert conflict.category_color_operations == []

    repeated_same_color = classify_chart_edit(
        "把华东设成蓝色",
        make_intent(category_colors=[operation, operation]),
        pie,
    )
    assert repeated_same_color.status == "apply"
    assert repeated_same_color.category_color_operations == [operation]

    with pytest.raises(ValidationError):
        ChartEditCategoryColorOperation(category_label="华东", color="#fff")


@pytest.mark.parametrize(
    ("row_count", "truncated"),
    [(9, False), (3, True)],
)
def test_pie_top_n_requires_existing_pie_constraints(row_count, truncated):
    result = classify_chart_edit(
        "当前结果中收入前 3 名",
        make_intent(top_n=top_n()),
        make_context(chart_type="pie", metrics=["revenue"], row_count=row_count, truncated=truncated),
    )
    assert result.clarification.code == "chart_type_incompatible"


def test_pie_top_n_is_allowed_when_existing_pie_view_constraints_pass():
    result = classify_chart_edit(
        "当前返回结果中的收入前 3 名",
        make_intent(top_n=top_n()),
        make_context(chart_type="pie", metrics=["revenue"], row_count=3),
    )
    assert result.status == "apply"
    assert result.patch.sort == ChartEditSort(mode="metric", field="revenue", direction="desc")


def test_orientation_requires_an_effective_bar_chart():
    result = classify_chart_edit('改成横向显示',
        make_intent(patch=make_patch(bar_orientation='horizontal')), make_context(chart_type='line'))
    assert result.status == 'clarify'
    assert result.clarification.code == 'chart_type_incompatible'


def test_selecting_metrics_cleans_inherited_hidden_fields_but_rejects_explicit_invalid_ones():
    ctx = make_context(hidden=['revenue'])
    inherited = classify_chart_edit('只用订单数作为指标',
        make_intent(patch=make_patch(metric_fields=['orders'])), ctx)
    assert inherited.status == 'apply'
    explicit = classify_chart_edit('只用订单数作为指标并隐藏收入',
        make_intent(patch=make_patch(metric_fields=['orders'], hidden_metric_fields=['revenue'])), ctx)
    assert explicit.status == 'clarify'
