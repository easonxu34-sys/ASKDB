from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from agent.presentation import QUERY_GATE_SYSTEM_PROMPT
from application.chart_context import QueryArtifactContext, parse_requested_chart_type
from application.chat_diagnostics import fingerprint, log_chat_diagnostic
from application.query_decision import decide_with_issues
from domain.turn_interpretation import RuntimeRef, empty_interpretation
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
_MEMORY_RECALL_KINDS = frozenset({"schema", "business_rule", "query_example"})
_MEMORY_RECALL_TITLE_LIMIT = 160
_MEMORY_RECALL_DETAIL_LIMIT = 600
_MEMORY_RECALL_ITEM_LIMIT = 13


def _bounded_memory_text(value: object, *, limit: int) -> tuple[str, bool]:
    if not isinstance(value, str):
        return "", False
    text = value.strip()
    if len(text) <= limit:
        return text, False
    return f"{text[: limit - 1].rstrip()}…", True


def _memory_recall_payload(
    memory_references: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    counts: dict[str, int] = {}
    items: list[dict[str, object]] = []
    for reference in memory_references:
        if not isinstance(reference, Mapping):
            continue
        kind = reference.get("kind")
        if not isinstance(kind, str) or kind not in _MEMORY_RECALL_KINDS:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        if len(items) >= _MEMORY_RECALL_ITEM_LIMIT:
            continue

        title, title_truncated = _bounded_memory_text(
            reference.get("title"), limit=_MEMORY_RECALL_TITLE_LIMIT
        )
        if not title:
            continue
        item: dict[str, object] = {
            "kind": kind,
            "title": title,
        }
        if title_truncated:
            item["title_truncated"] = True

        # Query examples expose only their natural-language question, never SQL.
        if kind in {"schema", "business_rule"}:
            detail, detail_truncated = _bounded_memory_text(
                reference.get("body"), limit=_MEMORY_RECALL_DETAIL_LIMIT
            )
            if detail:
                item["detail"] = detail
            if detail_truncated:
                item["detail_truncated"] = True
        items.append(item)
    return {"counts": counts, "items": items} if counts else None


async def stream_chat_events(
    runtime: Any,
    messages: Sequence[Mapping[str, str]],
    thread_id: str,
    *,
    memory_references: Sequence[Mapping[str, Any]] = (),
    memory_reference_text: str = "",
    personal_state: dict | None = None,
    turn_id: str | None = None,
    runtime_ref: RuntimeRef | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """Run the query agent and expose its completed user-facing answer."""
    latest_user_text = personal_state['question'] if personal_state else _latest_user_text(messages)
    if personal_state:
        messages = [*messages[:-1], {'role':'user','content':latest_user_text}]
    turn = personal_state.get('interpretation') if personal_state else None
    if turn is None:
        if personal_state and personal_state.get('eligible'):
            raise TypeError('eligible memory requires an immutable turn interpretation')
        turn = empty_interpretation(latest_user_text, runtime_ref or RuntimeRef('', '', ''))
    elif turn.question != latest_user_text:
        raise ValueError('turn interpretation question mismatch')
    personal_context = turn.query_context()
    apply_presentation = not bool(personal_state and personal_state.get('presentation_omitted'))
    agent_context = turn.agent_context(include_presentation=apply_presentation)
    personal_chart_type = turn.presentation.chart_type if apply_presentation else None
    query_gate = getattr(runtime, "query_gate", None)
    query_context = getattr(runtime, "query_context", "")
    if query_gate is None:
        raise TypeError("Chat runtime must provide a query gate.")

    yield "progress", {"step_id": "understanding", "label": "理解问题", "status": "running"}
    gate_messages = [
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
                        "personal_interpretation": personal_context,
                    },
                    ensure_ascii=False,
                )
            ),
        ]
    log_chat_diagnostic('gate_input', thread_id, turn_id,
        input=fingerprint([m.content for m in gate_messages]),
        policy=fingerprint(QUERY_GATE_SYSTEM_PROMPT), business_model=fingerprint(query_context),
        conversation=fingerprint(_visible_conversation(messages)), message_count=len(messages),
        question=fingerprint(latest_user_text), personal_context=fingerprint(personal_context),
        recalled_references=fingerprint(list(memory_references)), reference_count=len(memory_references))
    started = time.monotonic()
    try:
        readiness = await query_gate.ainvoke(gate_messages)
    except BaseException as exc:
        log_chat_diagnostic('gate_error', thread_id, turn_id,
            error_type=type(exc).__name__, elapsed_ms=round((time.monotonic()-started)*1000))
        raise
    gate_decision = _content_text(_message_value(readiness, "content")).strip()
    decision_kind = ('READY' if gate_decision == 'READY' else 'INTERNAL'
        if gate_decision == 'INTERNAL' else 'CLARIFY'
        if gate_decision.startswith('CLARIFY:') else 'INVALID')
    log_chat_diagnostic('gate_result', thread_id, turn_id, decision=decision_kind,
        response=fingerprint(gate_decision), elapsed_ms=round((time.monotonic()-started)*1000))
    decision = decide_with_issues(gate_decision, turn)
    log_chat_diagnostic('query_decision', thread_id, turn_id, decision=decision.status,
        reason_codes=[x.code for x in turn.semantics.issues if x.required],
        schema_version=turn.schema_version)
    if decision.status == "INTERNAL":
        yield "progress", {"step_id": "understanding", "label": "理解问题", "status": "completed"}
        text = _REFUSAL_ZH if _contains_chinese(latest_user_text) else _REFUSAL_EN
        yield "token", {"text": text}
        return

    clarification = decision.clarification or _clarification_from_gate(gate_decision, latest_user_text)
    if clarification is not None:
        yield "progress", {"step_id": "understanding", "label": "理解问题", "status": "completed"}
        clarification_is_safe = await _is_safe_user_facing_text(
            query_gate, clarification, messages, purpose="clarification"
        )
        log_chat_diagnostic('clarification_review', thread_id, turn_id,
            safe=clarification_is_safe, used_fallback=not clarification_is_safe or decision_kind == 'INVALID')
        if clarification_is_safe:
            text = clarification
        else:
            text = _CLARIFY_ZH if _contains_chinese(latest_user_text) else _CLARIFY_EN
        yield "token", {"text": text}
        return

    yield "progress", {"step_id": "understanding", "label": "理解问题", "status": "completed"}
    yield "progress", {"step_id": "query-analysis", "label": "分析查询需求", "status": "running"}
    final_answer: str | None = None

    context = QueryArtifactContext(interpretation=turn, runtime_ref=runtime_ref,
        display_units=personal_state.get('display_units', {}) if personal_state else {},
        apply_presentation=apply_presentation)
    chart_request = ChartRequest(
        latest_user_text=latest_user_text,
        requested_chart_type=parse_requested_chart_type(latest_user_text) or personal_chart_type,
        should_render=_chart_requested_for_turn(messages) or bool(personal_chart_type and re.search(r"分析|趋势|对比|比较|analy|trend|compar",latest_user_text,re.I) and not re.search(r"不要.*图|不.*画图|no chart",latest_user_text,re.I)),
    )
    tool_event_state = ToolEventState()
    active_tool_steps: dict[str, str] = {}
    tool_starts = tool_ends = tool_errors = 0
    agent_completed = False
    agent_started = time.monotonic()
    try:
        agent = runtime.create_agent_for_turn(context, chart_request)
        log_chat_diagnostic('agent_started', thread_id, turn_id,
            chart_requested=chart_request.should_render,
            recall_context=fingerprint(memory_reference_text + "\n" + agent_context))
        async for event in agent.astream_events(
            {"messages": _attach_recall_context(messages, memory_reference_text + "\n" + agent_context)},
            config={"configurable": {"thread_id": thread_id}},
            version="v2",
        ):
            event_type = event.get("event")
            tool_name = event.get("name")
            if event_type == "on_tool_start":
                tool_starts += 1
                step_id = str(event.get("run_id") or f"tool-{len(active_tool_steps) + 1}")
                label = _progress_tool_label(tool_name)
                active_tool_steps[step_id] = label
                yield "progress", {"step_id": step_id, "label": label, "status": "running"}
                continue
            if event_type in {"on_tool_end", "on_tool_error"}:
                tool_ends += event_type == 'on_tool_end'
                tool_errors += event_type == 'on_tool_error'
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
        agent_completed = True
    finally:
        log_chat_diagnostic('agent_finished', thread_id, turn_id,
            completed=agent_completed, tool_starts=tool_starts, tool_ends=tool_ends,
            tool_errors=tool_errors, successful_query=bool(tool_event_state.latest_result_id),
            has_answer=bool(final_answer), answer=fingerprint(final_answer or ''),
            elapsed_ms=round((time.monotonic()-agent_started)*1000))
        context.clear()
    if context.successful_analysis:
        descriptors = context.successful_analysis
        yield '_analysis_descriptor', {'steps': [step for x in descriptors for step in x['steps']][:30], 'references': sorted({r for x in descriptors for r in x['references']})[:40], 'question':latest_user_text[:500]}
        if context.applied_constraints:
            filters=turn.semantics.constraints()['filters']
            metrics=turn.semantics.constraints()['metrics']
            summary='；'.join([f"{x['column']} {x['op']} {json.dumps(x['value'],ensure_ascii=False)[:100]}" for x in filters]+[f"{x['aggregation']}({x['column']})" for x in metrics])[:900]
            yield 'personal_memory_usage', {'status':'applied','message':'本轮成功查询已采用个人条件/口径：'+summary,'applied': context.applied_constraints[:30]}

    if context.display_applied:
        yield 'personal_memory_usage', {'status':'applied','message':'已按声明的源单位换算金额展示；原始查询值未修改。'}
    if personal_chart_type and tool_event_state.chart_rendered_for_latest_result:
        yield 'personal_memory_usage', {'status':'applied','message':'本轮成功查询结果已按所选图表配置展示。'}
    yield "progress", {"step_id": "query-analysis", "label": "分析查询需求", "status": "completed"}
    yield "progress", {"step_id": "final-answer", "label": "整理结果", "status": "running"}
    if final_answer:
        answer_is_safe = await _is_safe_user_facing_text(
            query_gate, final_answer, messages, purpose="answer"
        )
        log_chat_diagnostic('answer_review', thread_id, turn_id, safe=answer_is_safe)
        if not answer_is_safe:
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
        if answer_is_safe and memory_reference_text:
            recall_payload = _memory_recall_payload(memory_references)
            if recall_payload is not None:
                yield "memory_recall", recall_payload
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
