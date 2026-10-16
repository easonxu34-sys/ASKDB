from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleCreateInput(_Input):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)
    data_source_ids: list[str] = Field(default_factory=list, max_length=100)


class RoleChangeInput(_Input):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    data_source_ids: list[str] | None = Field(default=None, max_length=100)
    preview_token: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def require_change(self):
        if self.name is None and self.description is None and self.data_source_ids is None:
            raise ValueError("请提供角色变更。")
        return self


class RolePreviewInput(_Input):
    data_source_ids: list[str] | None = Field(default=None, max_length=100)
    delete: bool = False


class RoleDeleteInput(_Input):
    preview_token: str = Field(min_length=1, max_length=128)


class BindingInput(_Input):
    role_ids: list[str] = Field(max_length=100)
    preview_token: str | None = Field(default=None, max_length=128)
