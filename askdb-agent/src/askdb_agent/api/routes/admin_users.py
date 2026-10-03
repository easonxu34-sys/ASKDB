from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from starlette.concurrency import run_in_threadpool

from askdb_agent.api.dependencies import auth_http_exception, get_auth_application, require_admin
from askdb_agent.api.schemas.auth import CreateUserInput, UpdateUserInput
from askdb_agent.application.auth import AuthApplication
from askdb_agent.domain.auth import AuthError, Principal
from askdb_agent.wren_settings import WrenSettingsUnavailable


router = APIRouter(prefix="/v1/admin/users", tags=["admin-users"])


def _request_id(request: Request) -> str | None:
    value = request.headers.get("x-request-id", "")
    return value if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value) else None


def _safe_username_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "USERNAME_INVALID", "message": "用户名长度或格式无效。"},
        headers={"Cache-Control": "no-store"},
    )


@router.get("")
async def list_users(
    response: Response,
    application: AuthApplication = Depends(get_auth_application),
    _admin: Principal = Depends(require_admin),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        users = await run_in_threadpool(application.list_users)
    except AuthError as exc:
        raise auth_http_exception(exc) from None
    return {"users": users}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserInput,
    request: Request,
    response: Response,
    application: AuthApplication = Depends(get_auth_application),
    admin: Principal = Depends(require_admin),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        user, temporary_password = await run_in_threadpool(
            application.create_user,
            username=body.username,
            role=body.role,
            actor_user_id=admin.user_id,
            request_id=_request_id(request),
        )
    except ValueError:
        raise _safe_username_error() from None
    except AuthError as exc:
        raise auth_http_exception(exc) from None
    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "is_active": user.is_active,
            "must_change_password": user.must_change_password,
            "data_source_ids": [],
        },
        "temporary_password": temporary_password,
    }


@router.patch("/{user_id}")
async def update_user(
    user_id: str,
    body: UpdateUserInput,
    request: Request,
    response: Response,
    application: AuthApplication = Depends(get_auth_application),
    admin: Principal = Depends(require_admin),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        user = await run_in_threadpool(
            application.update_user,
            user_id,
            username=body.username,
            role=body.role,
            is_active=body.is_active,
            actor_user_id=admin.user_id,
            request_id=_request_id(request),
        )
    except ValueError:
        raise _safe_username_error() from None
    except AuthError as exc:
        raise auth_http_exception(exc) from None
    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "is_active": user.is_active,
            "must_change_password": user.must_change_password,
        }
    }


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: str,
    request: Request,
    response: Response,
    application: AuthApplication = Depends(get_auth_application),
    admin: Principal = Depends(require_admin),
) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    try:
        temporary_password = await run_in_threadpool(
            application.reset_password,
            user_id,
            actor_user_id=admin.user_id,
            request_id=_request_id(request),
        )
    except AuthError as exc:
        raise auth_http_exception(exc) from None
    return {"temporary_password": temporary_password}


@router.put("/{user_id}/data-sources/{source_id}")
async def grant_data_source(
    user_id: str,
    source_id: str,
    request: Request,
    response: Response,
    application: AuthApplication = Depends(get_auth_application),
    admin: Principal = Depends(require_admin),
) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    try:
        await request.app.state.wren_settings.initialize()
        await run_in_threadpool(
            application.grant_data_source,
            user_id,
            source_id,
            actor_user_id=admin.user_id,
            request_id=_request_id(request),
        )
    except WrenSettingsUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "WREN_SETTINGS_UNAVAILABLE", "message": "数据源服务当前不可用。"},
            headers={"Cache-Control": "no-store"},
        ) from None
    except AuthError as exc:
        raise auth_http_exception(exc) from None
    return {"ok": True}


@router.delete("/{user_id}/data-sources/{source_id}")
async def revoke_data_source(
    user_id: str,
    source_id: str,
    request: Request,
    response: Response,
    application: AuthApplication = Depends(get_auth_application),
    admin: Principal = Depends(require_admin),
) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    try:
        await run_in_threadpool(
            application.revoke_data_source,
            user_id,
            source_id,
            actor_user_id=admin.user_id,
            request_id=_request_id(request),
        )
    except AuthError as exc:
        raise auth_http_exception(exc) from None
    return {"ok": True}
