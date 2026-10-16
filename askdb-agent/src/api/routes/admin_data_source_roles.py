from __future__ import annotations

import re
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from api.dependencies import require_admin
from api.schemas.data_source_roles import (
    BindingInput, RoleChangeInput, RoleCreateInput, RoleDeleteInput, RolePreviewInput,
)
from domain.auth import Principal
from integrations.data_source_role_store import RoleStoreError


router = APIRouter(tags=["admin-data-source-roles"])


def _service(request: Request):
    return request.app.state.data_source_roles


def _request_id(request: Request) -> str | None:
    value = request.headers.get("x-request-id", "")
    return value if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value) else None


def _raise_safe(exc: Exception) -> None:
    if isinstance(exc, RoleStoreError):
        raise HTTPException(exc.status_code, detail={"code": exc.code, "message": str(exc)},
                            headers={"Cache-Control": "no-store"}) from None
    if isinstance(exc, ValueError):
        raise HTTPException(422, detail={"code": "DATA_SOURCE_ROLE_INVALID", "message": str(exc)},
                            headers={"Cache-Control": "no-store"}) from None
    raise exc


async def _call(function: Callable[..., Any], **kwargs: Any) -> Any:
    try:
        return await run_in_threadpool(function, **kwargs)
    except (RoleStoreError, ValueError) as exc:
        _raise_safe(exc)


@router.get("/v1/admin/data-source-roles")
async def list_roles(request: Request, response: Response,
                     _admin: Principal = Depends(require_admin)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return {"roles": await _call(_service(request).list_roles)}


@router.post("/v1/admin/data-source-roles", status_code=201)
async def create_role(body: RoleCreateInput, request: Request, response: Response,
                      admin: Principal = Depends(require_admin)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    role = await _call(_service(request).create_role, name=body.name,
                       description=body.description, data_source_ids=body.data_source_ids,
                       actor_id=admin.user_id, request_id=_request_id(request))
    return {"role": role}


@router.get("/v1/admin/data-source-roles/previews/{token}")
async def preview_page(token: str, request: Request, response: Response,
                       page: int = Query(default=1, ge=1),
                       page_size: int = Query(default=50, ge=1, le=100),
                       admin: Principal = Depends(require_admin)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await _call(_service(request).preview_page, token=token, actor_id=admin.user_id,
                       page=page, page_size=page_size)


@router.get("/v1/admin/data-source-roles/{role_id}")
async def get_role(role_id: str, request: Request, response: Response,
                   _admin: Principal = Depends(require_admin)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return {"role": await _call(_service(request).get_role, role_id=role_id)}


@router.post("/v1/admin/data-source-roles/{role_id}/preview")
async def preview_role(role_id: str, body: RolePreviewInput, request: Request, response: Response,
                       page: int = Query(default=1, ge=1),
                       page_size: int = Query(default=50, ge=1, le=100),
                       admin: Principal = Depends(require_admin)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await _call(_service(request).preview_role, role_id=role_id,
                       data_source_ids=body.data_source_ids, delete=body.delete,
                       actor_id=admin.user_id, page=page, page_size=page_size)


@router.patch("/v1/admin/data-source-roles/{role_id}")
async def change_role(role_id: str, body: RoleChangeInput, request: Request, response: Response,
                      admin: Principal = Depends(require_admin)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    role = await _call(_service(request).change_role, role_id=role_id, name=body.name,
                       description=body.description, data_source_ids=body.data_source_ids,
                       delete=False, preview_token=body.preview_token,
                       actor_id=admin.user_id, request_id=_request_id(request))
    return {"role": role}


@router.delete("/v1/admin/data-source-roles/{role_id}")
async def delete_role(role_id: str, body: RoleDeleteInput, request: Request, response: Response,
                      admin: Principal = Depends(require_admin)) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    await _call(_service(request).change_role, role_id=role_id, name=None,
                description=None, data_source_ids=None, delete=True,
                preview_token=body.preview_token, actor_id=admin.user_id,
                request_id=_request_id(request))
    return {"ok": True}


@router.get("/v1/admin/users/{user_id}/data-source-roles")
async def get_bindings(user_id: str, request: Request, response: Response,
                       _admin: Principal = Depends(require_admin)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return {"role_ids": await _call(_service(request).get_bindings, user_id=user_id)}


@router.post("/v1/admin/users/{user_id}/data-source-roles/preview")
async def preview_bindings(user_id: str, body: BindingInput, request: Request, response: Response,
                           admin: Principal = Depends(require_admin)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await _call(_service(request).preview_bindings, user_id=user_id,
                       role_ids=body.role_ids, actor_id=admin.user_id)


@router.put("/v1/admin/users/{user_id}/data-source-roles")
async def replace_bindings(user_id: str, body: BindingInput, request: Request, response: Response,
                           admin: Principal = Depends(require_admin)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    ids = await _call(_service(request).replace_bindings, user_id=user_id,
                      role_ids=body.role_ids, preview_token=body.preview_token,
                      actor_id=admin.user_id, request_id=_request_id(request))
    return {"role_ids": ids}
