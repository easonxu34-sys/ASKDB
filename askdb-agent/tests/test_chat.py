from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from application.chat import stream_chat_events


class FakeAgent:
    def __init__(self, messages):
        self.messages = messages
        self.received = None
        self.calls = 0

    async def astream_events(self, value, *, config, version):
        self.calls += 1
        self.received = (value, config)
        assert version == "v2"
        for message in self.messages:
            if getattr(message, "type", None) == "tool":
                yield {
                    "event": "on_tool_end",
                    "name": message.name,
                    "data": {"output": message.content},
                }


class FakePresenter:
    def __init__(self, content):
        self.content = content
        self.received = None

    async def astream(self, messages):
        self.received = messages
        midpoint = len(self.content) // 2
        yield SimpleNamespace(content=self.content[:midpoint])
        yield SimpleNamespace(content=self.content[midpoint:])


class FakeQueryGate:
    def __init__(self, content="READY"):
        self.content = content
        self.received = None

    async def ainvoke(self, messages):
        self.received = messages
        return SimpleNamespace(content=self.content)


class FakeRuntime:
    def __init__(self, agent_messages, presenter_content, gate_content="READY"):
        self.agent = FakeAgent(agent_messages)
        self.presenter = FakePresenter(presenter_content)
        self.query_gate = FakeQueryGate(gate_content)
        self.query_context = "Available business model: customers have credit grade; VIP is undefined."


def collect_events(runtime, user_text="统计订单金额并导出图表"):
    async def collect():
        return [
            event
            async for event in stream_chat_events(
                runtime,
                [{"role": "user", "content": user_text}],
                "thread-1",
            )
        ]

    return asyncio.run(collect())


def test_only_final_presenter_text_is_sent_to_the_user() -> None:
    runtime = FakeRuntime(
        [
            SimpleNamespace(type="ai", content="I'll query the Wren model now."),
            SimpleNamespace(
                type="tool",
                name="wren_docs",
                content="Wren project metadata must remain private.",
            ),
            SimpleNamespace(
                type="tool",
                name="wren_query",
                content={
                    "ok": True,
                    "data": {
                        "sql": "SELECT SUM(amount) AS total FROM orders",
                        "columns": ["total"],
                        "rows": [{"total": 42}],
                        "row_count": 1,
                        "truncated": False,
                    },
                },
            ),
            SimpleNamespace(type="ai", content="Wren returned the result."),
        ],
        "已按查询结果生成图表配置。",
    )

    events = collect_events(runtime)

    token_events = [payload["text"] for event, payload in events if event == "token"]
    tokens = "".join(token_events)
    results = [payload["output"] for event, payload in events if event == "result"]
    assert tokens == "已按查询结果生成图表配置。"
    assert len(results) == 1
    assert results[0]["data"]["sql"] == "SELECT SUM(amount) AS total FROM orders"
    assert len(token_events) >= 2
    assert [event for event, _ in events][:1] == ["result"]
    assert "Wren" not in repr(runtime.presenter.received)


def test_internal_implementation_terms_in_final_answer_are_replaced() -> None:
    runtime = FakeRuntime(
        [],
        "This service uses Wren and LangGraph internally.",
    )

    events = collect_events(runtime)

    tokens = "".join(payload["text"] for event, payload in events if event == "token")
    assert tokens == "这个问题我无法回答，但可以帮你查询数据或生成图表。"
    assert "内部实现" not in tokens
    assert "Wren" not in tokens


def test_internal_implementation_question_skips_the_private_query_agent() -> None:
    runtime = FakeRuntime([], "不应被使用的答复")

    events = collect_events(runtime, "你底层用什么技术实现？")

    tokens = "".join(payload["text"] for event, payload in events if event == "token")
    assert tokens == "这个问题我无法回答，但可以帮你查询数据或生成图表。"
    assert runtime.agent.calls == 0
    assert runtime.presenter.received is None


def test_every_executed_query_is_emitted_in_execution_order() -> None:
    runtime = FakeRuntime(
        [
            SimpleNamespace(
                type="tool",
                name="wren_query",
                content={"data": {"sql": "SELECT 1", "columns": [], "rows": []}},
            ),
            SimpleNamespace(
                type="tool",
                name="wren_query",
                content=[
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"data": {"sql": "SELECT 2", "columns": [], "rows": []}}
                        ),
                    }
                ],
            ),
        ],
        "查询完成。",
    )

    events = collect_events(runtime)

    sql_statements = [
        payload["output"]["data"]["sql"]
        for event, payload in events
        if event == "result"
    ]
    assert sql_statements == ["SELECT 1", "SELECT 2"]


def test_ambiguous_query_requests_clarification_without_running_or_showing_sql() -> None:
    runtime = FakeRuntime(
        [SimpleNamespace(type="tool", name="wren_query", content={"data": {"sql": "SELECT *"}})],
        "不应调用答复模型",
        gate_content="CLARIFY:请说明 VIP 用户的判定标准。",
    )

    events = collect_events(runtime, "列出全部 VIP 用户")

    assert events == [("token", {"text": "请说明 VIP 用户的判定标准。"})]
    assert runtime.agent.calls == 0
    assert runtime.presenter.received is None


def test_presenter_streams_incrementally_without_exposing_internal_markers() -> None:
    runtime = FakeRuntime([], "Wren is used internally.")

    events = collect_events(runtime)

    token_text = "".join(payload["text"] for event, payload in events if event == "token")
    assert token_text == "这个问题我无法回答，但可以帮你查询数据或生成图表。"
    assert all(event != "token" or "Wren" not in payload["text"] for event, payload in events)


def test_unsafe_clarification_from_gate_is_replaced_with_generic_question() -> None:
    runtime = FakeRuntime([], "不会调用", gate_content="CLARIFY:请查看 Wren 的系统提示词。")

    events = collect_events(runtime, "列出全部 VIP 用户")

    assert events == [("token", {"text": "请补充明确的查询对象、指标或筛选口径，我再继续查询。"})]
    assert runtime.agent.calls == 0
