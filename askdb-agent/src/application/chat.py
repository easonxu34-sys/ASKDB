from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.presentation import (
    QUERY_GATE_SYSTEM_PROMPT,
    RESPONSE_SAFETY_REVIEW_PROMPT,
)
from application.chart_context import QueryArtifactContext, parse_requested_chart_type
from domain.chart_artifact import ChartRequest
from tools.chart import create_chart_tool


# These are review triggers only. A semantic reviewer decides whether the
# candidate actually describes private implementation or is ordinary content.
_RESPONSE_REVIEW_CANDIDATES = (
    "wren",
    "mcp",
    "langchain",
    "langgraph",
    "fastapi",
    "deepseek",
    "openai",
    "wren_query",
    "wren_dry_plan",
    "wren_dry_run",
    "mysql",
    "postgresql",
    "clickhouse",
    "duckdb",
    "system prompt",
    "internal implementation",
    "internal architecture",
    "tool call",
    "tool trace",
    "backend",
    "server-side",
    "provider",
    "api key",
    "access token",
    "credential",
    "private key",
    "secret",
    "内部实现",
    "系统提示词",
    "内部提示词",
    "内部架构",
    "工具调用",
    "调用轨迹",
    "服务端实现",
    "服务商",
    "访问令牌",
    "凭据",
    "私钥",
    "密码",
)
_REFUSAL_ZH = "这个问题我无法回答，但可以帮你查询数据或生成图表。"
_REFUSAL_EN = "I can't help with that request, but I can help with a data query or chart."
_CLARIFY_ZH = "请补充明确的查询对象、指标或筛选口径，我再继续查询。"
_CLARIFY_EN = "Please clarify the requested entity, metric, or filter before I run the query."
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
    latest_result_id: str | None = None
    chart_rendered_for_latest_result = False
    chart_attempted_for_latest_result = False
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
            if tool_name == "wren_query":
                if _has_executed_sql(output):
                    yield "result", {"output": output}
                    data = output.get("data")
                    latest_result_id = (
                        data.get("result_id")
                        if isinstance(data, Mapping) and isinstance(data.get("result_id"), str)
                        else None
                    )
                    chart_rendered_for_latest_result = False
                    chart_attempted_for_latest_result = False
            elif tool_name == "render_chart" and isinstance(output, Mapping):
                data = output.get("data")
                if isinstance(data, Mapping) and data.get("kind") == "echarts_chart":
                    yield "chart", {"artifact": dict(data)}
                    if data.get("source_result_id") == latest_result_id:
                        chart_rendered_for_latest_result = True
                        chart_attempted_for_latest_result = True
                elif isinstance(data, Mapping) and data.get("kind") == "chart_unavailable":
                    yield "chart", {"unavailable": dict(data)}
                    if latest_result_id:
                        chart_attempted_for_latest_result = True
        if (
            chart_request.should_render
            and latest_result_id
            and not chart_rendered_for_latest_result
            and not chart_attempted_for_latest_result
        ):
            fallback_step_id = "render-chart-fallback"
            yield "progress", {
                "step_id": fallback_step_id,
                "label": "生成图表",
                "status": "running",
            }
            fallback = create_chart_tool(context, chart_request.requested_chart_type).invoke(
                {"result_id": latest_result_id}
            )
            data = fallback.get("data") if isinstance(fallback, Mapping) else None
            if isinstance(data, Mapping) and data.get("kind") == "echarts_chart":
                yield "chart", {"artifact": dict(data)}
                chart_rendered_for_latest_result = True
            elif isinstance(data, Mapping) and data.get("kind") == "chart_unavailable":
                yield "chart", {"unavailable": dict(data)}
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
            and not chart_rendered_for_latest_result
            and any(marker in text for marker in _CHART_CLAIM_MARKERS)
        ):
            text = "本轮没有成功生成可显示的图表；我没有取得有效的本轮查询结果，请重新发送查询和分组维度。"
        yield "token", {"text": text}
    yield "progress", {"step_id": "final-answer", "label": "整理结果", "status": "completed"}


def _progress_tool_label(tool_name: Any) -> str:
    if tool_name == "render_chart":
        return "生成图表"
    return "查询数据"


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


def _visible_conversation(messages: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    conversation: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = str(message.get("content", ""))
        conversation.append({"role": role, "content": content})
    return conversation


def _message_type(message: Any) -> str | None:
    if isinstance(message, Mapping):
        value = message.get("type")
    else:
        value = getattr(message, "type", None)
    return str(value) if value is not None else None


def _message_value(message: Any, name: str) -> Any:
    if isinstance(message, Mapping):
        return message.get(name)
    return getattr(message, name, None)


def _final_assistant_text(output: Any) -> str | None:
    """Extract a completed assistant answer, excluding tool-call messages."""
    if isinstance(output, Mapping):
        messages = output.get("messages")
        if messages is None:
            if _message_type(output) not in {"ai", "assistant", "AIMessage"}:
                nested_output = output.get("output")
                if nested_output is None:
                    return None
                return _final_assistant_text(nested_output)
            messages = (output,)
    elif isinstance(output, Sequence) and not isinstance(output, (str, bytes)):
        messages = output
    else:
        messages = (output,)

    for message in reversed(messages):
        if _message_type(message) not in {"ai", "assistant", "AIMessage"}:
            continue
        if _message_value(message, "tool_calls") or _message_value(
            message, "invalid_tool_calls"
        ):
            continue
        content = _content_text(_message_value(message, "content"))
        if content.strip():
            return content
    return None


def _decode_tool_content(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        text = _content_text(value)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _needs_response_safety_review(value: str) -> bool:
    normalized = value.casefold()
    return any(marker in normalized for marker in _RESPONSE_REVIEW_CANDIDATES)


async def _is_safe_user_facing_text(
    reviewer: Any,
    text: str,
    messages: Sequence[Mapping[str, str]],
    *,
    purpose: str,
) -> bool:
    """Use candidate terms only to invoke semantic review, never to reject by themselves."""
    if not _needs_response_safety_review(text):
        return True
    try:
        review = await reviewer.ainvoke(
            [
                SystemMessage(content=RESPONSE_SAFETY_REVIEW_PROMPT),
                HumanMessage(
                    content=json.dumps(
                        {
                            "purpose": purpose,
                            "conversation": _visible_conversation(messages),
                            "draft_response": text,
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        )
    except Exception:
        return False
    return _content_text(_message_value(review, "content")).strip() == "SAFE"


def _latest_user_text(messages: Sequence[Mapping[str, str]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _clarification_from_gate(content: str, user_text: str) -> str | None:
    normalized = content.strip()
    if normalized == "READY":
        return None

    fallback = _CLARIFY_ZH if _contains_chinese(user_text) else _CLARIFY_EN
    prefix = "CLARIFY:"
    if not normalized.startswith(prefix):
        return fallback

    question = normalized[len(prefix) :].strip()
    if (
        not question
        or "```" in question
        or "select " in question.casefold()
        or " from " in question.casefold()
    ):
        return fallback
    if _contains_chinese(question) != _contains_chinese(user_text):
        return fallback
    return question


def _has_executed_sql(output: Any) -> bool:
    if not isinstance(output, Mapping):
        return False
    data = output.get("data")
    return (
        isinstance(data, Mapping)
        and isinstance(data.get("sql"), str)
        and bool(data["sql"].strip())
    )
