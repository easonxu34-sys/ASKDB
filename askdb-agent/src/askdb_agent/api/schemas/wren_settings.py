from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelColumnForm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=2000)
    hidden: bool = False
    primary_key: bool = False


class ModelForm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=4000)
    columns: list[ModelColumnForm] = Field(default_factory=list, max_length=2000)


class RelationshipForm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=128)
    left_model: str = Field(min_length=1, max_length=128)
    right_model: str = Field(min_length=1, max_length=128)
    join_type: Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"] = "many_to_one"
    condition: str = Field(min_length=1, max_length=4000)


class RuleForm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="业务规则", max_length=128)
    content: str = Field(min_length=1, max_length=20_000)


class ViewForm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=4000)
    sql: str = Field(min_length=1, max_length=50_000)


class SemanticForm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tables: list[str] = Field(default_factory=list, max_length=2000)
    models: list[ModelForm] = Field(default_factory=list, max_length=1000)
    relationships: list[RelationshipForm] = Field(default_factory=list, max_length=2000)
    rules: list[RuleForm] = Field(default_factory=list, max_length=1000)
    views: list[ViewForm] = Field(default_factory=list, max_length=1000)


class DataSourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=120)
    connector_type: str = Field(min_length=1, max_length=64)
    connection: dict[str, object]
    semantic: SemanticForm = Field(default_factory=SemanticForm)


class DataSourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=120)
    connection: dict[str, object]
    semantic: SemanticForm = Field(default_factory=SemanticForm)


class DefaultDataSourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_source_id: str | None = Field(default=None, max_length=128)
