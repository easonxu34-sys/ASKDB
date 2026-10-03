from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class QueryParameterSpecInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    value_type: Literal["string", "integer", "decimal", "boolean", "date", "datetime"]
    nullable: bool = False


class QueryExampleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=16, max_length=128)
    # Opaque key derived from the client turn id, not a database row identifier.
    source_turn_key: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(
        min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]{16,128}$"
    )
    question: str = Field(min_length=1, max_length=1000)
    sql_template: str = Field(min_length=1, max_length=20000)
    parameter_specs: list[QueryParameterSpecInput] = Field(default_factory=list, max_length=32)


class QueryExampleTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class QueryExampleReject(QueryExampleTransition):
    reason_code: Literal[
        "incorrect_sql", "incorrect_semantics", "unsafe_template", "duplicate", "other"
    ]


class QueryExampleRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(
        min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]{16,128}$"
    )


class QueryExampleView(BaseModel):
    query_example_id: UUID
    data_source_id: str
    source_thread_id: str | None
    source_turn_id: UUID | None
    normalized_question: str | None
    sql_template: str | None
    parameter_specs: list[QueryParameterSpecInput]
    connector_type: str
    wren_revision_id: str
    mdl_digest: str
    content_hash: str
    submitted_by: str
    review_status: str
    publication_status: str
    reviewed_by: str | None
    review_reason_code: str | None
    version: int
    created_at: datetime
    reviewed_at: datetime | None
    activated_at: datetime | None
    superseded_at: datetime | None
    revoked_at: datetime | None
    expires_at: datetime


class QueryCorpusRevisionView(BaseModel):
    data_source_id: str
    corpus_revision: int
    connector_type: str
    wren_revision_id: str
    mdl_digest: str
    content_hash: str
    record_ids: list[UUID]
    status: str
    created_at: datetime
    activated_at: datetime | None
    superseded_at: datetime | None
    delete_after: datetime | None
