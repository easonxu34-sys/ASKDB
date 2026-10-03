from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


UserRole = Literal["admin", "member"]


class AuthInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(AuthInput):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordInput(AuthInput):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class CreateUserInput(AuthInput):
    username: str = Field(min_length=1, max_length=128)
    role: UserRole = "member"


class UpdateUserInput(AuthInput):
    username: str | None = Field(default=None, min_length=1, max_length=128)
    role: UserRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_a_change(self) -> UpdateUserInput:
        if not self.model_fields_set or any(
            getattr(self, name) is None for name in self.model_fields_set
        ):
            raise ValueError("请至少提供一项有效的账号修改。")
        return self
