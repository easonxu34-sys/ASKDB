from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from api.dependencies import require_current_user
from domain.auth import Principal
from domain.personal_memory import ConfirmationResponse, MemoryEdit, PersonalMemoryError, SettingsInput

router = APIRouter()


async def invoke(request, response, function, *args):
    response.headers['Cache-Control'] = 'no-store'
    service = getattr(request.app.state, 'personal_memory', None)
    if service is None:
        raise HTTPException(503, detail={'code': 'PERSONAL_MEMORY_UNAVAILABLE', 'message': '个人记忆尚未准备。'})
    try:
        return await run_in_threadpool(getattr(service.store, function), *args)
    except PersonalMemoryError as exc:
        raise HTTPException(exc.status, detail={'code': exc.code, 'message': exc.message}) from None
    except Exception:
        raise HTTPException(503, detail={'code': 'PERSONAL_MEMORY_UNAVAILABLE', 'message': '个人记忆暂不可用，请重试。'}) from None


@router.get('/v1/me/memory-settings')
async def settings(request: Request, response: Response, user: Principal = Depends(require_current_user)):
    return await invoke(request,response,'settings',user.user_id)


@router.patch('/v1/me/memory-settings')
async def update_settings(body: SettingsInput, request: Request, response: Response, user: Principal = Depends(require_current_user)):
    return await invoke(request,response,'set_enabled',user.user_id,body.enabled,body.expected_revision)


@router.get('/v1/me/memories')
async def memories(request: Request, response: Response, cursor: str = Query(default='',max_length=128), limit: int = Query(default=50,ge=1,le=100), user: Principal = Depends(require_current_user)):
    return await invoke(request,response,'list',user.user_id,cursor,limit)


@router.patch('/v1/me/memories/{memory_id}')
async def edit(memory_id: str, body: MemoryEdit, request: Request, response: Response, user: Principal = Depends(require_current_user)):
    if body.source_id and not request.app.state.auth_application.can_access_data_source(user,body.source_id):
        raise HTTPException(404, detail={'code':'PERSONAL_MEMORY_SCOPE_UNAVAILABLE','message':'数据源当前不可用。'})
    from domain.personal_memory import MemoryInput
    memory = MemoryInput.model_validate(body.model_dump(exclude={'expected_version'}))
    response.headers['Cache-Control'] = 'no-store'
    try:
        return await request.app.state.personal_memory.edit(user.user_id,memory_id,memory,body.expected_version)
    except PersonalMemoryError as exc:
        raise HTTPException(exc.status,detail={'code':exc.code,'message':exc.message}) from None
    except Exception:
        raise HTTPException(503,detail={'code':'PERSONAL_MEMORY_PROCESSING_UNAVAILABLE','message':'编辑未保存，请保留草稿后重试。'}) from None


@router.delete('/v1/me/memories/{memory_id}')
async def delete(memory_id: str, request: Request, response: Response, expected_version: int = Query(ge=1), user: Principal = Depends(require_current_user)):
    return await invoke(request,response,'delete',user.user_id,memory_id,expected_version)


@router.post('/v1/me/memories/clear-preview')
async def preview(request: Request, response: Response, user: Principal = Depends(require_current_user)):
    return await invoke(request,response,'clear_preview',user.user_id)


@router.post('/v1/me/memories/clear')
async def clear(body: ConfirmationResponse, request: Request, response: Response, user: Principal = Depends(require_current_user)):
    return await invoke(request,response,'consume',user.user_id,body.request_id,body.choice_id)
