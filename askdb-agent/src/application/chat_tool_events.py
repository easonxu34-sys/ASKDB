"""Per-turn presentation adapters for internal agent tool lifecycle events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable

from application.chat_messages import has_executed_sql


ChatEvent = tuple[str, Any]
OutputHandler = Callable[[Any, "ToolEventState"], tuple[ChatEvent, ...]]


@dataclass(slots=True)
class ToolEventState:
    """Mutable event state scoped to exactly one streamed chat turn."""

    latest_result_id: str | None = None
    chart_rendered_for_latest_result: bool = False
    chart_attempted_for_latest_result: bool = False


@dataclass(frozen=True, slots=True)
class ToolPresentation:
    progress_label: str
    output_handler: OutputHandler | None = None


def _present_query_result(output: Any, state: ToolEventState) -> tuple[ChatEvent, ...]:
    if not has_executed_sql(output):
        return ()
    data = output.get("data")
    state.latest_result_id = (
        data.get("result_id")
        if isinstance(data, Mapping) and isinstance(data.get("result_id"), str)
        else None
    )
    state.chart_rendered_for_latest_result = False
    state.chart_attempted_for_latest_result = False
    return (("result", {"output": output}),)


def _present_chart_result(output: Any, state: ToolEventState) -> tuple[ChatEvent, ...]:
    if not isinstance(output, Mapping):
        return ()
    data = output.get("data")
    if not isinstance(data, Mapping):
        return ()
    if data.get("kind") == "echarts_chart":
        if data.get("source_result_id") == state.latest_result_id:
            state.chart_rendered_for_latest_result = True
            state.chart_attempted_for_latest_result = True
        return (("chart", {"artifact": dict(data)}),)
    if data.get("kind") == "chart_unavailable":
        if state.latest_result_id:
            state.chart_attempted_for_latest_result = True
        return (("chart", {"unavailable": dict(data)}),)
    return ()


_TOOL_PRESENTATIONS = MappingProxyType(
    {
        "wren_query": ToolPresentation("查询数据", _present_query_result),
        "render_chart": ToolPresentation("生成图表", _present_chart_result),
    }
)
_DEFAULT_PRESENTATION = ToolPresentation("查询数据")


def _presentation_for(tool_name: Any) -> ToolPresentation:
    if not isinstance(tool_name, str):
        return _DEFAULT_PRESENTATION
    return _TOOL_PRESENTATIONS.get(tool_name, _DEFAULT_PRESENTATION)


def progress_label_for_tool(tool_name: Any) -> str:
    return _presentation_for(tool_name).progress_label


def present_tool_output(
    tool_name: Any,
    output: Any,
    state: ToolEventState,
) -> tuple[ChatEvent, ...]:
    presentation = _presentation_for(tool_name)
    if presentation.output_handler is None:
        return ()
    return presentation.output_handler(output, state)
