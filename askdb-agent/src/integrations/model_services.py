"""Explicit embedding/reranking protocols; never route these through Chat."""
from __future__ import annotations

import math
from typing import Any

import httpx2 as httpx

from model_settings import ModelConfigurationError


class ModelServiceError(RuntimeError):
    """A provider returned a structured error in an otherwise valid response."""

    def __init__(self, status_code: int | None, provider_code: str):
        self.status_code = status_code
        self.provider_code = provider_code
        super().__init__("model service returned an error")


class ModelResponseInvalid(ValueError):
    """A successful HTTP response did not match the selected service protocol."""

    def __init__(self, diagnostic_code: str):
        self.diagnostic_code = diagnostic_code
        super().__init__(diagnostic_code)


def validate_service_options(kind: str, options: dict[str, Any]) -> dict[str, Any]:
    allowed = {'dimensions', 'protocol', 'max_candidates'}
    if set(options) - allowed:
        raise ModelConfigurationError('模型服务参数包含不支持的字段。')
    if kind == 'chat':
        if options:
            raise ModelConfigurationError('Chat 不接受向量或排序参数。')
        return {}
    protocol = options.get('protocol', 'openai_embedding' if kind == 'embedding' else 'compatible_rerank')
    if kind == 'embedding':
        dimensions = options.get('dimensions', 1024)
        if protocol not in {'openai_embedding', 'dashscope_embedding'} or type(dimensions) is not int or not 1 <= dimensions <= 4096:
            raise ModelConfigurationError('Embedding 协议或维度无效。')
        return {'protocol': protocol, 'dimensions': dimensions}
    count = options.get('max_candidates', 20)
    if protocol not in {'compatible_rerank', 'dashscope_rerank'} or type(count) is not int or not 1 <= count <= 100:
        raise ModelConfigurationError('Rerank 协议或候选数无效。')
    return {'protocol': protocol, 'max_candidates': count}


async def _request(profile: Any, body: dict[str, Any]) -> dict[str, Any]:
    if not profile.api_key:
        raise ModelConfigurationError('模型服务未配置密钥。')
    # Non-chat profiles use an explicit, complete endpoint chosen by the admin.
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.post(profile.base_url, json=body, headers={'Authorization': f'Bearer {profile.api_key}'})
        response.raise_for_status()
        if len(response.content) > 8_000_000:
            raise ModelResponseInvalid('response_too_large')
        try:
            value = response.json()
        except ValueError:
            raise ModelResponseInvalid('invalid_json') from None
    if not isinstance(value, dict):
        raise ModelResponseInvalid('response_not_object')
    status_code = value.get('status_code')
    if type(status_code) is not int:
        status_code = None
    provider_code = value.get('code')
    provider_code = provider_code.strip() if isinstance(provider_code, str) else ''
    if (status_code is not None and not 200 <= status_code < 300) or provider_code:
        raise ModelServiceError(status_code, provider_code)
    return value


def checked_embeddings(value: dict[str, Any], count: int, dimensions: int, native: bool = False) -> list[list[float]]:
    output = value.get('output') if native else value
    if not isinstance(output, dict):
        raise ModelResponseInvalid('embedding_output_missing')
    rows = output.get('embeddings' if native else 'data')
    if not isinstance(rows, list):
        raise ModelResponseInvalid('embedding_output_missing')
    if len(rows) != count:
        raise ModelResponseInvalid('embedding_count_mismatch')
    result: dict[int, list[float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ModelResponseInvalid('embedding_item_invalid')
        if native:
            if 'text_index' in row:
                index = row['text_index']
            elif 'index' in row:
                index = row['index']
            elif count == 1:
                # A single-input probe has an unambiguous result position.
                index = 0
            else:
                raise ModelResponseInvalid('embedding_index_missing')
        else:
            if 'index' not in row:
                raise ModelResponseInvalid('embedding_index_missing')
            index = row.get('index')
        if type(index) is not int or not 0 <= index < count or index in result:
            raise ModelResponseInvalid('embedding_index_invalid')
        if 'embedding' not in row:
            raise ModelResponseInvalid('embedding_vector_missing')
        vector = row.get('embedding')
        if not isinstance(vector, list):
            raise ModelResponseInvalid('embedding_vector_invalid')
        if len(vector) != dimensions:
            raise ModelResponseInvalid('embedding_dimension_mismatch')
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in vector):
            raise ModelResponseInvalid('embedding_value_invalid')
        result[index] = [float(x) for x in vector]
    return [result[index] for index in range(count)]


async def embed(profile: Any, texts: list[str]) -> list[list[float]]:
    if profile.model_kind != 'embedding' or not 1 <= len(texts) <= 10:
        raise ValueError('invalid embedding request')
    options = profile.service_options
    native = options['protocol'] == 'dashscope_embedding'
    body = {'model': profile.model, 'input': {'texts': texts}, 'parameters': {'dimension': options['dimensions']}} if native else {'model': profile.model, 'input': texts, 'dimensions': options['dimensions'], 'encoding_format': 'float'}
    return checked_embeddings(await _request(profile, body), len(texts), options['dimensions'], native)


def checked_ranking(value: dict[str, Any], count: int, native: bool = False) -> list[int]:
    output = value.get('output') if native else value
    if not isinstance(output, dict):
        raise ModelResponseInvalid('rerank_output_missing')
    rows = output.get('results')
    if not isinstance(rows, list):
        raise ModelResponseInvalid('rerank_output_missing')
    if len(rows) != count:
        raise ModelResponseInvalid('rerank_count_mismatch')
    indices: list[int] = []
    for row in rows:
        index = row.get('index') if isinstance(row, dict) else None
        score = row.get('relevance_score') if isinstance(row, dict) else None
        if type(index) is not int or not 0 <= index < count or index in indices or type(score) not in (float, int) or not math.isfinite(score):
            raise ModelResponseInvalid('rerank_item_invalid')
        indices.append(index)
    return indices


async def rerank(profile: Any, query: str, documents: list[str]) -> list[int]:
    if profile.model_kind != 'rerank' or not 1 <= len(documents) <= profile.service_options['max_candidates']:
        raise ValueError('invalid rerank request')
    native = profile.service_options['protocol'] == 'dashscope_rerank'
    body = {'model': profile.model, 'input': {'query': query, 'documents': documents}, 'parameters': {'top_n': len(documents)}} if native else {'model': profile.model, 'query': query, 'documents': documents, 'top_n': len(documents)}
    return checked_ranking(await _request(profile, body), len(documents), native)
