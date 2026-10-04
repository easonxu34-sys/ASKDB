"""User-facing chat text checks and safe clarification handling."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.presentation import RESPONSE_SAFETY_REVIEW_PROMPT
from application.chat_messages import content_text, message_value, visible_conversation


RESPONSE_REVIEW_CANDIDATES = (
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
REFUSAL_ZH = "这个问题我无法回答，但可以帮你查询数据或生成图表。"
REFUSAL_EN = "I can't help with that request, but I can help with a data query or chart."
CLARIFY_ZH = "请补充明确的查询对象、指标或筛选口径，我再继续查询。"
CLARIFY_EN = "Please clarify the requested entity, metric, or filter before I run the query."


def needs_response_safety_review(value: str) -> bool:
    normalized = value.casefold()
    return any(marker in normalized for marker in RESPONSE_REVIEW_CANDIDATES)


async def is_safe_user_facing_text(
    reviewer: Any,
    text: str,
    messages: Sequence[Mapping[str, str]],
    *,
    purpose: str,
) -> bool:
    """Use candidate terms only to invoke semantic review, never to reject by themselves."""
    if not needs_response_safety_review(text):
        return True
    try:
        review = await reviewer.ainvoke(
            [
                SystemMessage(content=RESPONSE_SAFETY_REVIEW_PROMPT),
                HumanMessage(
                    content=json.dumps(
                        {
                            "purpose": purpose,
                            "conversation": visible_conversation(messages),
                            "draft_response": text,
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        )
    except Exception:
        return False
    return content_text(message_value(review, "content")).strip() == "SAFE"


def latest_user_text(messages: Sequence[Mapping[str, str]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def contains_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def clarification_from_gate(content: str, user_text: str) -> str | None:
    normalized = content.strip()
    if normalized == "READY":
        return None

    fallback = CLARIFY_ZH if contains_chinese(user_text) else CLARIFY_EN
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
    if contains_chinese(question) != contains_chinese(user_text):
        return fallback
    return question
