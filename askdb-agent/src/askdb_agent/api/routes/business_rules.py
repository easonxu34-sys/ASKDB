from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from askdb_agent.api.dependencies import require_admin, require_current_user
from askdb_agent.api.schemas.business_rules import (
    BusinessRuleCandidateCreate,
    BusinessRuleCandidateView,
    BusinessRuleClarificationRequest,
    BusinessRuleClarificationResponse,
    BusinessRuleRejectInput,
    BusinessRuleRevokeInput,
    BusinessRuleTransition,
)
from askdb_agent.domain.auth import Principal
from askdb_agent.domain.business_rules import (
    BusinessRuleCandidate,
    BusinessRuleConflict,
    BusinessRuleForbidden,
    BusinessRuleNotFound,
    BusinessRuleQuotaExceeded,
    BusinessRuleStaleSource,
    BusinessRuleValidationError,
)
from askdb_agent.domain.conversation_memory import (
    ThreadDeletionJournalRequired,
    ThreadDeletionParticipantUnavailable,
)
from askdb_agent.integrations.deletion_journal import DeletionJournalUnavailable
from askdb_agent.wren_settings import WrenConfigurationError
from askdb_agent.wren_settings import WrenConfigurationError


router = APIRouter()
_RULE_ID = re.compile(r"^[a-f0-9]{32}$")


def _application(request: Request):
    application = getattr(request.app.state, "business_rule_memory", None)
    if application is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "BUSINESS_RULE_MEMORY_DISABLED", "message": "业务规则记忆服务尚未启用。"},
            headers={"Cache-Control": "no-store"},
        )
    return application


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _view(candidate: BusinessRuleCandidate) -> dict[str, Any]:
    return BusinessRuleCandidateView(
        business_rule_id=candidate.business_rule_id,
        data_source_id=candidate.data_source_id,
        term=candidate.term,
        definition=candidate.definition,
        mdl_references=list(candidate.mdl_references),
        base_wren_revision_id=candidate.base_wren_revision_id,
        base_mdl_digest=candidate.base_mdl_digest,
        content_hash=candidate.content_hash,
        review_status=candidate.review_status.value,
        publication_status=candidate.publication_status.value,
        has_exact_term_conflict=candidate.has_exact_term_conflict,
        clarification_question=candidate.clarification_question,
        submitted_by=candidate.submitted_by,
        reviewed_by=candidate.reviewed_by,
        review_reason_code=candidate.review_reason_code,
        version=candidate.version,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
        expires_at=candidate.expires_at,
    ).model_dump(mode="json")


def _rule_id(value: str) -> str:
    if not _RULE_ID.fullmatch(value):
        raise HTTPException(
            status_code=404,
            detail={"code": "BUSINESS_RULE_NOT_FOUND", "message": "找不到此业务规则候选。"},
            headers={"Cache-Control": "no-store"},
        )
    return value


def _raise_safe(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, WrenConfigurationError):
        code = exc.code
        status_code = 503 if code == "SOURCE_RECOVERY_REQUIRED" else (
            409 if code in {
                "DATA_SOURCE_UNAVAILABLE", "SOURCE_OPERATION_IN_PROGRESS",
                "SOURCE_GENERATION_STALE", "WREN_DRAFT_PENDING",
                "WREN_DRAFT_STALE", "WREN_RULE_REMOVAL_PENDING",
                "WREN_SUPPRESSED_RULE_RESTORE",
            } else 422
        )
        raise HTTPException(
            status_code=status_code,
            detail={"code": code, "message": str(exc)},
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, WrenConfigurationError):
        code = exc.code
        status_code = 503 if code == "SOURCE_RECOVERY_REQUIRED" else (
            409 if code in {
                "DATA_SOURCE_UNAVAILABLE", "SOURCE_OPERATION_IN_PROGRESS",
                "SOURCE_GENERATION_STALE", "WREN_DRAFT_PENDING",
                "WREN_RULE_REMOVAL_PENDING",
            } else 422
        )
        raise HTTPException(
            status_code=status_code,
            detail={"code": code, "message": str(exc)},
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, BusinessRuleStaleSource):
        raise HTTPException(
            status_code=409,
            detail={"code": "BUSINESS_RULE_SOURCE_STALE", "message": "数据源语义版本已变化，请基于当前版本重新确认。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, BusinessRuleNotFound):
        raise HTTPException(
            status_code=404,
            detail={"code": "BUSINESS_RULE_NOT_FOUND", "message": "找不到此候选，或当前账号无权查看。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, BusinessRuleForbidden):
        raise HTTPException(
            status_code=403,
            detail={"code": "BUSINESS_RULE_FORBIDDEN", "message": "当前账号没有此业务规则操作权限。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, BusinessRuleQuotaExceeded):
        raise HTTPException(
            status_code=429,
            detail={
                "code": exc.code,
                "message": "记忆候选数量已达到限制，请稍后再提交。",
            },
            headers={
                "Cache-Control": "no-store",
                "Retry-After": str(exc.retry_after),
            },
        ) from None
    if isinstance(exc, BusinessRuleConflict):
        raise HTTPException(
            status_code=409,
            detail={"code": "BUSINESS_RULE_STATE_CHANGED", "message": "候选状态已变化，请刷新后重试。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, BusinessRuleValidationError):
        raise HTTPException(
            status_code=422,
            detail={"code": "BUSINESS_RULE_INPUT_INVALID", "message": "业务规则候选字段无效。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    if isinstance(exc, (ThreadDeletionJournalRequired, ThreadDeletionParticipantUnavailable, DeletionJournalUnavailable)):
        raise HTTPException(
            status_code=503,
            detail={"code": "MEMORY_JOURNAL_UNAVAILABLE", "message": "记忆删除保护正在恢复，请稍后重试。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    raise HTTPException(
        status_code=503,
        detail={"code": "BUSINESS_RULE_MEMORY_UNAVAILABLE", "message": "业务规则记忆服务当前不可用。"},
        headers={"Cache-Control": "no-store"},
    ) from None


@router.post("/v1/business-rules/candidates", status_code=201)
async def submit_candidate(
    body: BusinessRuleCandidateCreate,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    _no_store(response)
    try:
        candidate = await run_in_threadpool(
            _application(request).submit,
            principal=principal,
            data_source_id=body.data_source_id,
            thread_id=body.thread_id,
            idempotency_key=body.idempotency_key,
            term=body.term,
            definition=body.definition,
            mdl_references=tuple(body.mdl_references),
        )
        return {"candidate": _view(candidate)}
    except Exception as exc:
        _raise_safe(exc)


@router.get("/v1/business-rules/candidates")
async def list_candidates(
    request: Request,
    response: Response,
    data_source_id: str = Query(min_length=1, max_length=128),
    status: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    _no_store(response)
    try:
        candidates = await run_in_threadpool(
            _application(request).list_candidates,
            principal=principal,
            data_source_id=data_source_id,
            status=status,
            limit=limit,
        )
        return {"candidates": [_view(candidate) for candidate in candidates]}
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/business-rules/candidates/{business_rule_id}/clarification-request")
async def request_clarification(
    business_rule_id: str,
    body: BusinessRuleClarificationRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    _no_store(response)
    try:
        candidate = await run_in_threadpool(
            _application(request).request_clarification,
            _rule_id(business_rule_id),
            principal=principal,
            expected_version=body.expected_version,
            question=body.question,
        )
        return {"candidate": _view(candidate)}
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/business-rules/candidates/{business_rule_id}/clarification-response")
async def respond_to_clarification(
    business_rule_id: str,
    body: BusinessRuleClarificationResponse,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    _no_store(response)
    try:
        candidate = await run_in_threadpool(
            _application(request).respond_to_clarification,
            _rule_id(business_rule_id),
            principal=principal,
            expected_version=body.expected_version,
            term=body.term,
            definition=body.definition,
            mdl_references=tuple(body.mdl_references),
        )
        return {"candidate": _view(candidate)}
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/business-rules/candidates/{business_rule_id}/approve")
async def approve_candidate(
    business_rule_id: str,
    body: BusinessRuleTransition,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    _no_store(response)
    try:
        candidate = await run_in_threadpool(
            _application(request).approve,
            _rule_id(business_rule_id),
            principal=principal,
            expected_version=body.expected_version,
        )
        return {
            "candidate": _view(candidate),
            "message": "审核已通过，等待发布到新的 Wren 版本后才会进入召回。",
        }
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/business-rules/candidates/{business_rule_id}/publish", status_code=202)
async def publish_candidate(
    business_rule_id: str,
    body: BusinessRuleTransition,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    _no_store(response)
    try:
        memory = _application(request)
        result = request.app.state.wren_settings.start_business_rule_publication(
            memory.store,
            business_rule_id=_rule_id(business_rule_id),
            actor_id=principal.user_id,
            expected_version=body.expected_version,
        )
        return {
            "candidate": _view(result["candidate"]),
            "operation": result["operation"],
        }
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/business-rules/candidates/{business_rule_id}/reject")
async def reject_candidate(
    business_rule_id: str,
    body: BusinessRuleRejectInput,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    _no_store(response)
    try:
        candidate = await run_in_threadpool(
            _application(request).reject,
            _rule_id(business_rule_id),
            principal=principal,
            expected_version=body.expected_version,
            reason_code=body.reason_code,
        )
        return {"candidate": _view(candidate)}
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/business-rules/candidates/{business_rule_id}/withdraw")
async def withdraw_candidate(
    business_rule_id: str,
    body: BusinessRuleTransition,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    _no_store(response)
    try:
        candidate = await run_in_threadpool(
            _application(request).withdraw,
            _rule_id(business_rule_id),
            principal=principal,
            expected_version=body.expected_version,
        )
        return {"candidate": _view(candidate)}
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/business-rules/candidates/{business_rule_id}/revoke")
async def revoke_candidate(
    business_rule_id: str,
    body: BusinessRuleRevokeInput,
    request: Request,
    response: Response,
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    _no_store(response)
    configured = getattr(request.app.state, "conversation_memory", None)
    if configured is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "THREAD_MEMORY_DISABLED", "message": "删除保护服务尚未启用。"},
            headers={"Cache-Control": "no-store"},
        )
    conversation_store = configured.store if hasattr(configured, "store") else configured
    try:
        return await run_in_threadpool(
            _application(request).revoke,
            _rule_id(business_rule_id),
            principal=principal,
            idempotency_key=body.idempotency_key,
            conversation_store=conversation_store,
        )
    except Exception as exc:
        _raise_safe(exc)
