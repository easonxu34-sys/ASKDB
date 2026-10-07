from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from api.schemas.model_settings import (
    DeleteModelProfileInput,
    ModelProfileInput,
    ModelProfileTestInput,
    ModelSettingsInput,
)
from api.dependencies import require_admin
from application.model_settings import (
    ModelProbeFailed,
    ModelSettingsApplication,
)
from model_settings import (
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
            "MODEL_AUTH_FAILED": "模型鉴权失败，请检查 API Key、地域和模型访问权限。",
            "MODEL_CONNECTION_FAILED": "无法连接模型服务或服务暂不可用，请检查 API 地址、网络和服务状态。",
            "MODEL_REJECTED": "模型服务拒绝了探测请求，请检查模型名称、请求参数或调用额度。",
            "MODEL_RESPONSE_INVALID": "服务已响应，但返回格式不符合预期；请确认 API URL 和服务类型。",
        }
        response_diagnostics = {
            "response_too_large": "服务响应过大，无法安全解析。",
            "invalid_json": "服务返回的内容不是有效 JSON；请确认填写的是完整 API URL。",
            "response_not_object": "服务返回的 JSON 顶层不是对象；请确认 API URL 和服务类型。",
            "embedding_output_missing": "响应中没有 output.embeddings；请确认使用 DashScope 原生 Embedding 接口。",
            "embedding_count_mismatch": "返回的向量条数与测试输入不一致。",
            "embedding_item_invalid": "Embedding 返回项不是有效对象。",
            "embedding_index_missing": "Embedding 结果缺少索引；多条输入时必须返回 text_index。",
            "embedding_index_invalid": "Embedding 返回的 text_index 无效或重复。",
            "embedding_vector_missing": "Embedding 返回项缺少 embedding 字段。",
            "embedding_vector_invalid": "Embedding 的 embedding 必须是数值数组。",
            "embedding_dimension_mismatch": "返回向量维度与请求维度不一致；请确认模型支持该维度。",
            "embedding_value_invalid": "Embedding 向量包含非数值内容。",
            "rerank_output_missing": "响应中没有 output.results；请确认使用 DashScope 原生 Rerank 接口。",
            "rerank_count_mismatch": "返回的排序条数与测试文档数不一致。",
            "rerank_item_invalid": "Rerank 结果缺少有效的 index 或 relevance_score。",
        }
        message = response_diagnostics.get(error.diagnostic_code, messages[error.code])
        raise HTTPException(
            status_code=502,
            detail={
                "code": error.code,
                "message": message,
                **({"diagnostic_code": error.diagnostic_code} if error.diagnostic_code else {}),
            },
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
            **({"model_kind": body.model_kind, "service_options": body.service_options} if isinstance(body, ModelProfileInput) else {}),
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
            **({"model_kind": body.model_kind, "service_options": body.service_options} if isinstance(body, ModelProfileInput) else {}),
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
            **({"model_kind": body.model_kind, "service_options": body.service_options} if isinstance(body, ModelProfileInput) else {}),
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
            **({"model_kind": body.model_kind, "service_options": body.service_options} if isinstance(body, ModelProfileInput) else {}),
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
            **({"model_kind": body.model_kind, "service_options": body.service_options} if isinstance(body, ModelProfileInput) else {}),
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


@router.put('/v1/settings/models/{profile_id}/memory-processing')
async def set_memory_processing(profile_id: str, request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    try:
        _service(request).store.set_memory_chat(profile_id)
        return await _service(request).public_catalog()
    except Exception as exc:
        _raise_settings_error(exc)
