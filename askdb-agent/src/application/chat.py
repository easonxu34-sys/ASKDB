from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.presentation import QUERY_GATE_SYSTEM_PROMPT
from application.chart_context import QueryArtifactContext, parse_requested_chart_type
from application.chat_messages import (
    content_text as _content_text,
    decode_tool_content as _decode_tool_content,
    final_assistant_text as _final_assistant_text,
    message_type as _message_type,
    message_value as _message_value,
    visible_conversation as _visible_conversation,
)
from application.chat_safety import (
    CLARIFY_EN as _CLARIFY_EN,
    CLARIFY_ZH as _CLARIFY_ZH,
    REFUSAL_EN as _REFUSAL_EN,
    REFUSAL_ZH as _REFUSAL_ZH,
    clarification_from_gate as _clarification_from_gate,
    contains_chinese as _contains_chinese,
    is_safe_user_facing_text as _is_safe_user_facing_text,
    latest_user_text as _latest_user_text,
)
from application.chat_tool_events import (
    ToolEventState,
    present_tool_output,
    progress_label_for_tool as _progress_tool_label,
)
from domain.chart_artifact import ChartRequest
from tools.chart import create_chart_tool


_CHART_CLAIM_MARKERS = ("图表已生成", "柱状图已生成", "折线图已生成", "饼图已生成", "成功生成图表")


async def stream_chat_events(
    runtime: Any,
    messages: Sequence[Mapping[str, str]],
    thread_id: str,
    *,
    memory_references: Sequence[Mapping[str, Any]] = (),
    memory_reference_text: str = "",
) -> AsyncIterator[tuple[str, Any]]:
    """Run the query agent and expose its completed user-facing answer."""
    latest_user_text = _latest_user_text(messages)
    query_gate = getattr(runtime, "query_gate", None)
    query_context = getattr(runtime, "query_context", "")
    if query_gate is None:
        raise TypeError("Chat runtime must provide a query gate.")

    yield "progress", {"step_id": "understanding", "label": "理解问题", "status": "running"}
    readiness = await query_gate.ainvoke(
        [
            SystemMessage(content=QUERY_GATE_SYSTEM_PROMPT),
            HumanMessage(
                content=json.dumps(
                    {
                        "business_model": (
                            {
                                "description": query_context,
                                "recalled_references": list(memory_references),
                            }
                            if memory_references
                            else query_context
                        ),
                        "conversation": _visible_conversation(messages),
                    },
                    ensure_ascii=False,
                )
            ),
        ]
    )
    gate_decision = _content_text(_message_value(readiness, "content")).strip()
    if gate_decision == "INTERNAL":
        yield "progress", {"step_id": "understanding", "label": "理解问题", "status": "completed"}
        text = _REFUSAL_ZH if _contains_chinese(latest_user_text) else _REFUSAL_EN
        yield "token", {"text": text}
        return

    clarification = _clarification_from_gate(gate_decision, latest_user_text)
    if clarification is not None:
        yield "progress", {"step_id": "understanding", "label": "理解问题", "status": "completed"}
        if await _is_safe_user_facing_text(
            query_gate, clarification, messages, purpose="clarification"
        ):
            text = clarification
        else:
            text = _CLARIFY_ZH if _contains_chinese(latest_user_text) else _CLARIFY_EN
        yield "token", {"text": text}
        return

    yield "progress", {"step_id": "understanding", "label": "理解问题", "status": "completed"}
    yield "progress", {"step_id": "query-analysis", "label": "分析查询需求", "status": "running"}
    final_answer: str | None = None

    context = QueryArtifactContext()
    chart_request = ChartRequest(
        latest_user_text=latest_user_text,
        requested_chart_type=parse_requested_chart_type(latest_user_text),
        should_render=_chart_requested_for_turn(messages),
    )
    tool_event_state = ToolEventState()
    active_tool_steps: dict[str, str] = {}
    try:
        agent = runtime.create_agent_for_turn(context, chart_request)
        async for event in agent.astream_events(
            {"messages": _attach_recall_context(messages, memory_reference_text)},
            config={"configurable": {"thread_id": thread_id}},
            version="v2",
        ):
            event_type = event.get("event")
            tool_name = event.get("name")
            if event_type == "on_tool_start":
                step_id = str(event.get("run_id") or f"tool-{len(active_tool_steps) + 1}")
                label = _progress_tool_label(tool_name)
                active_tool_steps[step_id] = label
                yield "progress", {"step_id": step_id, "label": label, "status": "running"}
                continue
            if event_type in {"on_tool_end", "on_tool_error"}:
                step_id = str(event.get("run_id") or "")
                active = active_tool_steps.pop(step_id, None)
                if active is not None:
                    yield "progress", {
                        "step_id": step_id,
                        "label": active,
                        "status": "failed" if event_type == "on_tool_error" else "completed",
                    }
                if event_type == "on_tool_error":
                    continue
            if event.get("event") in {"on_chat_model_end", "on_chain_end"}:
                candidate = _final_assistant_text(event.get("data", {}).get("output"))
                if candidate is not None:
                    final_answer = candidate
                continue
            if event_type != "on_tool_end":
                continue
            raw_output = event.get("data", {}).get("output")
            if _message_type(raw_output) == "tool":
                raw_output = _message_value(raw_output, "content")
            output = _decode_tool_content(raw_output)
            for output_event, payload in present_tool_output(
                tool_name, output, tool_event_state
            ):
                yield output_event, payload
        if (
            chart_request.should_render
            and tool_event_state.latest_result_id
            and not tool_event_state.chart_rendered_for_latest_result
            and not tool_event_state.chart_attempted_for_latest_result
        ):
            fallback_step_id = "render-chart-fallback"
            yield "progress", {
                "step_id": fallback_step_id,
                "label": "生成图表",
                "status": "running",
            }
            fallback = create_chart_tool(context, chart_request.requested_chart_type).invoke(
                {"result_id": tool_event_state.latest_result_id}
            )
            for output_event, payload in present_tool_output(
                "render_chart", fallback, tool_event_state
            ):
                yield output_event, payload
            yield "progress", {
                "step_id": fallback_step_id,
                "label": "生成图表",
                "status": "completed",
            }
    finally:
        context.clear()

    yield "progress", {"step_id": "query-analysis", "label": "分析查询需求", "status": "completed"}
    yield "progress", {"step_id": "final-answer", "label": "整理结果", "status": "running"}
    if final_answer:
        if not await _is_safe_user_facing_text(
            query_gate, final_answer, messages, purpose="answer"
        ):
            text = _REFUSAL_ZH if _contains_chinese(latest_user_text) else _REFUSAL_EN
        else:
            text = final_answer
        if (
            chart_request.should_render
            and not tool_event_state.chart_rendered_for_latest_result
            and any(marker in text for marker in _CHART_CLAIM_MARKERS)
        ):
            text = "本轮没有成功生成可显示的图表；我没有取得有效的本轮查询结果，请重新发送查询和分组维度。"
        yield "token", {"text": text}
    yield "progress", {"step_id": "final-answer", "label": "整理结果", "status": "completed"}


def _chart_requested_for_turn(messages: Sequence[Mapping[str, str]]) -> bool:
    chart_markers = ("图表", "柱状图", "折线图", "饼图", "画图", "绘图", "可视化", "chart", "plot")
    user_messages = [message for message in messages if message.get("role") == "user"]
    if not user_messages:
        return False
    latest = str(user_messages[-1].get("content", "")).casefold()
    if any(marker in latest for marker in chart_markers):
        return True

    revision_markers = ("替代", "改为", "换成", "换为")
    if not any(marker in latest for marker in revision_markers):
        return False
    previous_chart_request = any(
        any(marker in str(message.get("content", "")).casefold() for marker in chart_markers)
        for message in user_messages[:-1]
    )
    latest_assistant = next(
        (str(message.get("content", "")).casefold() for message in reversed(messages)
         if message.get("role") == "assistant"),
        "",
    )
    return previous_chart_request and any(
        marker in latest_assistant for marker in ("图表", "渲染", "柱状图", "折线图", "饼图")
    )


def _attach_recall_context(
    messages: Sequence[Mapping[str, str]], memory_reference_text: str
) -> list[Mapping[str, str]]:
    result = [dict(message) for message in messages]
    if not memory_reference_text:
        return result
    for index in range(len(result) - 1, -1, -1):
        if result[index].get("role") != "user":
            continue
        result[index] = {
            **result[index],
            "content": f"{result[index].get('content', '')}\n\n{memory_reference_text}",
        }
        break
    return result
