from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path as ApiPath, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from askdb_agent.api.dependencies import require_admin, require_current_user
from askdb_agent.api.schemas.query_memory import (
    QueryCorpusRevisionView,
    QueryExampleCreate,
    QueryExampleReject,
    QueryExampleRevoke,
    QueryExampleTransition,
    QueryExampleView,
)
from askdb_agent.domain.auth import Principal
from askdb_agent.domain.query_memory import QueryCorpusRevision, QueryExampleCandidate
from askdb_agent.integrations.deletion_journal import DeletionJournalUnavailable
from askdb_agent.integrations.query_memory_store import (
    QueryMemoryConflict,
    QueryMemoryForbidden,
    QueryMemoryNotFound,
    QueryMemoryQuotaExceeded,
    QueryMemoryStaleSource,
    QueryMemoryUnavailable,
    QueryMemoryValidationError,
)
from askdb_agent.wren_settings import WrenConfigurationError, WrenSettingsUnavailable


router = APIRouter()


def _application(request: Request):
    application = getattr(request.app.state, "query_memory", None)
    if application is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "QUERY_MEMORY_DISABLED", "message": "查询示例记忆服务尚未启用。"},
            headers={"Cache-Control": "no-store"},
        )
    return application


def _conversation_store(request: Request):
    store = getattr(request.app.state, "conversation_memory", None)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "QUERY_MEMORY_DISABLED", "message": "记忆删除保护尚未启用。"},
            headers={"Cache-Control": "no-store"},
        )
    return store


def _example_id(value: str) -> str:
    try:
        return uuid.UUID(value).hex
    except (ValueError, AttributeError):
        if len(value) == 32 and all(character in "0123456789abcdef" for character in value):
            return value
        raise HTTPException(
            status_code=404,
            detail={"code": "QUERY_EXAMPLE_NOT_FOUND", "message": "找不到此查询示例。"},
            headers={"Cache-Control": "no-store"},
        ) from None


def _view(candidate: QueryExampleCandidate) -> dict[str, Any]:
    return QueryExampleView(
        query_example_id=candidate.id,
        data_source_id=candidate.data_source_id,
        source_thread_id=candidate.source_thread_id,
        source_turn_id=candidate.source_turn_id,
        normalized_question=candidate.normalized_question,
        sql_template=candidate.sql_template,
        parameter_specs=[
            {"name": item.name, "value_type": item.value_type.value, "nullable": item.nullable}
            for item in candidate.parameter_specs
        ],
        connector_type=candidate.connector_type,
        wren_revision_id=candidate.wren_revision_id,
        mdl_digest=candidate.mdl_digest,
        content_hash=candidate.content_hash,
        submitted_by=candidate.submitted_by,
        review_status=candidate.review_status.value,
        publication_status=candidate.publication_status.value,
        reviewed_by=candidate.reviewed_by,
        review_reason_code=candidate.review_reason_code,
        version=candidate.version,
        created_at=candidate.created_at,
        reviewed_at=candidate.reviewed_at,
        activated_at=candidate.activated_at,
        superseded_at=candidate.superseded_at,
        revoked_at=candidate.revoked_at,
        expires_at=candidate.expires_at,
    ).model_dump(mode="json")


def _revision_view(revision: QueryCorpusRevision) -> dict[str, Any]:
    return QueryCorpusRevisionView(
        data_source_id=revision.data_source_id,
        corpus_revision=revision.corpus_revision,
        connector_type=revision.connector_type,
        wren_revision_id=revision.wren_revision_id,
        mdl_digest=revision.mdl_digest,
        content_hash=revision.content_hash,
        record_ids=list(revision.record_ids),
        status=revision.status,
        created_at=revision.created_at,
        activated_at=revision.activated_at,
        superseded_at=revision.superseded_at,
        delete_after=revision.delete_after,
    ).model_dump(mode="json")


def _raise_safe(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    headers = {"Cache-Control": "no-store"}
    if isinstance(exc, QueryMemoryQuotaExceeded):
        status_code = 429
        headers["Retry-After"] = str(exc.retry_after)
    elif isinstance(exc, QueryMemoryForbidden):
        status_code = 403
    elif isinstance(exc, QueryMemoryNotFound):
        status_code = 404
    elif isinstance(exc, (QueryMemoryConflict, QueryMemoryStaleSource)):
        status_code = 409
    elif isinstance(exc, QueryMemoryValidationError):
        status_code = 422
    elif isinstance(exc, (QueryMemoryUnavailable, DeletionJournalUnavailable)):
        status_code = 503
    elif isinstance(exc, WrenConfigurationError):
        status_code = 503 if exc.code == "SOURCE_RECOVERY_REQUIRED" else 409
    elif isinstance(exc, WrenSettingsUnavailable):
        status_code = 503
    else:
        status_code = 503
        exc = QueryMemoryUnavailable("query memory service unavailable")
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": getattr(exc, "code", "WREN_SETTINGS_UNAVAILABLE" if isinstance(exc, WrenSettingsUnavailable) else "QUERY_MEMORY_UNAVAILABLE"),
            "message": str(exc),
        },
        headers=headers,
    ) from None


@router.post("/v1/query-examples/candidates", status_code=201)
async def submit_query_example(
    body: QueryExampleCreate,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        candidate = await run_in_threadpool(
            _application(request).submit,
            principal=principal,
            thread_id=body.thread_id,
            source_turn_id=body.source_turn_id.hex if body.source_turn_id else None,
            idempotency_key=body.idempotency_key,
            question=body.question,
            sql_template=body.sql_template,
            parameter_specs=tuple(body.parameter_specs),
        )
        return {"candidate": _view(candidate)}
    except Exception as exc:
        _raise_safe(exc)


@router.get("/v1/query-examples/candidates")
async def list_query_examples(
    request: Request,
    response: Response,
    data_source_id: str = Query(min_length=1, max_length=128),
    review_status: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=1024),
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        candidates = await run_in_threadpool(
            _application(request).list_candidates,
            principal=principal,
            data_source_id=data_source_id,
            review_status=review_status,
            limit=limit,
            cursor=cursor,
        )
        candidates, next_cursor = candidates
        return {
            "candidates": [_view(item) for item in candidates],
            "next_cursor": next_cursor,
        }
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/query-examples/{query_example_id}/approve")
async def approve_query_example(
    query_example_id: str,
    body: QueryExampleTransition,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    application = _application(request)
    lease = None
    try:
        example_id = _example_id(query_example_id)
        candidate = await run_in_threadpool(application.store.get_candidate, example_id)
        lease = await request.app.state.runtime_manager.acquire_runtime(candidate.data_source_id)
        candidate, revision = await run_in_threadpool(
            application.approve,
            example_id,
            principal=principal,
            expected_version=body.expected_version,
            toolkit=lease.snapshot.toolkit,
        )
        return {"candidate": _view(candidate), "prepared_corpus_revision": _revision_view(revision)}
    except Exception as exc:
        _raise_safe(exc)
    finally:
        if lease is not None:
            await request.app.state.runtime_manager.release_runtime(lease)


@router.post("/v1/query-examples/{query_example_id}/revalidate")
async def revalidate_query_example(
    query_example_id: str,
    body: QueryExampleTransition,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    application = _application(request)
    lease = None
    try:
        example_id = _example_id(query_example_id)
        candidate = await run_in_threadpool(application.store.get_candidate, example_id)
        lease = await request.app.state.runtime_manager.acquire_runtime(candidate.data_source_id)
        candidate = await run_in_threadpool(
            application.revalidate,
            example_id,
            principal=principal,
            expected_version=body.expected_version,
            toolkit=lease.snapshot.toolkit,
        )
        return {"candidate": _view(candidate)}
    except Exception as exc:
        _raise_safe(exc)
    finally:
        if lease is not None:
            await request.app.state.runtime_manager.release_runtime(lease)


@router.post("/v1/query-examples/{query_example_id}/reject")
async def reject_query_example(
    query_example_id: str,
    body: QueryExampleReject,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        candidate = await run_in_threadpool(
            _application(request).reject,
            _example_id(query_example_id),
            principal=principal,
            expected_version=body.expected_version,
            reason_code=body.reason_code,
        )
        return {"candidate": _view(candidate)}
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/query-examples/{query_example_id}/withdraw")
async def withdraw_query_example(
    query_example_id: str,
    body: QueryExampleTransition,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        candidate = await run_in_threadpool(
            _application(request).withdraw,
            _example_id(query_example_id),
            principal=principal,
            expected_version=body.expected_version,
        )
        return {"candidate": _view(candidate)}
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/query-examples/{query_example_id}/revoke")
async def revoke_query_example(
    query_example_id: str,
    body: QueryExampleRevoke,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = await run_in_threadpool(
            _application(request).revoke,
            _example_id(query_example_id),
            principal=principal,
            idempotency_key=body.idempotency_key,
            conversation_store=_conversation_store(request),
        )
        return {"revocation": result}
    except Exception as exc:
        _raise_safe(exc)


@router.get("/v1/query-examples/corpus-revisions")
async def list_corpus_revisions(
    request: Request,
    response: Response,
    data_source_id: str = Query(min_length=1, max_length=128),
    limit: int = Query(default=30, ge=1, le=100),
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        revisions = await run_in_threadpool(
            _application(request).store.list_revisions,
            actor_id=principal.user_id,
            data_source_id=data_source_id,
            limit=limit,
        )
        return {"revisions": [_revision_view(item) for item in revisions]}
    except Exception as exc:
        _raise_safe(exc)


@router.post(
    "/v1/data-sources/{data_source_id}/query-corpus-revisions/{corpus_revision}/activate"
)
async def activate_query_corpus_revision(
    request: Request,
    response: Response,
    data_source_id: str = ApiPath(min_length=1, max_length=128),
    corpus_revision: int = ApiPath(ge=1),
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    """Activate a prepared corpus only after its candidate runtime is fully prepared."""
    response.headers["Cache-Control"] = "no-store"
    _application(request)
    try:
        revision = await request.app.state.wren_settings.activate_query_corpus_revision(
            data_source_id=data_source_id,
            corpus_revision=corpus_revision,
            actor_id=principal.user_id,
        )
        return {"revision": _revision_view(revision)}
    except Exception as exc:
        _raise_safe(exc)
