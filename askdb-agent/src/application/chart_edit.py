from __future__ import annotations

import re
import unicodedata
from typing import Any

from domain.chart_edit import (
    ChartEditClarificationCode,
    ChartEditContext,
    ChartEditIntent,
    ChartEditPatch,
    ChartEditQueryOperation,
    ChartEditQueryProposal,
    ChartEditSort,
    ChartEditTopNOperation,
    ChartType,
    ChartValueFormatPatch,
)


_TOP_N = re.compile(
    r"(?:\b(?:top|bottom)\s*\d+\b|(?:前|末|最高|最低)\s*\d+\s*(?:名|条|个|项)?)",
    re.IGNORECASE,
)
_CURRENT_RESULT_SCOPE = re.compile(
    r"当前(?:已)?(?:返回)?(?:的)?结果|返回结果|已返回(?:的)?数据|这批(?:结果|数据)|current\s+results?",
    re.IGNORECASE,
)
_FULL_DATA_SCOPE = re.compile(
    r"全量(?:数据|结果)?|全部数据|所有数据|整个数据库|全库|full[- ]dataset|all\s+data",
    re.IGNORECASE,
)
_NUMERIC_TYPE = re.compile(
    r"^(?:u?int(?:8|16|32|64)?|float(?:16|32|64)?|double|decimal(?:32|64|128|256)?|numeric)(?:$|[\s[(])",
    re.IGNORECASE,
)
_FILTER = re.compile(r"筛选|过滤|filter(?:\s+by)?", re.IGNORECASE)
_AGGREGATION = re.compile(r"按月|月度(?:汇总|统计|聚合)?|聚合|aggregate|group\s+by", re.IGNORECASE)
_PERIOD_COMPARISON = re.compile(r"同比|环比|去年同期|同期对比|period\s+comparison", re.IGNORECASE)
_SORT = re.compile(r"排序|sort(?:\s+by)?", re.IGNORECASE)
_CHART_TYPE_ONLY = re.compile(
    r"^(?:(?:请帮我|帮我|麻烦|请)\s*)?(?:(?:把|将)\s*)?(?:(?:当前|这个|该)\s*)?"
    r"(?:图表\s*)?(?:换成|换为|改成|改为|切换到|切换成|切换为|切到|调成|变成|"
    r"设置为|设为|用|使用)\s*"
    r"(?P<target>折线图|折线|柱状图|柱形图|条形图)(?:吧|即可|吗)?[。.!！?？]*$",
    re.IGNORECASE,
)
_DISPLAY_ASSIGNMENT_PREFIX = re.compile(
    r"(?:图表)?(?:标题|字段标签|标签|图例名|图例名称|名称|轴名|轴标签)\s*"
    r"(?:改为|改成|设置为|设为|写成|命名为|调整为)\s*",
    re.IGNORECASE,
)
_DISPLAY_OPERATION_START = re.compile(
    r"(?:按|筛选|过滤|聚合|汇总|统计|排序|比较|查询|取前|全量|做同比|同比|环比|对比|分组|"
    r"和(?:去年同期|同期)|仅看|只看|从|根据|top|bottom|filter|aggregate|group\s+by|sort)",
    re.IGNORECASE,
)
_DISPLAY_CONNECTORS = ("然后", "接着", "同时", "并且", "而且", "并", "且", "再")
_QUOTE_PAIRS = {"“": "”", "‘": "’", "「": "」", "『": "』", "\"": "\"", "'": "'", "«": "»"}
_UNIT_WORDS = {
    "yuan": "元",
    "thousand_yuan": "千元",
    "ten_thousand_yuan": "万元",
    "hundred_million_yuan": "亿元",
}
_UNIT_TEXT_TO_CODE = {value: key for key, value in _UNIT_WORDS.items()}
_UNIT_TEXT_PATTERN = r"(?:亿元|万元|千元|元)"
_UNIT_NEGATION = re.compile(r"不是|并非|不要|不应|不能|禁止|不再|而非|而是")


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _clarify(code: ChartEditClarificationCode) -> ChartEditIntent:
    return ChartEditIntent(
        status="clarify",
        patch=None,
        current_result_operation=None,
        category_color_operations=[],
        query_proposal=None,
        clarification={"code": code},
    )


def _operation_text(instruction: str) -> str:
    text = _normalized(instruction)
    parts: list[str] = []
    cursor = 0
    for match in _DISPLAY_ASSIGNMENT_PREFIX.finditer(text):
        if match.start() < cursor:
            continue
        end = match.end()
        expected_quote: str | None = None
        while end < len(text):
            character = text[end]
            if expected_quote is not None:
                if character == expected_quote:
                    expected_quote = None
                    end += 1
                    break
                end += 1
                continue
            if character in _QUOTE_PAIRS:
                expected_quote = _QUOTE_PAIRS[character]
                end += 1
                continue
            if character in "，,。；;!?！？":
                break
            connector = next(
                (item for item in _DISPLAY_CONNECTORS if text.startswith(item, end)),
                None,
            )
            if connector is not None and _DISPLAY_OPERATION_START.match(
                text[end + len(connector):].lstrip()
            ):
                break
            end += 1
        parts.extend((text[cursor:match.start()], " "))
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _query_operation(instruction: str) -> ChartEditQueryOperation | None:
    text = _operation_text(instruction)
    top_n_requested = _TOP_N.search(text) is not None
    if top_n_requested and _FULL_DATA_SCOPE.search(text):
        return "full_data_top_n"
    if _PERIOD_COMPARISON.search(text):
        return "period_comparison"
    if _AGGREGATION.search(text):
        return "aggregation"
    if _FILTER.search(text):
        return "database_filter"
    return None


def _requested_top_n(text: str) -> tuple[int, str] | None:
    match = _TOP_N.search(_operation_text(text))
    if match is None:
        return None
    number = re.search(r"\d+", match.group(0))
    if number is None:
        return None
    marker = match.group(0).casefold()
    direction = "asc" if marker.startswith(("bottom", "末", "最低")) else "desc"
    return int(number.group(0)), direction


def _view_list(view: dict[str, Any], name: str) -> list[str]:
    value = view.get(name)
    return value if isinstance(value, list) and all(isinstance(item, str) for item in value) else []


def _effective_metrics(patch: ChartEditPatch | None, view: dict[str, Any]) -> list[str]:
    if patch is not None and patch.metric_fields is not None:
        return patch.metric_fields
    return _view_list(view, "metric_fields")


def _effective_hidden_metrics(patch: ChartEditPatch | None, view: dict[str, Any]) -> list[str]:
    if patch is not None and patch.hidden_metric_fields is not None:
        return patch.hidden_metric_fields
    hidden = _view_list(view, "hidden_metric_fields")
    if patch is not None and patch.metric_fields is not None:
        return [field for field in hidden if field in patch.metric_fields]
    return hidden


def _valid_pie_view(context: ChartEditContext, view: dict[str, Any]) -> bool:
    metrics = _view_list(view, "metric_fields")
    hidden = _view_list(view, "hidden_metric_fields")
    return (
        view.get("chart_type") == "pie"
        and not context.truncated
        and context.row_count <= 8
        and len(metrics) == 1
        and not hidden
    )


def _unit_source_and_target_are_explicit(
    instruction: str,
    field: str,
    value: ChartValueFormatPatch,
    context: ChartEditContext,
) -> bool:
    text = _operation_text(instruction)
    view = dict(context.view)
    labels = view.get("field_labels")
    label = labels.get(field) if isinstance(labels, dict) else None
    mentions = _field_mentions(text, field, label if isinstance(label, str) else None)
    if len(mentions) != 1:
        return False
    mention_start, mention_end = mentions[0]

    other_mentions: list[tuple[int, int, str]] = []
    for other_field in context.columns:
        if other_field == field:
            continue
        other_label = labels.get(other_field) if isinstance(labels, dict) else None
        other_mentions.extend(
            (start, end, other_field)
            for start, end in _field_mentions(
                text, other_field, other_label if isinstance(other_label, str) else None
            )
        )
    if any(start < mention_end and end > mention_start for start, end, _ in other_mentions):
        return False
    next_field_start = min(
        (start for start, _, _ in other_mentions if start >= mention_end),
        default=len(text),
    )
    field_clause = text[mention_end:next_field_start]
    if _UNIT_NEGATION.search(field_clause):
        return False
    sources = list(re.finditer(
        rf"(?:源单位|原单位)\s*(?:是|为|[:：])?\s*({_UNIT_TEXT_PATTERN})(?![万千亿])",
        field_clause,
    ))
    targets = list(re.finditer(
        rf"(?:设置显示(?:单位)?(?:为|改成|设为)|显示(?:单位)?|显示成|换算成|"
        rf"改成|设置为|设为|使用)\s*"
        rf"(?:(?:改为|改成|设置为|设置成|设为|调整为|为|是|[:：])\s*)?"
        rf"({_UNIT_TEXT_PATTERN})(?![万千亿])",
        field_clause,
    ))
    source = sources[0] if len(sources) == 1 else None
    target = targets[0] if len(targets) == 1 else None
    return bool(
        source
        and target
        and _UNIT_TEXT_TO_CODE.get(source.group(1)) == value.source_unit
        and _UNIT_TEXT_TO_CODE.get(target.group(1)) == value.display_unit
    )


def _validate_patch_units(
    instruction: str, patch: ChartEditPatch | None, context: ChartEditContext
) -> bool:
    if patch is None or patch.format_by_field is None:
        return True
    for field, value in patch.format_by_field.items():
        if value.mode == "unit_scale" and not _unit_source_and_target_are_explicit(
            instruction, field, value, context
        ):
            return False
    return True


def _column_index(context: ChartEditContext, field: str) -> int | None:
    matches = [index for index, column in enumerate(context.columns) if column == field]
    return matches[0] if len(matches) == 1 else None


def _is_numeric_field(context: ChartEditContext, field: str) -> bool:
    index = _column_index(context, field)
    return (
        index is not None
        and len(context.column_types) == len(context.columns)
        and _NUMERIC_TYPE.match(context.column_types[index].strip()) is not None
    )


def _chart_type_only_target(instruction: str) -> ChartType | None:
    match = _CHART_TYPE_ONLY.fullmatch(_normalized(instruction).strip())
    if match is None:
        return None
    target = match.group("target")
    if target in {"折线", "折线图"}:
        return "line"
    return "bar"


def _can_switch_to_line_or_bar(target: ChartType, context: ChartEditContext) -> bool:
    if target not in {"line", "bar"}:
        return False
    view = dict(context.view)
    dimension = view.get("dimension_field")
    metrics = _view_list(view, "metric_fields")
    hidden = _view_list(view, "hidden_metric_fields")
    dimension_index = _column_index(context, dimension) if isinstance(dimension, str) else None
    if dimension_index is None or len(context.column_types) != len(context.columns):
        return False
    dimension_type = context.column_types[dimension_index].strip()
    dimension_is_temporal = re.match(
        r"^(?:date|time|timestamp)(?:32|64)?(?:$|[\s[(])", dimension_type, re.IGNORECASE
    ) is not None
    dimension_is_categorical = re.match(
        r"^(?:dictionary|string|large_string|bool|boolean)(?:$|[\s<[(])",
        dimension_type,
        re.IGNORECASE,
    ) is not None
    dimension_supported = dimension_is_temporal or dimension_is_categorical
    return (
        dimension_supported
        and 1 <= len(metrics) <= 4
        and len(hidden) <= 4
        and all(field in metrics for field in hidden)
        and any(field not in hidden for field in metrics)
        and all(_is_numeric_field(context, field) for field in metrics)
    )


def _explicit_chart_type_change(
    instruction: str, context: ChartEditContext
) -> ChartEditIntent | None:
    target = _chart_type_only_target(instruction)
    if target is None or not _can_switch_to_line_or_bar(target, context):
        return None
    return ChartEditIntent(
        status="apply",
        patch=ChartEditPatch(
            chart_type=target,
            dimension_field=None,
            metric_fields=None,
            hidden_metric_fields=None,
            bar_orientation=None,
            title=None,
            field_labels=None,
            sort=None,
            format_by_field=None,
            show_data_labels=None,
            show_legend=None,
            color_by_metric=None,
        ),
        current_result_operation=None,
        category_color_operations=[],
        query_proposal=None,
        clarification=None,
    )


def _validate_patch_fields(
    patch: ChartEditPatch | None, context: ChartEditContext
) -> ChartEditClarificationCode | None:
    if patch is None:
        return None
    view = dict(context.view)
    current_metrics = _view_list(view, "metric_fields")
    metrics = patch.metric_fields if patch.metric_fields is not None else current_metrics
    hidden = _effective_hidden_metrics(patch, view)

    referenced_fields: list[str] = []
    if patch.dimension_field is not None:
        referenced_fields.append(patch.dimension_field)
    if patch.metric_fields is not None:
        referenced_fields.extend(patch.metric_fields)
    if patch.hidden_metric_fields is not None:
        referenced_fields.extend(patch.hidden_metric_fields)
    if patch.field_labels is not None:
        referenced_fields.extend(patch.field_labels)
    if patch.format_by_field is not None:
        referenced_fields.extend(patch.format_by_field)
    if patch.color_by_metric is not None:
        referenced_fields.extend(patch.color_by_metric)
    if patch.sort is not None and patch.sort.field is not None:
        referenced_fields.append(patch.sort.field)
    if any(_column_index(context, field) is None for field in referenced_fields):
        return "field_not_in_result"

    if patch.metric_fields is not None and any(
        not _is_numeric_field(context, field) for field in patch.metric_fields
    ):
        return "field_not_in_result"
    if any(field not in metrics for field in hidden):
        return "field_not_in_result"

    dimension = (
        patch.dimension_field
        if patch.dimension_field is not None
        else view.get("dimension_field")
    )
    if patch.sort is not None:
        if patch.sort.mode == "dimension" and patch.sort.field != dimension:
            return "field_not_in_result"
        if patch.sort.mode == "metric" and patch.sort.field not in metrics:
            return "field_not_in_result"
    if patch.format_by_field is not None and any(
        field not in metrics for field in patch.format_by_field
    ):
        return "field_not_in_result"
    if patch.color_by_metric is not None:
        if any(field not in metrics for field in patch.color_by_metric):
            return "field_not_in_result"
        if (patch.chart_type or view.get("chart_type")) == "pie":
            return "chart_type_incompatible"
    if patch.bar_orientation is not None and (patch.chart_type or view.get('chart_type')) != 'bar':
        return 'chart_type_incompatible'
    return None


def _field_mentions(instruction: str, field: str, label: str | None) -> list[tuple[int, int]]:
    text = _normalized(instruction)
    matches: set[tuple[int, int]] = set()
    for candidate in (field, label):
        if not candidate:
            continue
        normalized = _normalized(candidate)
        if re.fullmatch(r"[a-z0-9_]+", normalized):
            pattern = re.compile(
                rf"(?<![a-z0-9_]){re.escape(normalized)}(?![a-z0-9_])"
            )
            matches.update((match.start(), match.end()) for match in pattern.finditer(text))
        elif normalized in text:
            start = 0
            while True:
                start = text.find(normalized, start)
                if start < 0:
                    break
                matches.add((start, start + len(normalized)))
                start += len(normalized)
    return sorted(matches)


def _explicitly_mentions_field(instruction: str, field: str, label: str | None) -> bool:
    return bool(_field_mentions(_operation_text(instruction), field, label))


def _user_selected_top_n_metric(
    instruction: str, field: str, context: ChartEditContext
) -> bool:
    view = dict(context.view)
    selected = _view_list(view, "metric_fields")
    hidden = set(_view_list(view, "hidden_metric_fields"))
    if field not in selected or field in hidden or not _is_numeric_field(context, field):
        return False
    labels = view.get("field_labels")
    named_fields = []
    for candidate in selected:
        if candidate in hidden or not _is_numeric_field(context, candidate):
            continue
        label = labels.get(candidate) if isinstance(labels, dict) else None
        if _explicitly_mentions_field(
            instruction, candidate, label if isinstance(label, str) else None
        ):
            named_fields.append(candidate)
    return len(named_fields) == 1 and named_fields[0] == field


def _validate_category_colors(
    instruction: str, intent: ChartEditIntent, context: ChartEditContext
) -> tuple[ChartEditIntent, ChartEditClarificationCode | None]:
    operations = intent.category_color_operations
    if not operations:
        return intent, None
    view = dict(context.view)
    if (
        not _valid_pie_view(context, view)
        or (intent.patch is not None and intent.patch.chart_type not in (None, "pie"))
    ):
        return intent, "chart_type_incompatible"
    if any(operation.category_label not in instruction for operation in operations):
        return intent, "operation_unsupported"
    colors: dict[str, str] = {}
    for operation in operations:
        previous = colors.get(operation.category_label)
        if previous is not None and previous != operation.color:
            return intent, "conflicting_category_color"
        colors[operation.category_label] = operation.color
    if len(colors) != len(operations):
        deduplicated = []
        seen: set[str] = set()
        for operation in operations:
            if operation.category_label not in seen:
                deduplicated.append(operation)
                seen.add(operation.category_label)
        intent = intent.model_copy(update={"category_color_operations": deduplicated})
    return intent, None


def _validate_top_n(
    instruction: str, intent: ChartEditIntent, context: ChartEditContext
) -> tuple[ChartEditIntent, ChartEditClarificationCode | None]:
    requested = _requested_top_n(instruction)
    operation = intent.current_result_operation
    operation_text = _operation_text(instruction)
    has_current_scope = _CURRENT_RESULT_SCOPE.search(operation_text) is not None

    if requested is not None and _FULL_DATA_SCOPE.search(operation_text):
        if operation is not None:
            return intent, "operation_unsupported"
        return intent, None
    if requested is not None and not has_current_scope:
        return intent, "top_n_scope_required"
    if requested is None and operation is None:
        return intent, None
    if requested is None or not has_current_scope:
        return intent, "operation_unsupported"
    if operation is None:
        return intent, "top_n_metric_required"
    count, direction = requested
    if operation.count != count or operation.direction != direction:
        return intent, "conflicting_sort"

    view = dict(context.view)
    patch = intent.patch
    if patch is not None and patch.chart_type is not None and patch.chart_type != view.get("chart_type"):
        return intent, "chart_type_incompatible"
    if view.get("chart_type") == "line":
        return intent, "chart_type_incompatible"
    if view.get("chart_type") == "pie" and not _valid_pie_view(context, view):
        return intent, "chart_type_incompatible"
    if view.get("chart_type") not in {"bar", "pie"}:
        return intent, "chart_type_incompatible"

    column_index = _column_index(context, operation.field)
    if column_index is None or len(context.column_types) != len(context.columns):
        return intent, "field_not_in_result"
    field_type = context.column_types[column_index].strip()
    if _NUMERIC_TYPE.match(field_type) is None:
        return intent, "top_n_metric_required"
    if not _user_selected_top_n_metric(instruction, operation.field, context):
        return intent, "top_n_metric_required"
    if operation.field not in _effective_metrics(patch, view):
        return intent, "top_n_metric_required"
    if operation.field in _effective_hidden_metrics(patch, view):
        return intent, "top_n_metric_required"

    desired_sort = ChartEditSort(mode="metric", field=operation.field, direction=direction)
    if _SORT.search(operation_text) and (patch is None or patch.sort is None):
        return intent, "conflicting_sort"
    if patch is not None and patch.sort is not None and patch.sort != desired_sort:
        return intent, "conflicting_sort"
    if patch is None:
        fields = {
            "chart_type": None,
            "dimension_field": None,
            "metric_fields": None,
            "hidden_metric_fields": None,
            "bar_orientation": None,
            "title": None,
            "field_labels": None,
            "sort": desired_sort,
            "format_by_field": None,
            "show_data_labels": None,
            "show_legend": None,
            "color_by_metric": None,
        }
        patch = ChartEditPatch(**fields)
    else:
        patch = patch.model_copy(update={"sort": desired_sort})
    return intent.model_copy(update={"patch": patch}), None


def classify_chart_edit(
    instruction: str, intent: ChartEditIntent, context: ChartEditContext
) -> ChartEditIntent:
    """Apply deterministic policy to a typed, untrusted model intent."""
    explicit_chart_type_change = _explicit_chart_type_change(instruction, context)
    if explicit_chart_type_change is not None:
        return explicit_chart_type_change
    if intent.status == "clarify":
        return intent

    expected_query = _query_operation(instruction)
    if intent.status == "query_required":
        if expected_query is None or intent.query_proposal is None:
            return _clarify("operation_unsupported")
        if intent.query_proposal.operation != expected_query:
            return _clarify("operation_unsupported")
    elif expected_query is not None:
        # The user's explicit data operation always takes precedence over a model
        # classification that would apply only a local display edit.
        if expected_query == "full_data_top_n" and intent.current_result_operation is not None:
            return _clarify("operation_unsupported")
        intent = intent.model_copy(
            update={
                "status": "query_required",
                "query_proposal": ChartEditQueryProposal(operation=expected_query),
            }
        )

    if not _validate_patch_units(instruction, intent.patch, context):
        return _clarify("source_unit_required")

    field_error = _validate_patch_fields(intent.patch, context)
    if field_error is not None:
        return _clarify(field_error)

    intent, top_n_error = _validate_top_n(instruction, intent, context)
    if top_n_error is not None:
        return _clarify(top_n_error)

    intent, category_error = _validate_category_colors(instruction, intent, context)
    if category_error is not None:
        return _clarify(category_error)

    if intent.status == "query_required":
        return intent
    return intent


def build_chart_edit_capabilities(context: ChartEditContext) -> dict[str, object]:
    """Display hints derived from bounded context; never an authorization grant."""
    view = dict(context.view)
    numeric_fields = [field for field in context.columns if _is_numeric_field(context, field)]
    dimension_fields = [field for field, kind in zip(context.columns, context.column_types)
        if re.match(r"^(?:date|time|timestamp|dictionary|string|large_string|bool|boolean)(?:32|64)?(?:$|[\s<[(])",
                    kind.strip(), re.IGNORECASE)]
    chart_types = [kind for kind in ('line', 'bar') if _can_switch_to_line_or_bar(kind, context)]
    if _valid_pie_view(context, {**view, 'chart_type': 'pie'}):
        chart_types.append('pie')
    return {
        'numeric_fields': numeric_fields,
        'dimension_fields': dimension_fields,
        'chart_types': chart_types,
        'max_metrics': 4,
        'top_n_scope': 'current_result',
        'top_n_chart_types': ['bar', 'pie'] if not context.truncated else ['bar'],
        'unit_conversion': 'explicit_source_and_target_required',
        'palette': ['blue', 'teal', 'green', 'amber', 'orange', 'red', 'purple', 'slate'],
        'query_operations': ['database_filter', 'full_data_top_n', 'aggregation', 'period_comparison'],
    }


async def interpret_chart_edit(
    *, model: Any, instruction: str, context: ChartEditContext
) -> ChartEditIntent:
    """Interpret a display edit with the caller's already-built runtime model.

    The HTTP caller owns authorization and the runtime lease, and passes
    lease.snapshot.graph.model. No query agent is constructed for this use case.
    """
    from agent.chart_edit_interpreter import ChartEditInterpreter

    intent = await ChartEditInterpreter(model).ainvoke(instruction, context)
    return classify_chart_edit(instruction, intent, context)
