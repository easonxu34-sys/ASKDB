import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from domain.personal_memory import MemoryInput


def record(kind='display', payload=None, content='用中文说明', **kwargs):
    return {'id':'m1', 'version':1, 'kind':kind, 'payload':payload or {},
            'content':content, 'scenario':'default', 'source_id':None, **kwargs}


def test_kind_specific_write_contract_rejects_semantics_in_display():
    with pytest.raises(ValueError):
        MemoryInput(kind='display', content='中文', payload={'filters':[]})
    with pytest.raises(ValueError):
        MemoryInput(kind='metric_definition', content='金额', payload={'metric':{'column':'Sales.paid','aggregation':'execute'}})
    assert MemoryInput(kind='display', content='中文', payload={'language':'中文'}).payload['language']=='中文'


def test_display_only_recall_does_not_ask_business_model_or_matcher():
    from application.personal_memory import PersonalMemoryApplication
    app=PersonalMemoryApplication(None,None,None)
    async def forbidden(*args):
        raise AssertionError('structured display must not call a semantic matcher')
    app.interpret=forbidden
    state={'question':'每天销售金额趋势','eligible':[record(payload={'language':'中文'})],
           'source_id':'s','thread_id':'t','turn_id':'u','events':[],'reply':None}
    result=asyncio.run(app.recall(state,SimpleNamespace(wren_revision_id='r',mdl_digest='d'),None))
    turn=result['interpretation']
    assert result['reply'] is None
    assert turn.presentation.language=='中文'
    assert not turn.semantics.issues
    assert turn.gate_projection()==replace(turn,presentation=type(turn.presentation)()).gate_projection()


def test_old_display_body_is_compiled_to_presentation_not_query_context():
    from application.personal_memory import PersonalMemoryApplication
    app=PersonalMemoryApplication(None,None,None)
    state={'question':'销售趋势','eligible':[record()], 'source_id':'s',
           'thread_id':'t','turn_id':'u','events':[],'reply':None}
    result=asyncio.run(app.recall(state,SimpleNamespace(wren_revision_id='r',mdl_digest='d'),None))
    assert result['interpretation'].presentation.language=='中文'
    assert '用中文说明' not in result['interpretation'].query_context()


def test_legacy_cross_kind_payload_is_an_issue_not_executed_or_silently_dropped():
    from application.personal_memory import PersonalMemoryApplication
    app=PersonalMemoryApplication(None,None,None)
    state={'question':'销售趋势','eligible':[record(payload={'filters':[{'column':'Sales.region','op':'eq','value':'East'}]})],
           'source_id':'s','thread_id':'t','turn_id':'u','events':[],'reply':None}
    result=asyncio.run(app.recall(state,SimpleNamespace(wren_revision_id='r',mdl_digest='d'),None))
    assert result['reply'] is None
    assert result['interpretation'].semantics.issues[0].code=='INVALID_MEMORY_PAYLOAD'
    assert not result['interpretation'].semantics.filters


def test_gate_presentation_invariance_and_required_issues_override_ready():
    from domain.turn_interpretation import (RuntimeRef, BoundQuerySemantics,
        ResolvedTurnInterpretation, PresentationPreferences, SemanticIssue, MemoryRef)
    from application.query_decision import decide_with_issues
    turn=ResolvedTurnInterpretation('sales',BoundQuerySemantics(RuntimeRef('s','r','d')))
    assert turn.gate_projection()==replace(turn,presentation=PresentationPreferences(language='中文',chart_type='line')).gate_projection()
    issue=SemanticIssue('UNBOUND_FIELD',(MemoryRef('m',1),),'filter')
    unresolved=replace(turn,semantics=replace(turn.semantics,issues=(issue,)))
    assert decide_with_issues('READY',unresolved).status=='CLARIFY'
    assert decide_with_issues('INTERNAL',unresolved).status=='INTERNAL'


def test_chat_uses_frozen_semantics_but_never_raw_memory_context():
    from application.chat import stream_chat_events
    from domain.turn_interpretation import RuntimeRef, empty_interpretation, PresentationPreferences
    from test_chat import FakeRuntime
    calls=[]
    for presentation in (PresentationPreferences(), PresentationPreferences(language='中文')):
        runtime=FakeRuntime([], gate_responses=('CLARIFY: 请确认销售口径', 'SAFE'))
        turn=replace(empty_interpretation('sales',RuntimeRef('s','r','d')),presentation=presentation)
        async def run():
            return [x async for x in stream_chat_events(runtime,[{'role':'user','content':'sales'}],'t',
                personal_state={'question':'sales','interpretation':turn,'context':'RAW MEMORY INSTRUCTION'})]
        asyncio.run(run())
        calls.append([x.content for x in runtime.query_gate.received[0]])
    assert calls[0]==calls[1]
    assert 'RAW MEMORY INSTRUCTION' not in json.dumps(calls)


def test_query_tool_checks_version_and_required_filter_before_any_wren_call():
    from application.chart_context import QueryArtifactContext
    from tools.wren_query import create_guarded_query_tool
    from domain.turn_interpretation import RuntimeRef, BoundQuerySemantics, ResolvedTurnInterpretation
    from domain.memory_payload import FilterSpec
    from domain.personal_memory import PersonalMemoryError
    runtime=RuntimeRef('s','r','d')
    turn=ResolvedTurnInterpretation('sales', BoundQuerySemantics(runtime,
        filters=(FilterSpec(column='Sales.region',op='eq',value='East'),)))
    context=QueryArtifactContext(interpretation=turn,runtime_ref=RuntimeRef('s','new','d'))
    tool=create_guarded_query_tool(SimpleNamespace(),context)
    with pytest.raises(PersonalMemoryError) as error:
        tool.invoke({'sql':"SELECT SUM(paid) FROM Sales WHERE region='East'"})
    assert error.value.code=='PERSONAL_MEMORY_VERSION_CHANGED'
    context=QueryArtifactContext(interpretation=turn,runtime_ref=runtime)
    tool=create_guarded_query_tool(SimpleNamespace(),context)
    with pytest.raises(PersonalMemoryError) as error:
        tool.invoke({'sql':'SELECT SUM(paid) FROM Sales'})
    assert error.value.code=='PERSONAL_MEMORY_FILTER_MISSING'


def semantic_fixture(tmp_path):
    target=tmp_path/'target'
    target.mkdir()
    (target/'mdl.json').write_text(json.dumps({'models':[{'name':'Sales','columns':[
        {'name':'region','properties':{'description':'地区'}},
        {'name':'paid','properties':{'description':'实收金额','unit':'元'}}]}]}))
    return SimpleNamespace(get_revision=lambda *args:SimpleNamespace(project_dir=str(tmp_path)))


def state_for(rows,question='分析销售金额趋势'):
    return {'question':question,'eligible':rows,'source_id':'s','thread_id':'t','turn_id':'u',
            'events':[],'reply':None,'owner':'owner'}


def test_default_filters_bound_conflicts_and_stale_version(tmp_path):
    from application.personal_memory import PersonalMemoryApplication
    app=PersonalMemoryApplication(None,None,None)
    store=semantic_fixture(tmp_path)
    east=record('default_filter',{'filters':[{'column':'Sales.region','op':'eq','value':'East'}]})
    snapshot=SimpleNamespace(wren_revision_id='r',mdl_digest='d')
    state=asyncio.run(app.recall(state_for([east]),snapshot,store))
    assert state['reply'] is None and not state['interpretation'].semantics.issues
    assert state['interpretation'].semantics.filters[0].value=='East'
    west={**east,'id':'m2','payload':{'filters':[{'column':'Sales.region','op':'eq','value':'West'}]}}
    state=asyncio.run(app.recall(state_for([east,west]),snapshot,store))
    assert any(x.code=='CONFLICTING_DEFAULTS' for x in state['interpretation'].semantics.issues)
    stale={**east,'payload':{**east['payload'],'binding':{'source_id':'s','revision':'old','digest':'old'}}}
    state=asyncio.run(app.recall(state_for([stale]),snapshot,store))
    assert state['interpretation'].semantics.issues[0].code=='STALE_BINDING'


def test_unbound_default_uses_mdl_and_rejects_arbitrary_model_reply(tmp_path):
    from application.personal_memory import PersonalMemoryApplication
    app=PersonalMemoryApplication(None,None,None)
    inputs=[]
    async def interpreter(instruction,value):
        inputs.append(value)
        return {'clarification':'请确认全部字段'}
    app.interpret=interpreter
    result=asyncio.run(app.recall(state_for([record('default_filter')]),
        SimpleNamespace(wren_revision_id='r',mdl_digest='d'),semantic_fixture(tmp_path)))
    assert inputs[0]['source_fields']['sales.region']=='Sales.region'
    assert result['reply'] is None
    assert result['interpretation'].semantics.issues[0].code=='UNBOUND_FIELD'


def test_malicious_selector_cannot_omit_required_default(tmp_path):
    from application.personal_memory import PersonalMemoryApplication
    app=PersonalMemoryApplication(None,None,None)
    async def interpreter(instruction,value): return {'use_ids':[], 'overrides':['m1']}
    app.interpret=interpreter
    rows=[record('default_filter',{'filters':[{'column':'Sales.region','op':'eq','value':'East'}]}),
          record('metric_definition',{'metric':{'column':'Sales.paid','aggregation':'sum'}},id='m2')]
    result=asyncio.run(app.recall(state_for(rows),SimpleNamespace(wren_revision_id='r',mdl_digest='d'),semantic_fixture(tmp_path)))
    assert result['interpretation'].semantics.filters[0].value=='East'
    assert any(x.code=='UNKNOWN_SCOPE' for x in result['interpretation'].semantics.issues)


def test_required_issues_stop_agent_even_if_gate_says_ready():
    from test_chat import FakeRuntime
    from application.chat import stream_chat_events
    from domain.turn_interpretation import RuntimeRef, BoundQuerySemantics, ResolvedTurnInterpretation, SemanticIssue
    turn=ResolvedTurnInterpretation('sales',BoundQuerySemantics(RuntimeRef('s','r','d'),
        issues=(SemanticIssue('UNBOUND_FIELD',(),'metric'),)))
    runtime=FakeRuntime([],gate_responses=('READY','SAFE'))
    async def run():
        return [x async for x in stream_chat_events(runtime,[{'role':'user','content':'sales'}],'t',
            personal_state={'question':'sales','interpretation':turn})]
    events=asyncio.run(run())
    assert runtime.agent.calls==0
    assert any(x[0]=='token' for x in events)


def test_current_explicit_filter_and_language_override(tmp_path):
    from application.personal_memory import PersonalMemoryApplication
    app=PersonalMemoryApplication(None,None,None)
    rows=[record('default_filter',{'filters':[{'column':'Sales.region','op':'eq','value':'East'}]}),
          record('display',{'language':'中文'},id='m2')]
    result=asyncio.run(app.recall(state_for(rows,'本次地区改为"West"，用英文说明销售趋势'),
        SimpleNamespace(wren_revision_id='r',mdl_digest='d'),semantic_fixture(tmp_path)))
    assert result['interpretation'].semantics.filters[0].value=='West'
    assert result['interpretation'].presentation.language=='英文'
    assert not result['interpretation'].semantics.issues


def test_optional_presentation_budget_does_not_modify_semantics():
    from domain.turn_interpretation import RuntimeRef, empty_interpretation, PresentationPreferences
    from application.presentation_budget import presentation_fits
    from application.chart_context import QueryArtifactContext
    turn=replace(empty_interpretation('sales',RuntimeRef('s','r','d')),
        presentation=PresentationPreferences(language='中文',display_unit='万元'))
    before=turn.gate_projection()
    counter=SimpleNamespace(count_tokens=lambda text,tokenizer_id:len(text))
    assert not presentation_fits(turn,0,counter,'test')
    assert presentation_fits(turn,10000,counter,'test')
    assert turn.gate_projection()==before
    assert QueryArtifactContext(interpretation=turn,apply_presentation=False).display_unit is None


def test_successful_bound_query_keeps_contract_frozen_and_tracks_execution():
    from dataclasses import FrozenInstanceError
    from domain.turn_interpretation import RuntimeRef, BoundQuerySemantics, ResolvedTurnInterpretation
    from domain.memory_payload import FilterSpec
    from application.chart_context import QueryArtifactContext
    from tools.wren_query import create_guarded_query_tool
    runtime=RuntimeRef('s','r','d')
    turn=ResolvedTurnInterpretation('sales',BoundQuerySemantics(runtime,
        filters=(FilterSpec(column='Sales.region',op='eq',value='East'),)))
    class Table:
        column_names=['amount']; num_rows=1
        schema=SimpleNamespace(field=lambda index:SimpleNamespace(type='int64'))
        def to_pylist(self): return [{'amount':42}]
    calls=[]
    toolkit=SimpleNamespace(
        dry_plan=lambda sql: calls.append('plan') or sql,
        dry_run=lambda sql:calls.append('run'), query=lambda sql,limit:calls.append('query') or Table())
    context=QueryArtifactContext(interpretation=turn,runtime_ref=runtime)
    create_guarded_query_tool(toolkit,context).invoke({'sql':"SELECT SUM(paid) AS amount FROM Sales WHERE region='East'"})
    assert calls==['plan','run','query']
    assert context.applied_constraints and context.successful_analysis
    assert 'applied' not in turn.semantics.constraints()
    with pytest.raises(FrozenInstanceError): turn.question='other'
    with pytest.raises(ValidationError): turn.semantics.filters[0].value='West'


def test_mutated_payload_is_revalidated_before_store_transaction():
    from integrations.personal_memory_store import PersonalMemoryStore
    memory=MemoryInput(kind='display',content='中文',payload={'language':'中文'})
    memory.payload['filters']=[]
    with pytest.raises(ValueError):
        PersonalMemoryStore(None).mutate('owner','action',[memory])


@pytest.mark.parametrize('cancelled',[False,True])
def test_gate_failure_or_cancellation_keeps_sse_lease_cleanup(cancelled):
    from api.chat_stream import ChatStreamContext,stream_chat_response
    class Gate:
        async def ainvoke(self,messages):
            if cancelled: raise asyncio.CancelledError()
            raise RuntimeError('private failure text')
    released=[]
    async def release(lease): released.append(lease)
    lease=SimpleNamespace(snapshot=SimpleNamespace(wren_revision_id='r',mdl_digest='d'))
    context=ChatStreamContext(
        app=SimpleNamespace(state=SimpleNamespace(runtime_manager=SimpleNamespace(release_runtime=release))),
        request=SimpleNamespace(thread_id='t',turn_id='u',message=None),principal=None,
        lease=lease,runtime=SimpleNamespace(query_gate=Gate(),query_context='model'),source_id='s',
        runtime_rule_ids=(),business_rule_store=None,recalled_document_keys=(),memory_references=(),
        memory_reference_text='',turn_start=None,memory_store=None,
        chat_messages=[{'role':'user','content':'sales'}])
    async def run(): return [x async for x in stream_chat_response(context)]
    if cancelled:
        with pytest.raises(asyncio.CancelledError): asyncio.run(run())
    else:
        events=asyncio.run(run())
        assert any('AGENT_ERROR' in x for x in events)
        assert all('private failure text' not in x for x in events)
    assert released==[lease]


def test_display_model_failure_is_optional_and_cannot_return_query_clarification():
    from application.personal_memory import PersonalMemoryApplication
    app=PersonalMemoryApplication(None,None,None)
    async def interpreter(instruction,value): return {'clarification':'确认销售口径'}
    app.interpret=interpreter
    state=asyncio.run(app.recall(state_for([record(content='回答先给结论再分点')]),
        SimpleNamespace(wren_revision_id='r',mdl_digest='d'),None))
    assert state['reply'] is None
    assert not state['interpretation'].semantics.issues
    assert state['interpretation'].gate_projection()=={}
    assert state['events'][0][1]['status']=='not_applied'


def test_legacy_analysis_steps_are_normalized_to_bounded_plan(tmp_path):
    from application.personal_memory import PersonalMemoryApplication
    app=PersonalMemoryApplication(None,None,None)
    async def interpreter(instruction,value):
        if 'candidates' in value: return {'use_ids':['m1']}
        return {'steps':['query','group','sort'], 'references':['Sales.region','Sales.paid']}
    app.interpret=interpreter
    state=asyncio.run(app.recall(state_for([record('analysis_steps',content='分析销售时按地区分组排序')]),
        SimpleNamespace(wren_revision_id='r',mdl_digest='d'),semantic_fixture(tmp_path)))
    assert not state['interpretation'].semantics.issues
    assert state['interpretation'].analysis.steps==('query','group','sort')
    assert '分析销售时按地区分组排序' not in state['interpretation'].agent_context()


def test_boolean_constraint_is_checked_without_dropping_it():
    from application.personal_memory_query import enforce_personal_query
    enforce_personal_query('SELECT paid FROM Sales WHERE active = TRUE','mysql',
        {'filters':[{'column':'Sales.active','op':'eq','value':True}]})


def test_scenario_display_cannot_change_semantic_selection_input(tmp_path):
    from application.personal_memory import PersonalMemoryApplication
    inputs=[]; turns=[]
    metric=record('metric_definition',{'metric':{'column':'Sales.paid','aggregation':'sum'}})
    style=record('display',{'language':'中文'},content='销售趋势用中文',scenario='销售趋势',id='style')
    store=semantic_fixture(tmp_path)
    for rows in ([metric],[metric,style]):
        app=PersonalMemoryApplication(None,None,None)
        async def interpreter(instruction,value):
            if any(x['kind']=='metric_definition' for x in value.get('candidates',[])):
                inputs.append(value)
            return {'use_ids':[x['id'] for x in value.get('candidates',[])]}
        app.interpret=interpreter
        result=asyncio.run(app.recall(state_for(rows),SimpleNamespace(wren_revision_id='r',mdl_digest='d'),store))
        turns.append(result['interpretation'])
    assert inputs[0]==inputs[1]
    assert turns[0].gate_projection()==turns[1].gate_projection()


def test_presentation_only_recipe_has_no_semantic_issue_or_mdl_dependency():
    from application.personal_memory import PersonalMemoryApplication
    app=PersonalMemoryApplication(None,None,None)
    result=asyncio.run(app.recall(state_for([record('analysis_recipe',{'display':{'language':'中文'}})]),
        SimpleNamespace(wren_revision_id='r',mdl_digest='d'),None))
    assert result['interpretation'].presentation.language=='中文'
    assert result['interpretation'].gate_projection()=={}
