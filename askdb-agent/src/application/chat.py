from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.presentation import QUERY_GATE_SYSTEM_PROMPT


_INTERNAL_MARKERS = (
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
)
_INTERNAL_QUESTION_MARKERS = (
    "底层",
    "内部实现",
    "实现细节",
    "技术栈",
    "技术架构",
    "系统架构",
    "开发框架",
    "使用什么技术",
    "用了什么技术",
    "基于什么",
    "怎么搭建",
    "使用什么模型",
    "用了什么模型",
    "哪个模型",
    "什么引擎",
    "什么数据库",
    "数据库类型",
    "数据引擎",
    "工具链",
    "系统提示词",
    "提示词",
    "under the hood",
    "implementation details",
    "how are you built",
    "architecture",
    "tech stack",
    "technology stack",
    "which model",
    "what model",
    "which engine",
    "what engine",
    "which database",
    "what database",
    "which framework",
    "what framework",
    "what tools do you use",
    "system prompt",
    "backend",
    "server-side",
    "provider",
    "internals",
)
_REFUSAL_ZH = "这个问题我无法回答，但可以帮你查询数据或生成图表。"
_REFUSAL_EN = "I can't help with that request, but I can help with a data query or chart."
_CLARIFY_ZH = "请补充明确的查询对象、指标或筛选口径，我再继续查询。"
_CLARIFY_EN = "Please clarify the requested entity, metric, or filter before I run the query."


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
    if _asks_about_internal_implementation(latest_user_text):
        text = _REFUSAL_ZH if _contains_chinese(latest_user_text) else _REFUSAL_EN
        yield "token", {"text": text}
        return

    agent = getattr(runtime, "agent", None)
    query_gate = getattr(runtime, "query_gate", None)
    query_context = getattr(runtime, "query_context", "")
    if agent is None or query_gate is None:
        raise TypeError("Chat runtime must provide an agent and query gate.")

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
    clarification = _clarification_from_gate(
        _content_text(_message_value(readiness, "content")), latest_user_text
    )
    if clarification is not None:
        yield "token", {"text": clarification}
        return

    final_answer: str | None = None

    async for event in agent.astream_events(
        {"messages": _attach_recall_context(messages, memory_reference_text)},
        config={"configurable": {"thread_id": thread_id}},
        version="v2",
    ):
        if event.get("event") in {"on_chat_model_end", "on_chain_end"}:
            candidate = _final_assistant_text(event.get("data", {}).get("output"))
            if candidate is not None:
                final_answer = candidate
            continue
        if event.get("event") != "on_tool_end" or event.get("name") != "wren_query":
            continue
        raw_output = event.get("data", {}).get("output")
        if _message_type(raw_output) == "tool":
            raw_output = _message_value(raw_output, "content")
        output = _decode_tool_content(raw_output)
        if not _has_executed_sql(output):
            continue
        yield "result", {"output": output}

    if final_answer:
        if _contains_internal_marker(final_answer):
            text = _REFUSAL_ZH if _contains_chinese(latest_user_text) else _REFUSAL_EN
        else:
            text = final_answer
        yield "token", {"text": text}


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
        if role == "assistant" and _contains_internal_marker(content):
            content = "[此前回复已省略内部信息]"
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


def _contains_internal_marker(value: str) -> bool:
    normalized = value.casefold()
    return any(marker in normalized for marker in _INTERNAL_MARKERS)


def _asks_about_internal_implementation(value: str) -> bool:
    normalized = value.casefold()
    return any(marker in normalized for marker in _INTERNAL_QUESTION_MARKERS)


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
        or _contains_internal_marker(question)
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
