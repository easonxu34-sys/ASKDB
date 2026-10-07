import asyncio
import json
import logging
from types import SimpleNamespace
import pytest

from application.chat import stream_chat_events
from application.personal_memory import PersonalMemoryApplication


def diagnostic_records(caplog):
    return [json.loads(r.getMessage().split('ASKDB_CHAT_DIAG ', 1)[1])
            for r in caplog.records if 'ASKDB_CHAT_DIAG ' in r.getMessage()]


def test_empty_memory_logs_skip_for_both_switch_states(caplog):
    caplog.set_level(logging.INFO)
    async def run():
        for enabled in (True, False):
            store = SimpleNamespace(snapshot=lambda *args: {'enabled': enabled, 'memories': []})
            app = PersonalMemoryApplication(store, None, None)
            state = await app.prepare('owner-secret', 'source', 'thread', 'turn', 'private question')
            await app.recall(state, None, None)
    asyncio.run(run())
    records = diagnostic_records(caplog)
    assert [r['enabled'] for r in records if r['event'] == 'personal_prepare'] == [True, False]
    assert sum(r['event'] == 'personal_recall_skipped' for r in records) == 2
    assert 'private question' not in caplog.text
    assert 'owner-secret' not in caplog.text


def test_gate_logs_fingerprint_and_clarification_without_text(caplog):
    caplog.set_level(logging.INFO)
    class Gate:
        async def ainvoke(self, messages):
            if len(messages) == 2 and 'personal_interpretation' in messages[1].content:
                return SimpleNamespace(content='CLARIFY: 请确认私密金额字段')
            return SimpleNamespace(content='SAFE')
    runtime = SimpleNamespace(query_gate=Gate(), query_context='private business model')
    async def run():
        return [x async for x in stream_chat_events(
            runtime, [{'role': 'user', 'content': '私密问题'}], 'thread', turn_id='turn')]
    events = asyncio.run(run())
    assert any(x[0] == 'token' for x in events)
    records = diagnostic_records(caplog)
    gate_input = next(r for r in records if r['event'] == 'gate_input')
    decision = next(r for r in records if r['event'] == 'gate_result')
    assert len(gate_input['input']['sha256']) == 64
    assert decision['decision'] == 'CLARIFY'
    assert decision['elapsed_ms'] >= 0
    assert not any(r['event'] == 'agent_started' for r in records)
    assert '私密' not in caplog.text
    assert 'private business model' not in caplog.text


def test_empty_memory_switch_has_identical_gate_input(caplog):
    caplog.set_level(logging.INFO)
    class Gate:
        async def ainvoke(self, messages):
            return SimpleNamespace(content='INTERNAL')
    async def run():
        for enabled in (True, False):
            store = SimpleNamespace(snapshot=lambda *args: {'enabled': enabled, 'memories': []})
            app = PersonalMemoryApplication(store, None, None)
            state = await app.prepare('owner', 'source', 'thread', 'turn', 'question')
            state = await app.recall(state, None, None)
            runtime = SimpleNamespace(query_gate=Gate(), query_context='model')
            async for _ in stream_chat_events(runtime, [{'role': 'user', 'content': 'question'}],
                    'thread', turn_id='turn', personal_state=state):
                pass
    asyncio.run(run())
    inputs = [r['input'] for r in diagnostic_records(caplog) if r['event'] == 'gate_input']
    assert len(inputs) == 2 and inputs[0] == inputs[1]


def test_ready_logs_agent_completion(caplog):
    from test_chat import FakeRuntime, chat_model_event
    caplog.set_level(logging.INFO)
    runtime = FakeRuntime([chat_model_event('private answer')], gate_responses=('READY', 'SAFE'))
    async def run():
        return [x async for x in stream_chat_events(runtime,
            [{'role': 'user', 'content': 'question'}], 'thread', turn_id='turn')]
    asyncio.run(run())
    records = diagnostic_records(caplog)
    assert any(r['event'] == 'agent_started' for r in records)
    end = next(r for r in records if r['event'] == 'agent_finished')
    assert end['completed'] and end['has_answer'] and not end['successful_query']
    assert 'private answer' not in caplog.text


def test_gate_error_logs_type_without_exception_content(caplog):
    caplog.set_level(logging.INFO)
    class Gate:
        async def ainvoke(self, messages):
            raise RuntimeError('secret api key')
    async def run():
        async for _ in stream_chat_events(SimpleNamespace(query_gate=Gate(), query_context='model'),
                [{'role': 'user', 'content': 'question'}], 'thread', turn_id='turn'):
            pass
    with pytest.raises(RuntimeError):
        asyncio.run(run())
    error = next(r for r in diagnostic_records(caplog) if r['event'] == 'gate_error')
    assert error['error_type'] == 'RuntimeError'
    assert 'secret api key' not in caplog.text


@pytest.mark.parametrize('reply', [None, 'private clarification'])
def test_sse_forwards_turn_reference_and_logs_personal_short_circuit(caplog, reply):
    from api.chat_stream import ChatStreamContext, stream_chat_response
    caplog.set_level(logging.INFO)
    class Gate:
        async def ainvoke(self, messages):
            return SimpleNamespace(content='INTERNAL')
    released = []
    async def release(lease):
        released.append(lease)
    lease = object()
    context = ChatStreamContext(
        app=SimpleNamespace(state=SimpleNamespace(runtime_manager=SimpleNamespace(release_runtime=release))),
        request=SimpleNamespace(thread_id='thread', turn_id='turn', message=None),
        principal=None, lease=lease, runtime=SimpleNamespace(query_gate=Gate(), query_context='model'),
        source_id='source', runtime_rule_ids=(), business_rule_store=None,
        recalled_document_keys=(), memory_references=(), memory_reference_text='',
        turn_start=None, memory_store=None, chat_messages=[{'role':'user','content':'question'}],
        personal_state={'question':'question', 'reply':reply})
    async def run():
        return [x async for x in stream_chat_response(context)]
    asyncio.run(run())
    records = diagnostic_records(caplog)
    assert released == [lease]
    assert all(r['turn_ref'] == records[0]['turn_ref'] for r in records)
    assert bool([r for r in records if r['event'] == 'gate_input']) == (reply is None)
    assert bool([r for r in records if r['event'] == 'personal_short_circuit']) == (reply is not None)
    assert records[-1]['event'] == 'stream_finished'
    assert records[-1]['lease_released']
    assert 'private clarification' not in caplog.text
