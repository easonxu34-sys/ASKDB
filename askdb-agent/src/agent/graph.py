from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent

from agent.prompts import build_system_prompt
from tools.wren_query import create_guarded_query_tool


@dataclass(frozen=True)
class AgentRuntime:
    """Query agent plus hidden answerability gate."""

    agent: Any
    query_gate: Any
    query_context: str


def build_graph(model: Any, toolkit: Any, dialect: str = "mysql"):
    """Build the query agent and its hidden answerability gate."""
    tools = toolkit.get_tools(include_memory_write=False, raise_on_error=True)
    tools = [
        create_guarded_query_tool(toolkit, dialect) if item.name == "wren_query" else item
        for item in tools
    ]
    system_prompt = build_system_prompt(toolkit, tools)
    return AgentRuntime(
        agent=create_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
        ),
        query_gate=model,
        query_context=system_prompt,
    )
