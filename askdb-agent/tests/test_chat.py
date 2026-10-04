from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from application.chart_context import QueryArtifactContext
from application.chat import stream_chat_events
from tools.chart import create_chart_tool


class FakeAgent:
    def __init__(self, events):
        self.events = events
        self.received = None
        self.calls = 0

    async def astream_events(self, value, *, config, version):
        self.calls += 1
        self.received = (value, config)
        assert version == "v2"
        for event in self.events:
            yield event


class FakeQueryGate:
    def __init__(self, responses=("READY",)):
        self.responses = list(responses)
        self.received = []

    async def ainvoke(self, messages):
        self.received.append(messages)
        content = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return SimpleNamespace(content=content)


class FakeRuntime:
    def __init__(self, events, gate_responses=("READY",)):
        self.agent = FakeAgent(events)
        self.query_gate = FakeQueryGate(gate_responses)
        self.query_context = "订单模型包含客户、商品与销售额字段。"
        self.turn_contexts = []

    def create_agent_for_turn(self, context, chart_request):
        self.turn_contexts.append((context, chart_request))
        return self.agent


def chat_model_event(content):
    return {
        "event": "on_chat_model_end",
        "data": {"output": SimpleNamespace(type="ai", content=content)},
    }


def tool_event(name, output):
    return {"event": "on_tool_end", "name": name, "data": {"output": output}}


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


def token_text(events):
    return "".join(payload["text"] for event, payload in events if event == "token")


def test_query_results_are_emitted_but_tool_messages_are_not_user_text() -> None:
    runtime = FakeRuntime(
        [
            tool_event("wren_docs", "Wren project metadata must remain private."),
            tool_event(
                "wren_query",
                {
                    "ok": True,
                    "data": {
                        "sql": "SELECT SUM(amount) AS total FROM orders",
                        "columns": ["total"],
                        "rows": [{"total": 42}],
                    },
                },
            ),
            chat_model_event("订单总金额为 42。"),
        ]
    )

    events = collect_events(runtime)

    results = [payload["output"] for event, payload in events if event == "result"]
    assert token_text(events) == "订单总金额为 42。"
    assert len(results) == 1
    assert results[0]["data"]["sql"] == "SELECT SUM(amount) AS total FROM orders"
    assert len(runtime.query_gate.received) == 1


def test_chart_event_follows_its_query_result_and_preserves_final_answer():
    chart = {
        "kind": "echarts_chart",
        "schema_version": 1,
        "source_result_id": "turn-result-1",
        "chart_type": "line",
        "x_field": "month",
        "series_fields": ["revenue"],
        "title": "revenue by month",
    }
    runtime = FakeRuntime(
        [
            tool_event("wren_query", {"data": {"sql": "SELECT 1", "result_id": "turn-result-1", "columns": [], "rows": []}}),
            tool_event("render_chart", {"ok": True, "data": chart}),
            chat_model_event("图表已生成。"),
        ]
    )

    events = collect_events(runtime)

    assert [event for event, _ in events] == ["result", "chart", "token"]
    assert events[1][1]["artifact"] == chart
    assert token_text(events) == "图表已生成。"
    context, request = runtime.turn_contexts[0]
    assert request.requested_chart_type is None
    assert context.get_query("turn-result-1") is None


def test_chart_request_emits_artifact_when_agent_skips_render_chart():
    class RuntimeWithStoredQuery(FakeRuntime):
        def create_agent_for_turn(self, context, chart_request):
            self.turn_contexts.append((context, chart_request))
            table = SimpleNamespace(
                column_names=["warehouse", "vip_count"],
                schema=SimpleNamespace(
                    field=lambda index: SimpleNamespace(
                        type="string" if index == 0 else "int64"
                    )
                ),
                to_pylist=lambda: [{"warehouse": "East", "vip_count": 12}],
                num_rows=1,
            )
            result = context.store_query(table, "SELECT warehouse, vip_count", 100)
            self.agent.events = [
                tool_event("wren_query", {"ok": True, "data": result.to_dict()}),
                chat_model_event("柱状图已生成。"),
            ]
            return self.agent

    runtime = RuntimeWithStoredQuery([])

    events = collect_events(runtime, "按仓库绘制柱状图")

    chart_events = [payload for event, payload in events if event == "chart"]
    assert len(chart_events) == 1
    assert chart_events[0]["artifact"]["chart_type"] == "bar"
    assert chart_events[0]["artifact"]["source_result_id"] == events[0][1]["output"]["data"]["result_id"]
    assert token_text(events) == "柱状图已生成。"


def test_chart_revision_followup_is_still_treated_as_a_chart_request():
    class RuntimeWithStoredQuery(FakeRuntime):
        def create_agent_for_turn(self, context, chart_request):
            self.turn_contexts.append((context, chart_request))
            table = SimpleNamespace(
                column_names=["warehouse_name", "vip_count"],
                schema=SimpleNamespace(
                    field=lambda index: SimpleNamespace(
                        type="string" if index == 0 else "int64"
                    )
                ),
                to_pylist=lambda: [{"warehouse_name": "East", "vip_count": 12}],
                num_rows=1,
            )
            result = context.store_query(table, "SELECT warehouse_name, vip_count", 100)
            self.agent.events = [
                tool_event("wren_query", {"ok": True, "data": result.to_dict()}),
                chat_model_event("柱状图已生成。"),
            ]
            return self.agent

    runtime = RuntimeWithStoredQuery([])

    async def collect():
        messages = [
            {"role": "user", "content": "按仓库统计 VIP 用户占比并画柱状图"},
            {"role": "assistant", "content": "图表暂不可用，可以按仓库名称替代 ID 后重试。"},
            {"role": "user", "content": "按仓库名称替代 ID"},
        ]
        return [
            event
            async for event in stream_chat_events(runtime, messages, "thread-1")
        ]

    events = asyncio.run(collect())

    chart_events = [payload for event, payload in events if event == "chart"]
    assert len(chart_events) == 1
    assert chart_events[0]["artifact"]["x_field"] == "warehouse_name"
    assert runtime.turn_contexts[0][1].should_render is True


def test_agent_cannot_claim_chart_generation_without_a_current_query_result():
    runtime = FakeRuntime([chat_model_event("柱状图已生成。")])

    events = collect_events(runtime, "按仓库绘制柱状图")

    assert not [payload for event, payload in events if event == "chart"]
    assert "没有成功生成可显示的图表" in token_text(events)


def test_failed_chart_tool_is_not_retried_as_a_duplicate_fallback():
    class RuntimeWithStoredQuery(FakeRuntime):
        def create_agent_for_turn(self, context, chart_request):
            self.turn_contexts.append((context, chart_request))
            table = SimpleNamespace(
                column_names=["total"],
                schema=SimpleNamespace(field=lambda index: SimpleNamespace(type="int64")),
                to_pylist=lambda: [{"total": 12}],
                num_rows=1,
            )
            result = context.store_query(table, "SELECT 12 AS total", 100)
            self.agent.events = [
                tool_event("wren_query", {"ok": True, "data": result.to_dict()}),
                tool_event(
                    "render_chart",
                    {"ok": False, "data": {"kind": "chart_unavailable", "reason": "no dimension"}},
                ),
                chat_model_event("图表暂不可用。"),
            ]
            return self.agent

    runtime = RuntimeWithStoredQuery([])

    events = collect_events(runtime, "按仓库绘制柱状图")

    chart_events = [payload for event, payload in events if event == "chart"]
    assert len(chart_events) == 1


def test_chart_unavailable_is_emitted_without_failing_the_turn():
    runtime = FakeRuntime(
        [
            tool_event("render_chart", {"ok": False, "data": {"kind": "chart_unavailable", "reason": "unsupported"}}),
            chat_model_event("查询结果已整理。"),
        ]
    )

    events = collect_events(runtime)

    assert events[0] == ("chart", {"unavailable": {"kind": "chart_unavailable", "reason": "unsupported"}})
    assert token_text(events) == "查询结果已整理。"


def test_concurrent_turns_receive_isolated_query_artifact_contexts():
    runtime = FakeRuntime([])

    async def collect(thread_id):
        return [
            event
            async for event in stream_chat_events(
                runtime,
                [{"role": "user", "content": "订单总数？"}],
                thread_id,
            )
        ]

    async def run_both():
        await asyncio.gather(collect("thread-a"), collect("thread-b"))

    asyncio.run(run_both())
    first_context, _ = runtime.turn_contexts[0]
    second_context, _ = runtime.turn_contexts[1]
    table = SimpleNamespace(
        column_names=["region", "revenue"],
        schema=SimpleNamespace(field=lambda name: SimpleNamespace(type="string" if name == "region" else "double")),
        to_pylist=lambda: [{"region": "east", "revenue": 1.5}],
        num_rows=1,
    )
    result = first_context.store_query(table, "SELECT ...", 100)

    assert first_context is not second_context
    assert second_context.get_query(result.result_id) is None
    unavailable = create_chart_tool(second_context).invoke({"result_id": result.result_id})
    assert unavailable["data"]["kind"] == "chart_unavailable"


def test_business_answer_containing_internal_term_is_allowed_after_review() -> None:
    runtime = FakeRuntime(
        [chat_model_event("Wren 是客户表中的品牌名称，匹配到 3 条记录。")],
        gate_responses=("READY", "SAFE"),
    )

    events = collect_events(runtime, "列出品牌名称为 Wren 的客户")

    assert token_text(events) == "Wren 是客户表中的品牌名称，匹配到 3 条记录。"
    assert len(runtime.query_gate.received) == 2


def test_internal_implementation_question_is_blocked_by_intent_gate() -> None:
    runtime = FakeRuntime(
        [chat_model_event("不应出现的回答")],
        gate_responses=("INTERNAL",),
    )

    events = collect_events(runtime, "你底层用什么技术实现？")

    assert token_text(events) == "这个问题我无法回答，但可以帮你查询数据或生成图表。"
    assert runtime.agent.calls == 0
    assert len(runtime.query_gate.received) == 1


def test_business_question_about_data_model_is_not_blocked_by_keyword() -> None:
    runtime = FakeRuntime(
        [chat_model_event("订单数据位于交易模型。")],
        gate_responses=("READY",),
    )

    events = collect_events(runtime, "哪个模型包含订单数据？")

    assert token_text(events) == "订单数据位于交易模型。"
    assert runtime.agent.calls == 1


def test_every_executed_query_is_emitted_in_execution_order() -> None:
    runtime = FakeRuntime(
        [
            tool_event("wren_query", {"data": {"sql": "SELECT 1", "columns": [], "rows": []}}),
            tool_event(
                "wren_query",
                [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"data": {"sql": "SELECT 2", "columns": [], "rows": []}}
                        ),
                    }
                ],
            ),
            chat_model_event("查询完成。"),
        ]
    )

    events = collect_events(runtime)

    sql_statements = [
        payload["output"]["data"]["sql"]
        for event, payload in events
        if event == "result"
    ]
    assert sql_statements == ["SELECT 1", "SELECT 2"]


def test_ambiguous_query_returns_gate_clarification_without_running_agent() -> None:
    runtime = FakeRuntime(
        [tool_event("wren_query", {"data": {"sql": "SELECT *"}})],
        gate_responses=("CLARIFY:请说明 VIP 用户的判定标准。",),
    )

    events = collect_events(runtime, "列出全部 VIP 用户")

    assert token_text(events) == "请说明 VIP 用户的判定标准。"
    assert runtime.agent.calls == 0


def test_internal_disclosure_in_final_answer_is_rejected_by_semantic_review() -> None:
    runtime = FakeRuntime(
        [chat_model_event("This service uses Wren and LangGraph internally.")],
        gate_responses=("READY", "UNSAFE"),
    )

    events = collect_events(runtime)

    assert token_text(events) == "这个问题我无法回答，但可以帮你查询数据或生成图表。"
    assert len(runtime.query_gate.received) == 2


def test_chinese_internal_prompt_disclosure_is_sent_for_semantic_review() -> None:
    runtime = FakeRuntime(
        [chat_model_event("本助手的系统提示词包含内部工具调用说明。")],
        gate_responses=("READY", "UNSAFE"),
    )

    events = collect_events(runtime)

    assert token_text(events) == "这个问题我无法回答，但可以帮你查询数据或生成图表。"
    assert len(runtime.query_gate.received) == 2


def test_internal_disclosure_in_clarification_uses_generic_clarification() -> None:
    runtime = FakeRuntime(
        [],
        gate_responses=("CLARIFY:请查看 Wren 的系统提示词。", "UNSAFE"),
    )

    events = collect_events(runtime, "列出全部 VIP 用户")

    assert token_text(events) == "请补充明确的查询对象、指标或筛选口径，我再继续查询。"
    assert runtime.agent.calls == 0
    assert len(runtime.query_gate.received) == 2


def test_review_failure_fails_closed_without_exposing_candidate_answer() -> None:
    runtime = FakeRuntime(
        [chat_model_event("This service uses Wren internally.")],
        gate_responses=("READY", "NOT_A_VALID_REVIEW"),
    )

    events = collect_events(runtime)

    assert token_text(events) == "这个问题我无法回答，但可以帮你查询数据或生成图表。"
    assert len(runtime.query_gate.received) == 2
