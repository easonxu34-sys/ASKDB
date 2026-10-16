from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from api.dependencies import require_admin
from api.schemas.effective_permissions import (
    EffectivePermissionExportQuery,
    EffectivePermissionPage,
    EffectivePermissionQuery,
    ExportView,
    PermissionScope,
    PermissionStatus,
    PermissionView,
)
from domain.auth import Principal
from integrations.effective_permission_store import EffectivePermissionStoreError


router = APIRouter(tags=["admin-effective-permissions"])


def _request_id(request: Request) -> str | None:
    value = request.headers.get("x-request-id", "")
    return value if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value) else None


def _validation_error() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "EFFECTIVE_PERMISSION_QUERY_INVALID", "message": "查询参数无效。"},
        headers={"Cache-Control": "no-store"},
    )


def _query_model(**values: object) -> EffectivePermissionQuery:
    try:
        return EffectivePermissionQuery(**values)
    except ValidationError:
        raise _validation_error() from None


def _export_query_model(**values: object) -> EffectivePermissionExportQuery:
    try:
        return EffectivePermissionExportQuery(**values)
    except ValidationError:
        raise _validation_error() from None


async def _call(function: Callable[..., Any], **kwargs: Any) -> Any:
    try:
        return await run_in_threadpool(function, **kwargs)
    except EffectivePermissionStoreError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
            headers={"Cache-Control": "no-store"},
        ) from None
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "EFFECTIVE_PERMISSION_QUERY_INVALID", "message": str(exc)},
            headers={"Cache-Control": "no-store"},
        ) from None
    except OSError:
        raise HTTPException(
            status_code=503,
            detail={"code": "EFFECTIVE_PERMISSION_EXPORT_UNAVAILABLE", "message": "权限导出当前不可用。"},
            headers={"Cache-Control": "no-store"},
        ) from None


async def _file_chunks(path: Path) -> AsyncIterator[bytes]:
    try:
        with path.open("rb") as file:
            while chunk := await asyncio.to_thread(file.read, 64 * 1024):
                yield chunk
    finally:
        path.unlink(missing_ok=True)


@router.get("/v1/admin/effective-permissions", response_model=EffectivePermissionPage)
async def query_effective_permissions(
    request: Request,
    response: Response,
    view: PermissionView = Query(...),
    user_id: str | None = Query(default=None, max_length=128),
    data_source_id: str | None = Query(default=None, max_length=128),
    scope: PermissionScope = Query(default="granted"),
    status: PermissionStatus = Query(default="all"),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=2048),
    _admin: Principal = Depends(require_admin),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    query = _query_model(
        view=view,
        user_id=user_id,
        data_source_id=data_source_id,
        scope=scope,
        status=status,
        limit=limit,
        cursor=cursor,
    )
    service = request.app.state.effective_permissions
    return await _call(service.query, **query.model_dump())


@router.get("/v1/admin/effective-permissions/export")
async def export_effective_permissions(
    request: Request,
    view: ExportView = Query(...),
    user_id: str | None = Query(default=None, max_length=128),
    data_source_id: str | None = Query(default=None, max_length=128),
    scope: PermissionScope = Query(default="granted"),
    status: PermissionStatus = Query(default="all"),
    admin: Principal = Depends(require_admin),
) -> StreamingResponse:
    query = _export_query_model(
        view=view,
        user_id=user_id,
        data_source_id=data_source_id,
        scope=scope,
        status=status,
    )
    service = request.app.state.effective_permissions
    artifact = await _call(
        service.prepare_export,
        **query.model_dump(),
        actor_id=admin.user_id,
        request_id=_request_id(request),
    )
    headers = {
        "Cache-Control": "no-store",
        "Content-Disposition": f'attachment; filename="{artifact.filename}"',
        "Content-Length": str(artifact.byte_count),
        "X-Export-Generated-At": artifact.read_at,
        "X-Export-Row-Count": str(artifact.row_count),
        "X-Export-Sha256": artifact.sha256,
    }
    return StreamingResponse(
        _file_chunks(artifact.path),
        media_type="text/csv; charset=utf-8",
        headers=headers,
        background=BackgroundTask(artifact.path.unlink, missing_ok=True),
    )
