from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


PermissionView = Literal["user", "source"]
ExportView = Literal["user", "source", "all"]
PermissionScope = Literal["granted", "all"]
PermissionStatus = Literal[
    "all", "usable", "granted_blocked", "ungranted", "multiple_sources"
]


class EffectivePermissionQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: PermissionView
    user_id: str | None = Field(default=None, min_length=1, max_length=128)
    data_source_id: str | None = Field(default=None, min_length=1, max_length=128)
    scope: PermissionScope = "granted"
    status: PermissionStatus = "all"
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def validate_target(self) -> EffectivePermissionQuery:
        if self.view == "user" and (self.user_id is None or self.data_source_id is not None):
            raise ValueError("user view requires only user_id")
        if self.view == "source" and (self.data_source_id is None or self.user_id is not None):
            raise ValueError("source view requires only data_source_id")
        return self


class EffectivePermissionExportQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: ExportView
    user_id: str | None = Field(default=None, min_length=1, max_length=128)
    data_source_id: str | None = Field(default=None, min_length=1, max_length=128)
    scope: PermissionScope = "granted"
    status: PermissionStatus = "all"

    @model_validator(mode="after")
    def validate_target(self) -> EffectivePermissionExportQuery:
        if self.view == "user" and (self.user_id is None or self.data_source_id is not None):
            raise ValueError("user view requires only user_id")
        if self.view == "source" and (self.data_source_id is None or self.user_id is not None):
            raise ValueError("source view requires only data_source_id")
        if self.view == "all" and (self.user_id is not None or self.data_source_id is not None):
            raise ValueError("all view does not accept target ids")
        return self


class PermissionUser(BaseModel):
    id: str
    username: str
    role: Literal["admin", "member"]
    is_active: bool


class PermissionDataSource(BaseModel):
    id: str
    display_name: str
    exists: bool
    enabled: bool
    runtime_status: str | None


class DirectGrantSource(BaseModel):
    type: Literal["direct"]
    key: Literal["direct"]
    granted_by: str | None
    granted_at: str | None
    role_id: None = None
    role_name: None = None
    bound_by: None = None
    bound_at: None = None


class RoleGrantSource(BaseModel):
    type: Literal["role"]
    key: str
    granted_by: str | None
    granted_at: str | None
    role_id: str
    role_name: str | None
    bound_by: str | None
    bound_at: str | None


class AdminIdentityGrantSource(BaseModel):
    type: Literal["admin_identity"]
    key: Literal["admin_identity"]
    granted_by: None = None
    granted_at: None = None
    role_id: None = None
    role_name: None = None
    bound_by: None = None
    bound_at: None = None


GrantSource = Annotated[
    Union[DirectGrantSource, RoleGrantSource, AdminIdentityGrantSource],
    Field(discriminator="type"),
]


class RemovalImpact(BaseModel):
    source_key: str
    remaining_sources: list[str]
    access_allowed_after: bool
    would_lose_access: bool


class EffectivePermissionItem(BaseModel):
    user: PermissionUser
    data_source: PermissionDataSource
    grant_sources: list[GrantSource]
    has_grant: bool
    access_allowed: bool
    runtime_ready: bool
    usable_now: bool
    block_reasons: list[str]
    removal_impacts: list[RemovalImpact]


class EffectivePermissionCounts(BaseModel):
    total: int
    usable: int
    granted_blocked: int
    ungranted: int
    multiple_sources: int
    matching: int


class PermissionUserTarget(BaseModel):
    type: Literal["user"]
    id: str
    username: str
    role: Literal["admin", "member"]
    is_active: bool


class PermissionSourceTarget(BaseModel):
    type: Literal["source"]
    id: str
    display_name: str
    enabled: bool
    runtime_status: str


PermissionTarget = Annotated[
    Union[PermissionUserTarget, PermissionSourceTarget], Field(discriminator="type")
]


class EffectivePermissionPage(BaseModel):
    read_at: str
    target: PermissionTarget
    counts: EffectivePermissionCounts
    items: list[EffectivePermissionItem]
    next_cursor: str | None
