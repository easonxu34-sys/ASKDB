"""Normalize framework messages and tool payloads for the chat use case."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def visible_conversation(messages: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    """Return only user-visible roles and text for safety review prompts."""
    conversation: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        if role in {"user", "assistant"}:
            conversation.append({"role": role, "content": str(message.get("content", ""))})
    return conversation


def message_type(message: Any) -> str | None:
    value = message.get("type") if isinstance(message, Mapping) else getattr(message, "type", None)
    return str(value) if value is not None else None


def message_value(message: Any, name: str) -> Any:
    return message.get(name) if isinstance(message, Mapping) else getattr(message, name, None)


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def final_assistant_text(output: Any) -> str | None:
    """Extract a completed assistant answer, excluding tool-call messages."""
    if isinstance(output, Mapping):
        messages = output.get("messages")
        if messages is None:
            if message_type(output) not in {"ai", "assistant", "AIMessage"}:
                nested_output = output.get("output")
                return final_assistant_text(nested_output) if nested_output is not None else None
            messages = (output,)
    elif isinstance(output, Sequence) and not isinstance(output, (str, bytes)):
        messages = output
    else:
        messages = (output,)

    for message in reversed(messages):
        if message_type(message) not in {"ai", "assistant", "AIMessage"}:
            continue
        if message_value(message, "tool_calls") or message_value(message, "invalid_tool_calls"):
            continue
        content = content_text(message_value(message, "content"))
        if content.strip():
            return content
    return None


def decode_tool_content(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        text = content_text(value)
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


def has_executed_sql(output: Any) -> bool:
    if not isinstance(output, Mapping):
        return False
    data = output.get("data")
    return (
        isinstance(data, Mapping)
        and isinstance(data.get("sql"), str)
        and bool(data["sql"].strip())
    )
