from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai", "deepseek", "custom"]
    name: str = Field(default="默认模型", min_length=1, max_length=100)
    profile_id: str | None = Field(default=None, min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: str = Field(default="", max_length=4096)
    context_window_tokens: int | None = Field(default=None, ge=1024, le=2_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=2_000_000)
    tokenizer_id: Literal["tiktoken:cl100k_base", "tiktoken:o200k_base"] | None = None


class ModelProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    provider: Literal["openai", "deepseek", "custom"]
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: str = Field(default="", max_length=4096)
    context_window_tokens: int | None = Field(default=None, ge=1024, le=2_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=2_000_000)
    tokenizer_id: Literal["tiktoken:cl100k_base", "tiktoken:o200k_base"] | None = None


class ModelProfileTestInput(ModelProfileInput):
    profile_id: str | None = Field(default=None, min_length=1, max_length=128)


class DeleteModelProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_default_profile_id: str | None = Field(default=None, min_length=1, max_length=128)


class PublicModelSettings(BaseModel):
    provider: Literal["openai", "deepseek", "custom"]
    model: str
    base_url: str
    api_key_configured: bool
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    tokenizer_id: Literal["tiktoken:cl100k_base", "tiktoken:o200k_base"] | None = None
