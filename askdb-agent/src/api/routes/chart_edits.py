from __future__ import annotations

import asyncio
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool
from api.dependencies import require_current_user
from api.schemas.chart_edit import ChartEditRequest
from application.chart_edit import interpret_chart_edit
from agent.chart_edit_interpreter import ChartEditInterpretationError
from domain.auth import Principal
from domain.chart_edit import ChartEditIntent
from domain.conversation_memory import ThreadNotFound, ThreadGrantRevoked
from application.runtime_manager import RuntimeModelNotConfigured
from model_settings import ModelProfileNotFound

router = APIRouter()
logger = logging.getLogger(__name__)
MODEL_CALL_TIMEOUT_SECONDS = 30


def _error(
    code: str,
    status: int,
    message: str,
    *,
    diagnostic: dict[str, object] | None = None,
) -> HTTPException:
    detail: dict[str, object] = {'code': code, 'message': message}
    if diagnostic is not None:
        detail['diagnostic'] = diagnostic
    return HTTPException(status, detail=detail,
        headers={'Cache-Control': 'no-store'})


def _authorized_source(request: Request, thread_id: str, principal: Principal) -> str:
    memory = getattr(request.app.state, 'conversation_memory', None)
    if memory is None:
        raise _error('THREAD_MEMORY_DISABLED', 503, '会话记忆服务当前不可用。')
    store = memory.store if hasattr(memory, 'store') else memory
    catalog = request.app.state.wren_store
    context = store.load_context(thread_id, owner_user_id=principal.user_id, limit=1)
    binding = catalog.get_thread_binding(thread_id)
    if not binding or binding[1] != principal.user_id or binding[0] != context.source_id:
        raise _error('THREAD_NOT_FOUND', 404, '找不到此会话或当前账号无权查看。')
    source = catalog.get_data_source(context.source_id)
    if not source.enabled:
        raise _error('DATA_SOURCE_UNAVAILABLE', 503, '所选数据源当前不可用。')
    if not request.app.state.auth_application.can_access_data_source(principal, context.source_id):
        raise _error('THREAD_NOT_FOUND', 404, '找不到此会话或当前账号无权查看。')
    return context.source_id


@router.post('/v1/chart-edits/interpret', response_model=ChartEditIntent)
async def interpret(
    body: ChartEditRequest, request: Request, response: Response,
    principal: Principal = Depends(require_current_user),
) -> ChartEditIntent:
    response.headers['Cache-Control'] = 'no-store'
    lease = None
    manager = request.app.state.runtime_manager
    try:
        source_id = await run_in_threadpool(_authorized_source, request, body.thread_id, principal)
        try:
            lease = await manager.acquire_runtime(source_id, body.model_profile_id)
        except RuntimeModelNotConfigured:
            raise _error('MODEL_CONFIGURATION_INVALID', 503, '所选模型配置当前不可用。') from None
        except ModelProfileNotFound:
            raise _error('MODEL_PROFILE_NOT_FOUND', 404, '找不到所选模型配置，请重新选择。') from None
        except Exception:
            raise _error('CHART_EDIT_RUNTIME_UNAVAILABLE', 503, '图表编辑服务当前不可用。') from None
        current_source = await run_in_threadpool(_authorized_source, request, body.thread_id, principal)
        if current_source != source_id:
            raise _error('THREAD_NOT_FOUND', 404, '找不到此会话或当前账号无权查看。')
        async with asyncio.timeout(MODEL_CALL_TIMEOUT_SECONDS):
            result = await interpret_chart_edit(model=lease.snapshot.graph.model,
                instruction=body.instruction, context=body.to_context())
        return ChartEditIntent.model_validate(result.model_dump(mode='python'))
    except HTTPException:
        raise
    except (ThreadNotFound, ThreadGrantRevoked, LookupError):
        raise _error('THREAD_NOT_FOUND', 404, '找不到此会话或当前账号无权查看。') from None
    except TimeoutError:
        raise _error('CHART_EDIT_TIMEOUT', 504, '图表编辑请求超时，请重试。') from None
    except ChartEditInterpretationError as exc:
        status = 422 if exc.code == 'CHART_EDIT_INPUT_INVALID' else 502
        logger.warning(
            'chart_edit_failure=%s',
            json.dumps(
                {
                    'event': 'chart_edit_failure',
                    'code': exc.code,
                    'diagnostic': exc.diagnostic,
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(',', ':'),
            ),
        )
        raise _error(
            exc.code,
            status,
            '无法解释此次图表编辑，请调整指令或模型后重试。',
            diagnostic=exc.diagnostic,
        ) from None
    except Exception:
        raise _error('CHART_EDIT_MODEL_FAILED', 502, '无法解释此次图表编辑，请稍后重试。') from None
    finally:
        if lease is not None:
            # Finish resource release even if another cancellation arrives.
            cleanup = asyncio.create_task(manager.release_runtime(lease))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError as cancellation:
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        continue
                    except BaseException:
                        break
                if cleanup.done() and not cleanup.cancelled():
                    cleanup.exception()
                raise cancellation from None
            except Exception:
                raise _error('CHART_EDIT_RUNTIME_UNAVAILABLE', 503, '图表编辑服务当前不可用。') from None
