from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from api.dependencies import require_current_user
from api.schemas.threads import (
    ThreadCreateInput,
    ThreadDeleteInput,
    ThreadHistoryImportChunkInput,
    ThreadLifecycleInput,
    ThreadMetadataPatch,
    ThreadStatesInput,
)
from domain.auth import Principal
from domain.conversation_memory import (
    ThreadCreateIdempotencyConflict,
    ThreadCursorStale,
    ThreadDeletionConflict,
    ThreadDeletionJournalRequired,
    ThreadDeletionParticipantUnavailable,
    ThreadGrantRevoked,
    ThreadHistoryImportConflict,
    ThreadHistoryImportIncomplete,
    LegacyHistoryImportDescriptor,
    ConversationThread,
    ThreadNotFound,
    ThreadMetadataConflict,
    ThreadStateConflict,
    TurnAlreadyRunning,
    TurnIdempotencyConflict,
    TurnSequenceConflict,
)
from integrations.conversation_store import ConversationMemoryStore
from integrations.deletion_journal import DeletionJournalUnavailable


router = APIRouter()
MAX_THREAD_REQUEST_BYTES = 64 * 1024


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _memory(request: Request) -> ConversationMemoryStore:
    configured = getattr(request.app.state, "conversation_memory", None)
    if configured is not None:
        return configured.store if hasattr(configured, "store") else configured
    raise HTTPException(
        status_code=503,
        detail={"code": "THREAD_MEMORY_DISABLED", "message": "会话记忆服务尚未完成启动恢复。"},
        headers={"Cache-Control": "no-store"},
    )


def _thread_payload(thread: ConversationThread) -> dict[str, Any]:
    return {
        "thread_id": thread.thread_id,
        "title": thread.title,
        "data_source_id": thread.source_id,
        "data_source_name": thread.source_name,
        "is_pinned": thread.is_pinned,
        "archived_at": thread.archived_at,
        "last_user_turn_at": thread.last_user_turn_at,
        "expires_at": thread.expires_at,
        "retention_paused": thread.retention_paused,
        "retention_remaining_seconds": thread.retention_remaining_seconds,
        "metadata_revision": thread.metadata_revision,
        "history_import_pending": thread.history_import_pending,
    }


def _raise_safe(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, (ThreadNotFound, ThreadGrantRevoked, LookupError)):
        raise HTTPException(
            status_code=404,
            detail={"code": "THREAD_NOT_FOUND", "message": "找不到此会话或当前账号无权查看。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, ThreadCursorStale):
        raise HTTPException(
            status_code=409,
            detail={"code": "THREAD_CURSOR_STALE", "message": "会话列表已变化，请刷新后重试。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, ThreadMetadataConflict):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "THREAD_METADATA_CONFLICT",
                "message": "会话信息已在其他窗口修改，请刷新后重试。",
                "current": _thread_payload(exc.current),
            },
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, ThreadStateConflict):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "THREAD_STATE_CONFLICT",
                "message": "会话状态已变化，请刷新后重试。",
                "current": _thread_payload(exc.current),
            },
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, ThreadDeletionConflict):
        raise HTTPException(
            status_code=409,
            detail={"code": "THREAD_DELETE_CONFIRMATION_STALE", "message": "会话删除影响已变化，请重新确认。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, ThreadCreateIdempotencyConflict):
        raise HTTPException(
            status_code=409,
            detail={"code": "THREAD_CREATE_CONFLICT", "message": "会话创建请求内容已变化，请刷新后重试。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, ThreadHistoryImportConflict):
        raise HTTPException(
            status_code=409,
            detail={"code": "THREAD_HISTORY_IMPORT_CONFLICT", "message": "旧会话导入状态已变化，请重新开始导入。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, ThreadHistoryImportIncomplete):
        raise HTTPException(
            status_code=409,
            detail={"code": "THREAD_HISTORY_IMPORT_INCOMPLETE", "message": "旧会话仍在导入，请稍后重试。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, (ThreadDeletionJournalRequired, ThreadDeletionParticipantUnavailable, DeletionJournalUnavailable)):
        raise HTTPException(
            status_code=503,
            detail={"code": "THREAD_MEMORY_UNAVAILABLE", "message": "会话记忆服务正在恢复或尚未配置。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, (TurnAlreadyRunning, TurnIdempotencyConflict, TurnSequenceConflict)):
        raise HTTPException(
            status_code=409,
            detail={"code": "THREAD_TURN_CONFLICT", "message": "会话状态已变化，请刷新后重试。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=422,
            detail={"code": "THREAD_INPUT_INVALID", "message": "会话请求字段无效。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    raise HTTPException(
        status_code=503,
        detail={"code": "THREAD_STORE_UNAVAILABLE", "message": "会话存储当前不可用。"},
        headers={"Cache-Control": "no-store"},
    ) from None


@router.post("/v1/threads", status_code=201)
async def create_thread(
    body: ThreadCreateInput,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    _no_store(response)
    if len(await request.body()) > MAX_THREAD_REQUEST_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"code": "THREAD_REQUEST_TOO_LARGE", "message": "会话导入请求不能超过 64 KiB。"},
            headers={"Cache-Control": "no-store"},
        )
    try:
        thread = await run_in_threadpool(_memory(request).create_thread,
            owner_user_id=principal.user_id,
            source_id=body.data_source_id,
            creation_key=body.creation_key,
            initial_history=tuple(
                (turn.user_content, turn.assistant_content)
                for turn in body.initial_history
            ),
            history_import=(
                LegacyHistoryImportDescriptor(
                    import_id=body.history_import.import_id,
                    chunk_hashes=tuple(body.history_import.chunk_hashes),
                    turn_count=body.history_import.turn_count,
                    content_bytes=body.history_import.content_bytes,
                )
                if body.history_import is not None
                else None
            ),
        )
        return {
            **_thread_payload(thread),
            "created_at": thread.created_at,
        }
    except Exception as exc:
        _raise_safe(exc)


@router.get("/v1/threads")
async def list_threads(
    request: Request,
    response: Response,
    view: Literal["recent", "archived"],
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=4096),
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    _no_store(response)
    try:
        page = await run_in_threadpool(
            _memory(request).list_thread_page,
            owner_user_id=principal.user_id,
            view=view,
            q=q,
            limit=limit,
            cursor=cursor,
        )
        return {
            "threads": [_thread_payload(thread) for thread in page.threads],
            "next_cursor": page.next_cursor,
            "thread_list_revision": page.snapshot_revision,
        }
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/threads/states")
async def thread_states(
    body: ThreadStatesInput,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    _no_store(response)
    if len(await request.body()) > 8 * 1024:
        raise HTTPException(
            status_code=413,
            detail={"code": "THREAD_REQUEST_TOO_LARGE", "message": "会话状态请求不能超过 8 KiB。"},
            headers={"Cache-Control": "no-store"},
        )
    try:
        states = await run_in_threadpool(
            _memory(request).get_thread_states,
            owner_user_id=principal.user_id,
            thread_ids=tuple(body.thread_ids),
        )
        return {"states": [{"thread_id": item.thread_id, "status": item.status} for item in states]}
    except Exception as exc:
        _raise_safe(exc)


@router.patch("/v1/threads/{thread_id}")
async def update_thread_metadata(
    thread_id: str,
    body: ThreadMetadataPatch,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    _no_store(response)
    if len(await request.body()) > 8 * 1024:
        raise HTTPException(
            status_code=413,
            detail={"code": "THREAD_REQUEST_TOO_LARGE", "message": "会话信息请求不能超过 8 KiB。"},
            headers={"Cache-Control": "no-store"},
        )
    patch: dict[str, Any] = {}
    if "title" in body.model_fields_set:
        patch["title"] = body.title
    if "is_pinned" in body.model_fields_set:
        patch["is_pinned"] = body.is_pinned
    try:
        thread = await run_in_threadpool(
            _memory(request).update_thread_metadata,
            thread_id=thread_id,
            owner_user_id=principal.user_id,
            expected_metadata_revision=body.expected_metadata_revision,
            **patch,
        )
        return _thread_payload(thread)
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/threads/{thread_id}/archive")
async def archive_thread(
    thread_id: str,
    body: ThreadLifecycleInput,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    _no_store(response)
    if len(await request.body()) > 8 * 1024:
        raise HTTPException(
            status_code=413,
            detail={"code": "THREAD_REQUEST_TOO_LARGE", "message": "会话操作请求不能超过 8 KiB。"},
            headers={"Cache-Control": "no-store"},
        )
    try:
        thread = await run_in_threadpool(
            _memory(request).archive_thread,
            thread_id=thread_id,
            owner_user_id=principal.user_id,
            expected_metadata_revision=body.expected_metadata_revision,
        )
        return _thread_payload(thread)
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/threads/{thread_id}/restore")
async def restore_thread(
    thread_id: str,
    body: ThreadLifecycleInput,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    _no_store(response)
    if len(await request.body()) > 8 * 1024:
        raise HTTPException(
            status_code=413,
            detail={"code": "THREAD_REQUEST_TOO_LARGE", "message": "会话操作请求不能超过 8 KiB。"},
            headers={"Cache-Control": "no-store"},
        )
    try:
        thread = await run_in_threadpool(
            _memory(request).restore_thread,
            thread_id=thread_id,
            owner_user_id=principal.user_id,
            expected_metadata_revision=body.expected_metadata_revision,
        )
        return _thread_payload(thread)
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/threads/{thread_id}/history-import")
async def append_history_import_chunk(
    thread_id: str,
    body: ThreadHistoryImportChunkInput,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    _no_store(response)
    if len(await request.body()) > MAX_THREAD_REQUEST_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"code": "THREAD_REQUEST_TOO_LARGE", "message": "会话导入请求不能超过 64 KiB。"},
            headers={"Cache-Control": "no-store"},
        )
    try:
        result = await run_in_threadpool(
            _memory(request).append_legacy_history_import_chunk,
            thread_id=thread_id,
            owner_user_id=principal.user_id,
            import_id=body.import_id,
            chunk_index=body.chunk_index,
            turns=tuple(
                (turn.user_content, turn.assistant_content) for turn in body.turns
            ),
        )
        return {
            "thread_id": thread_id,
            "import_id": body.import_id,
            "received_chunks": result.received_chunks,
            "expected_chunks": result.expected_chunks,
            "completed": result.completed,
            "replayed": result.replayed,
        }
    except Exception as exc:
        _raise_safe(exc)


@router.get("/v1/threads/{thread_id}/history")
async def thread_history(
    thread_id: str,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    _no_store(response)
    try:
        context = await run_in_threadpool(
            _memory(request).load_context,
            thread_id,
            owner_user_id=principal.user_id,
            limit=200,
        )
        return {
            "thread_id": context.thread_id,
            "data_source_id": context.source_id,
            "current_sequence": context.current_sequence,
            "summary": context.summary,
            "turns": [
                {
                    "turn_id": turn.turn_id,
                    "sequence": turn.sequence,
                    "role": turn.role,
                    "content": turn.content,
                    "created_at": turn.created_at,
                }
                for turn in context.turns
            ],
        }
    except Exception as exc:
        _raise_safe(exc)


@router.get("/v1/threads/{thread_id}/deletion-impact")
async def thread_deletion_impact(
    thread_id: str,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    _no_store(response)
    try:
        impact = await run_in_threadpool(
            _memory(request).create_deletion_impact,
            thread_id=thread_id,
            owner_user_id=principal.user_id,
        )
        return {
            "thread_id": impact.thread_id,
            "impact_version": impact.impact_version,
            "expires_at": impact.expires_at,
            "linked_business_rules": {
                "count": impact.rule_count,
                "labels": list(impact.rule_labels),
            },
            "linked_query_example_candidates": {
                "count": impact.query_example_candidate_count,
            },
            "published_query_examples_retained": True,
        }
    except Exception as exc:
        _raise_safe(exc)


@router.delete("/v1/threads/{thread_id}")
async def delete_thread(
    thread_id: str,
    body: ThreadDeleteInput,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    _no_store(response)
    if not body.confirmed:
        raise HTTPException(
            status_code=422,
            detail={"code": "THREAD_DELETE_CONFIRMATION_REQUIRED", "message": "请先确认删除及其关联记忆。"},
            headers={"Cache-Control": "no-store"},
        )
    try:
        operation = await run_in_threadpool(
            _memory(request).delete_thread,
            thread_id=thread_id,
            owner_user_id=principal.user_id,
            impact_version=body.impact_version,
            idempotency_key=body.idempotency_key,
        )
        return {
            "operation_id": operation.operation_id,
            "thread_id": operation.thread_id,
            "status": operation.status,
            "journal_sequence": operation.journal_sequence,
            "thread_content_removed": True,
            "linked_rule_suppression_recorded": operation.status == "suppressed",
            "historical_artifacts_expire_at": operation.historical_content_expires_at,
        }
    except Exception as exc:
        _raise_safe(exc)
