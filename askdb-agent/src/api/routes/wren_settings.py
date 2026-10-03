from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from api.schemas.wren_settings import (
    DataSourceCreate,
    DataSourceUpdate,
    DefaultDataSourceUpdate,
)
from api.dependencies import require_admin, require_current_user
from domain.auth import Principal
from integrations.wren_connectors import connector_catalog
from wren_settings import WrenConfigurationError, WrenSettingsUnavailable


router = APIRouter()


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _service(request: Request) -> Any:
    return request.app.state.wren_settings


def _raise_safe(exc: Exception) -> None:
    if isinstance(exc, LookupError):
        raise HTTPException(
            status_code=404,
            detail={"code": "DATA_SOURCE_NOT_FOUND", "message": "找不到所选数据源或操作。"},
        ) from None
    if isinstance(exc, WrenSettingsUnavailable):
        raise HTTPException(
            status_code=503,
            detail={"code": "WREN_SETTINGS_UNAVAILABLE", "message": "Wren 设置存储当前不可用。"},
        ) from None
    if isinstance(exc, WrenConfigurationError):
        code = exc.code
        if code == "SOURCE_RECOVERY_REQUIRED":
            status_code = 503
        elif code in {
            "DATA_SOURCE_UNAVAILABLE", "SOURCE_OPERATION_IN_PROGRESS",
            "SOURCE_GENERATION_STALE", "WREN_DRAFT_PENDING",
            "WREN_DRAFT_STALE", "WREN_RULE_REMOVAL_PENDING",
            "WREN_SUPPRESSED_RULE_RESTORE",
        }:
            status_code = 409
        else:
            status_code = 422
        raise HTTPException(
            status_code=status_code,
            detail={"code": code, "message": str(exc)},
        ) from None
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=422,
            detail={"code": "WREN_CONFIGURATION_INVALID", "message": str(exc)},
        ) from None
    raise HTTPException(
        status_code=503,
        detail={"code": "WREN_SETTINGS_UNAVAILABLE", "message": "Wren 设置服务当前不可用。"},
    ) from None


@router.get("/v1/data-sources")
async def public_catalog(
    request: Request,
    response: Response,
    principal: Principal = Depends(require_current_user),
) -> dict[str, Any]:
    _no_store(response)
    try:
        service = _service(request)
        await service.initialize()
        catalog = service.catalog()
        allowed_source_ids = request.app.state.auth_application.source_ids_for(principal)
        enabled_sources = [
            source
            for source in catalog["data_sources"]
            if source["enabled"]
            and (allowed_source_ids is None or source["id"] in allowed_source_ids)
        ]
        visible_ids = {source["id"] for source in enabled_sources}
        return {
            **catalog,
            "default_data_source_id": (
                catalog["default_data_source_id"]
                if catalog["default_data_source_id"] in visible_ids
                else None
            ),
            "data_sources": enabled_sources,
        }
    except Exception as exc:
        _raise_safe(exc)


@router.get("/v1/settings/wren/data-sources", dependencies=[Depends(require_admin)])
async def list_sources(request: Request, response: Response) -> dict[str, Any]:
    _no_store(response)
    try:
        service = _service(request)
        await service.initialize()
        store = service.store
        return {
            "default_data_source_id": store.public_catalog()["default_data_source_id"],
            "data_sources": [
                store.source_detail(source.id)
                for source in store.list_data_sources()
            ],
        }
    except Exception as exc:
        _raise_safe(exc)


@router.get("/v1/settings/wren/connectors", dependencies=[Depends(require_admin)])
async def list_connectors(response: Response) -> dict[str, Any]:
    _no_store(response)
    try:
        return {"connectors": connector_catalog()}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={"code": "WREN_SETTINGS_UNAVAILABLE", "message": "Wren 连接器定义当前不可用。"},
        ) from None


@router.post("/v1/settings/wren/data-sources", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def create_source(
    body: DataSourceCreate,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    _no_store(response)
    try:
        service = _service(request)
        await service.initialize()
        return service.create_source(
            body.display_name,
            dict(body.connection),
            body.semantic.model_dump(),
            connector_type=body.connector_type,
        )
    except Exception as exc:
        _raise_safe(exc)


@router.get("/v1/settings/wren/data-sources/{source_id}", dependencies=[Depends(require_admin)])
async def get_source(source_id: str, request: Request, response: Response) -> dict[str, Any]:
    _no_store(response)
    try:
        service = _service(request)
        await service.initialize()
        return service.detail(source_id)
    except Exception as exc:
        _raise_safe(exc)


@router.get(
    "/v1/settings/wren/data-sources/{source_id}/revisions/{revision_id}",
    dependencies=[Depends(require_admin)],
)
async def get_source_revision(
    source_id: str,
    revision_id: str,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    _no_store(response)
    try:
        service = _service(request)
        await service.initialize()
        return service.revision_detail(source_id, revision_id)
    except Exception as exc:
        _raise_safe(exc)


@router.put("/v1/settings/wren/data-sources/{source_id}", dependencies=[Depends(require_admin)])
async def update_source(
    source_id: str,
    body: DataSourceUpdate,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    _no_store(response)
    try:
        return _service(request).update_draft(
            source_id,
            body.display_name,
            dict(body.connection),
            body.semantic.model_dump(),
        )
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/settings/wren/data-sources/{source_id}/connection/test", dependencies=[Depends(require_admin)])
async def test_connection(source_id: str, request: Request, response: Response) -> dict[str, Any]:
    _no_store(response)
    try:
        return await asyncio.to_thread(_service(request).test_connection, source_id)
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/settings/wren/data-sources/{source_id}/schema/refresh", dependencies=[Depends(require_admin)])
async def refresh_schema(source_id: str, request: Request, response: Response) -> dict[str, Any]:
    _no_store(response)
    try:
        return await asyncio.to_thread(_service(request).refresh_schema, source_id)
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/settings/wren/data-sources/{source_id}/apply", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_admin)])
async def apply_source(source_id: str, request: Request, response: Response) -> dict[str, Any]:
    _no_store(response)
    try:
        return await _service(request).start_apply(source_id)
    except Exception as exc:
        _raise_safe(exc)


@router.get("/v1/settings/wren/operations/{operation_id}", dependencies=[Depends(require_admin)])
async def get_operation(operation_id: str, request: Request, response: Response) -> dict[str, Any]:
    _no_store(response)
    try:
        return _service(request).get_operation(operation_id)
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/settings/wren/data-sources/{source_id}/rollback/{revision_id}", dependencies=[Depends(require_admin)])
async def rollback(
    source_id: str,
    revision_id: str,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    _no_store(response)
    try:
        return await _service(request).rollback(source_id, revision_id)
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/settings/wren/data-sources/{source_id}/deactivate", dependencies=[Depends(require_admin)])
async def deactivate(source_id: str, request: Request, response: Response) -> dict[str, Any]:
    _no_store(response)
    try:
        return _service(request).deactivate(source_id)
    except Exception as exc:
        _raise_safe(exc)


@router.post("/v1/settings/wren/data-sources/{source_id}/enable", dependencies=[Depends(require_admin)])
async def enable(source_id: str, request: Request, response: Response) -> dict[str, Any]:
    _no_store(response)
    try:
        return _service(request).enable(source_id)
    except Exception as exc:
        _raise_safe(exc)


@router.put("/v1/settings/wren/default-data-source", dependencies=[Depends(require_admin)])
async def update_default(
    body: DefaultDataSourceUpdate,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    _no_store(response)
    try:
        return _service(request).set_default(body.data_source_id)
    except Exception as exc:
        _raise_safe(exc)
