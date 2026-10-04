from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent

from agent.prompts import build_system_prompt
from application.chart_context import QueryArtifactContext
from domain.chart_artifact import ChartRequest
from tools.chart import create_chart_tool
from tools.wren_query import create_guarded_query_tool


@dataclass(frozen=True)
class AgentRuntime:
    """Query agent plus hidden answerability gate."""

    query_gate: Any
    query_context: str
    model: Any
    toolkit: Any
    tools: tuple[Any, ...]
    dialect: str

    def create_agent_for_turn(
        self,
        context: QueryArtifactContext,
        chart_request: ChartRequest,
    ) -> Any:
        tools = [
            create_guarded_query_tool(self.toolkit, context, self.dialect)
            if item.name == "wren_query"
            else item
            for item in self.tools
        ]
        tools.append(create_chart_tool(context, chart_request.requested_chart_type))
        system_prompt = build_system_prompt(self.toolkit, tools)
        if chart_request.should_render:
            system_prompt += (
                "\n\nThis turn requests a chart or a chart revision. Run a fresh successful "
                "wren_query for the requested dimensions and call render_chart with its exact "
                "result_id before answering. Never say a chart was generated unless that tool "
                "returns an echarts_chart artifact."
            )
        return create_agent(
            model=self.model,
            tools=tools,
            system_prompt=system_prompt,
        )


def build_graph(model: Any, toolkit: Any, dialect: str = "mysql"):
    """Build the query agent and its hidden answerability gate."""
    tools = toolkit.get_tools(include_memory_write=False, raise_on_error=True)
    system_prompt = build_system_prompt(toolkit, tools)
    return AgentRuntime(
        query_gate=model,
        query_context=system_prompt,
        model=model,
        toolkit=toolkit,
        tools=tuple(tools),
        dialect=dialect,
    )
