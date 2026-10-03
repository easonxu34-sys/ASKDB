from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from askdb_agent.api.schemas.model_settings import (
    DeleteModelProfileInput,
    ModelProfileInput,
    ModelProfileTestInput,
    ModelSettingsInput,
)
from askdb_agent.api.dependencies import require_admin
from askdb_agent.application.model_settings import (
    ModelProbeFailed,
    ModelSettingsApplication,
)
from askdb_agent.model_settings import (
    ModelConfigurationError,
    ModelProfileNotFound,
    ModelSettingsUnavailable,
)

router = APIRouter(dependencies=[Depends(require_admin)])


def _service(request: Request) -> ModelSettingsApplication:
    return request.app.state.model_settings


def _raise_settings_error(error: Exception) -> None:
    if isinstance(error, ModelSettingsUnavailable):
        raise HTTPException(
            status_code=503,
            detail={"code": "MODEL_SETTINGS_UNAVAILABLE", "message": "模型设置服务暂不可用。"},
        ) from None
    if isinstance(error, ModelProfileNotFound):
        raise HTTPException(
            status_code=404,
            detail={"code": "MODEL_PROFILE_NOT_FOUND", "message": "找不到所选模型配置，请重新选择。"},
        ) from None
    if isinstance(error, ModelConfigurationError):
        raise HTTPException(
            status_code=422,
            detail={"code": "MODEL_CONFIGURATION_INVALID", "message": str(error)},
        ) from None
    if isinstance(error, ModelProbeFailed):
        messages = {
            "MODEL_AUTH_FAILED": "模型认证失败，请检查 API Key。",
            "MODEL_CONNECTION_FAILED": "无法连接模型服务，请检查 API 地址后重试。",
            "MODEL_REJECTED": "模型服务拒绝了探测请求，请检查模型名称和配置。",
        }
        raise HTTPException(
            status_code=502,
            detail={"code": error.code, "message": messages[error.code]},
        ) from None
    raise HTTPException(
        status_code=503,
        detail={"code": "MODEL_SETTINGS_UNAVAILABLE", "message": "模型设置服务暂不可用。"},
    ) from None


@router.get("/v1/settings/models")
async def get_model_profiles(request: Request, response: Response) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _service(request).public_catalog()
    except Exception as exc:
        _raise_settings_error(exc)


@router.post("/v1/settings/models/test")
async def test_model_profile(
    body: ModelProfileTestInput, request: Request, response: Response
) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    try:
        await _service(request).test(
            body.provider, body.model, body.base_url, body.api_key,
            profile_id=body.profile_id,
            context_window_tokens=body.context_window_tokens,
            max_output_tokens=body.max_output_tokens,
            tokenizer_id=body.tokenizer_id,
        )
    except Exception as exc:
        _raise_settings_error(exc)
    return {"ok": True}


@router.post("/v1/settings/models")
async def create_model_profile(
    body: ModelProfileInput, request: Request, response: Response
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _service(request).create(
            body.name, body.provider, body.model, body.base_url, body.api_key,
            context_window_tokens=body.context_window_tokens,
            max_output_tokens=body.max_output_tokens,
            tokenizer_id=body.tokenizer_id,
        )
    except Exception as exc:
        _raise_settings_error(exc)


@router.put("/v1/settings/models/{profile_id}")
async def update_model_profile(
    profile_id: str, body: ModelProfileInput, request: Request, response: Response
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _service(request).update(
            profile_id, body.name, body.provider, body.model, body.base_url, body.api_key,
            context_window_tokens=body.context_window_tokens,
            max_output_tokens=body.max_output_tokens,
            tokenizer_id=body.tokenizer_id,
        )
    except Exception as exc:
        _raise_settings_error(exc)


@router.put("/v1/settings/models/{profile_id}/default")
async def set_default_model_profile(
    profile_id: str, request: Request, response: Response
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _service(request).set_default(profile_id)
    except Exception as exc:
        _raise_settings_error(exc)


@router.delete("/v1/settings/models/{profile_id}")
async def delete_model_profile(
    profile_id: str,
    request: Request,
    response: Response,
    body: DeleteModelProfileInput | None = None,
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _service(request).delete(
            profile_id, body.new_default_profile_id if body else None
        )
    except Exception as exc:
        _raise_settings_error(exc)


@router.delete("/v1/settings/models/{profile_id}/credential")
async def delete_model_profile_credential(
    profile_id: str, request: Request, response: Response
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _service(request).clear_credential(profile_id)
    except Exception as exc:
        _raise_settings_error(exc)


@router.get("/v1/settings/model")
async def get_model_settings(request: Request, response: Response) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _service(request).public_settings()
    except Exception as exc:
        _raise_settings_error(exc)


@router.post("/v1/settings/model/test")
async def test_model_settings(
    body: ModelSettingsInput, request: Request, response: Response
) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    try:
        await _service(request).test(
            body.provider, body.model, body.base_url, body.api_key,
            profile_id=body.profile_id,
            use_default_if_missing=True,
            context_window_tokens=body.context_window_tokens,
            max_output_tokens=body.max_output_tokens,
            tokenizer_id=body.tokenizer_id,
        )
    except Exception as exc:
        _raise_settings_error(exc)
    return {"ok": True}


@router.put("/v1/settings/model")
async def put_model_settings(
    body: ModelSettingsInput, request: Request, response: Response
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _service(request).save(
            body.provider, body.model, body.base_url, body.api_key,
            context_window_tokens=body.context_window_tokens,
            max_output_tokens=body.max_output_tokens,
            tokenizer_id=body.tokenizer_id,
        )
    except Exception as exc:
        _raise_settings_error(exc)


@router.delete("/v1/settings/model/credential")
async def delete_model_credential(request: Request, response: Response) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _service(request).clear_default_credential()
    except Exception as exc:
        _raise_settings_error(exc)
