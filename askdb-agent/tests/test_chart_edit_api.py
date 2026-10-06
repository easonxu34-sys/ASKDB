import asyncio
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException, Request, Response
from fastapi.testclient import TestClient

from api.app import create_app
from api.dependencies import require_current_user
from auth_store import AuthStore
from model_settings import ModelSettingsStore
from model_settings import ModelProfileNotFound
from wren_settings import WrenSettingsStore
from domain.auth import Principal
from domain.chart_edit import ChartEditIntent
from domain.conversation_memory import ThreadNotFound
from agent.chart_edit_interpreter import ChartEditInterpretationError


def payload():
    return {'thread_id': 'thread-1', 'model_profile_id': 'profile-2', 'instruction': '改成柱状图',
        'source_result_id': 'result-1', 'view': {'chart_type': 'bar', 'dimension_field': 'region',
        'metric_fields': ['revenue'], 'hidden_metric_fields': [], 'sort': {'mode': 'original'}},
        'columns': ['region', 'revenue'], 'column_types': ['string', 'double'],
        'row_count': 5, 'truncated': False}


def intent(status='clarify'):
    return ChartEditIntent(status=status, patch=None, current_result_operation=None,
        category_color_operations=[], query_proposal={'operation': 'aggregation'} if status == 'query_required' else None,
        clarification={'code': 'operation_unsupported'} if status == 'clarify' else None)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from api.routes import chart_edits
    app = create_app(wren_store=WrenSettingsStore(database_path=tmp_path/'wren.db'),
        model_store=ModelSettingsStore(database_path=tmp_path/'model.db'),
        auth_store=AuthStore(database_path=tmp_path/'auth.db'))
    principal = Principal('user-1', 'member', 'member', False)
    app.dependency_overrides[require_current_user] = lambda: principal
    state = NS(binding=('source-1', 'user-1'), enabled=True, grant=True, missing=False,
        acquisitions=[], releases=[], interpreted=[], loads=[], acquire_error=None,
        interpretation_error=None, change_on_acquire=None)
    model = object()
    lease = NS(snapshot=NS(graph=NS(model=model)))
    def load(thread_id, *, owner_user_id, limit):
        state.loads.append((thread_id, owner_user_id, limit))
        if state.missing:
            raise ThreadNotFound('secret')
        return NS(source_id='source-1')
    app.state.conversation_memory = NS(load_context=load)
    app.state.wren_store = NS(get_thread_binding=lambda thread_id: state.binding,
        get_data_source=lambda source_id: NS(enabled=state.enabled))
    app.state.auth_application = NS(can_access_data_source=lambda user, source_id: state.grant)
    async def acquire(source_id, profile_id):
        state.acquisitions.append((source_id, profile_id))
        if state.acquire_error:
            raise state.acquire_error
        if state.change_on_acquire:
            state.change_on_acquire()
        return lease
    async def release(item):
        assert item is lease
        state.releases.append(item)
    async def interpret(**kwargs):
        state.interpreted.append(kwargs)
        if state.interpretation_error:
            raise state.interpretation_error
        return state.output
    state.output = intent()
    app.state.runtime_manager = NS(acquire_runtime=acquire, release_runtime=release)
    monkeypatch.setattr(chart_edits, 'interpret_chart_edit', interpret)
    return app, TestClient(app), state, model, principal


def test_requires_authentication_and_no_store(setup):
    app, client, state, _, _ = setup
    def unauthorized():
        raise HTTPException(401, detail={'code': 'AUTH_REQUIRED', 'message': '请登录'})
    app.dependency_overrides[require_current_user] = unauthorized
    response = client.post('/v1/chart-edits/interpret', json=payload())
    assert response.status_code == 401
    assert response.headers['cache-control'] == 'no-store'
    assert not state.acquisitions


@pytest.mark.parametrize('extra', ['data_source_id', 'sql', 'rows', 'categories', 'tools', 'history'])
def test_forbidden_request_keys_rejected_before_acquire(setup, extra):
    _, client, state, _, _ = setup
    response = client.post('/v1/chart-edits/interpret', json={**payload(), extra: 'secret'})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'CHART_EDIT_INPUT_INVALID'
    assert 'secret' not in response.text
    assert not state.acquisitions


@pytest.mark.parametrize('change', [
    {'thread_id': ''}, {'source_result_id': ''}, {'instruction': 'x'*2049},
    {'columns': ['x']*101}, {'columns': ['x'*129, 'revenue']},
    {'column_types': ['x'*65, 'double']}, {'column_types': ['double']},
    {'row_count': True}, {'row_count': 1001}, {'truncated': 0},
    {'view': {'title': 'x'*121}}, {'view': {'field_labels': {'revenue': 'x'*81}}},
    {'view': {'rows': []}}, {'view': {'pie_category_colors': {'secret': 'red'}}},
])
def test_invalid_bounds_rejected_before_acquire(setup, change):
    _, client, state, _, _ = setup
    response = client.post('/v1/chart-edits/interpret', json={**payload(), **change})
    assert response.status_code == 422
    assert response.headers['cache-control'] == 'no-store'
    assert not state.acquisitions


def test_body_limit_rejects_before_acquire(setup):
    _, client, state, _, _ = setup
    response = client.post('/v1/chart-edits/interpret', content=b' '*65537,
        headers={'content-type': 'application/json'})
    assert response.status_code == 413
    assert response.json()['detail']['code'] == 'CHART_EDIT_REQUEST_TOO_LARGE'
    assert not state.acquisitions


@pytest.mark.parametrize('case', ['missing', 'unbound', 'foreign', 'revoked', 'disabled'])
def test_thread_source_access_rejected_before_acquire(setup, case):
    _, client, state, _, _ = setup
    if case == 'missing': state.missing = True
    if case == 'unbound': state.binding = None
    if case == 'foreign': state.binding = ('source-1', 'other-user')
    if case == 'revoked': state.grant = False
    if case == 'disabled': state.enabled = False
    response = client.post('/v1/chart-edits/interpret', json=payload())
    assert response.status_code in (404, 503)
    assert not state.acquisitions
    assert not state.interpreted


@pytest.mark.parametrize('status', ['apply', 'query_required', 'clarify'])
def test_selected_profile_leased_model_and_typed_intent(setup, status):
    _, client, state, model, _ = setup
    if status == 'apply':
        from test_chart_edit_interpreter import intent_payload
        state.output = ChartEditIntent.model_validate(intent_payload())
    else: state.output = intent(status)
    response = client.post('/v1/chart-edits/interpret', json=payload())
    assert response.status_code == 200
    assert response.json()['status'] == status
    assert response.headers['cache-control'] == 'no-store'
    assert state.acquisitions == [('source-1', 'profile-2')]
    assert len(state.releases) == 1
    assert state.interpreted[0]['model'] is model
    assert state.interpreted[0]['context'].source_result_id == 'result-1'
    assert state.loads == [('thread-1', 'user-1', 1)] * 2
    # Fake catalog has no bind/create/query/tool methods: accidental use fails.


@pytest.mark.parametrize('change', ['owner', 'source', 'grant', 'disabled'])
def test_access_rechecked_after_acquire_and_released(setup, change):
    _, client, state, _, _ = setup
    def mutate():
        if change == 'owner': state.binding = ('source-1', 'other')
        if change == 'source': state.binding = ('source-2', 'user-1')
        if change == 'grant': state.grant = False
        if change == 'disabled': state.enabled = False
    state.change_on_acquire = mutate
    response = client.post('/v1/chart-edits/interpret', json=payload())
    assert response.status_code in (404, 503)
    assert len(state.releases) == 1
    assert not state.interpreted


@pytest.mark.parametrize(('error', 'code'), [
    (ChartEditInterpretationError('CHART_EDIT_STRUCTURED_OUTPUT_UNSUPPORTED'), 'CHART_EDIT_STRUCTURED_OUTPUT_UNSUPPORTED'),
    (RuntimeError('provider secret'), 'CHART_EDIT_MODEL_FAILED'),
    (TimeoutError('provider secret'), 'CHART_EDIT_TIMEOUT'),
])
def test_failure_safe_and_releases(setup, error, code):
    _, client, state, _, _ = setup
    state.interpretation_error = error
    response = client.post('/v1/chart-edits/interpret', json=payload())
    assert response.status_code in (502, 503, 504)
    assert response.json()['detail']['code'] == code
    assert 'secret' not in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert len(state.releases) == 1


def test_acquire_failure_does_not_release(setup):
    _, client, state, _, _ = setup
    state.acquire_error = RuntimeError('provider secret')
    response = client.post('/v1/chart-edits/interpret', json=payload())
    assert response.status_code == 503
    assert 'secret' not in response.text
    assert not state.releases


def test_deleted_selected_profile_has_specific_safe_error(setup):
    _, client, state, _, _ = setup
    state.acquire_error = ModelProfileNotFound('profile-secret')
    response = client.post('/v1/chart-edits/interpret', json=payload())
    assert response.status_code == 404
    assert response.json()['detail']['code'] == 'MODEL_PROFILE_NOT_FOUND'
    assert 'secret' not in response.text
    assert not state.releases


def test_cancellation_releases_lease(setup):
    from api.routes.chart_edits import interpret
    from api.schemas.chart_edit import ChartEditRequest
    app, _, state, _, principal = setup
    state.interpretation_error = asyncio.CancelledError()
    request = Request({'type': 'http', 'app': app, 'headers': []})
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(interpret(ChartEditRequest.model_validate(payload()), request, Response(), principal))
    assert len(state.releases) == 1


def test_bounded_timeout_releases_lease(setup, monkeypatch):
    from api.routes import chart_edits
    _, client, state, _, _ = setup
    async def blocked(**kwargs):
        await asyncio.Event().wait()
    monkeypatch.setattr(chart_edits, 'interpret_chart_edit', blocked)
    monkeypatch.setattr(chart_edits, 'MODEL_CALL_TIMEOUT_SECONDS', 0.001)
    response = client.post('/v1/chart-edits/interpret', json=payload())
    assert response.status_code == 504
    assert len(state.releases) == 1


def test_release_error_is_safe(setup):
    app, client, _, _, _ = setup
    async def fail_release(lease):
        raise RuntimeError('credential-secret')
    app.state.runtime_manager.release_runtime = fail_release
    response = client.post('/v1/chart-edits/interpret', json=payload())
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'CHART_EDIT_RUNTIME_UNAVAILABLE'
    assert 'secret' not in response.text


def test_repeat_cancellation_during_failed_cleanup_preserves_cancellation(setup, monkeypatch):
    from api.routes import chart_edits
    from api.schemas.chart_edit import ChartEditRequest
    app, _, _, _, principal = setup
    async def scenario():
        interpreting, releasing, finish_release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def blocked(**kwargs):
            interpreting.set()
            await asyncio.Event().wait()
        async def release(lease):
            releasing.set()
            await finish_release.wait()
            raise RuntimeError('credential-secret')
        monkeypatch.setattr(chart_edits, 'interpret_chart_edit', blocked)
        app.state.runtime_manager.release_runtime = release
        request = Request({'type': 'http', 'app': app, 'headers': []})
        task = asyncio.create_task(chart_edits.interpret(
            ChartEditRequest.model_validate(payload()), request, Response(), principal))
        await interpreting.wait()
        task.cancel()
        await releasing.wait()
        task.cancel()
        await asyncio.sleep(0)
        finish_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())


def test_streamed_body_limit_without_content_length(setup):
    _, client, state, _, _ = setup
    response = client.post('/v1/chart-edits/interpret', content=iter([b' '*32768, b' '*32769]),
        headers={'content-type': 'application/json'})
    assert response.status_code == 413
    assert response.json()['detail']['code'] == 'CHART_EDIT_REQUEST_TOO_LARGE'
    assert not state.acquisitions
