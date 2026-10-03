from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class RecallKind(StrEnum):
    SCHEMA = "schema"
    BUSINESS_RULE = "business_rule"
    QUERY_EXAMPLE = "query_example"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVALIDATION = "needs_revalidation"
    REVOKED = "revoked"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


class PublicationStatus(StrEnum):
    DRAFT = "draft"
    NOT_PUBLISHED = "not_published"
    QUEUED = "queued"
    PUBLISHING = "publishing"
    ACTIVE = "active"
    FAILED = "failed"
    SUPERSEDED = "superseded"
    REMOVED = "removed"


class QueryParameterType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"


@dataclass(frozen=True)
class QueryParameterSpec:
    """Value-free parameter metadata for safe SQL-template binding."""

    name: str
    value_type: QueryParameterType
    nullable: bool = False


@dataclass(frozen=True)
class QueryExample:
    """Reviewed, source-scoped NL-to-SQL evidence; never an authorization grant."""

    id: UUID
    data_source_id: str
    normalized_question: str
    sql_template: str
    connector_type: str
    wren_revision_id: str
    mdl_digest: str
    review_status: ReviewStatus
    publication_status: PublicationStatus
    publication_operation_id: str | None
    content_hash: str
    source_turn_id: UUID | None
    submitted_by: str
    reviewed_by: str | None
    created_at: datetime
    reviewed_at: datetime | None
    activated_at: datetime | None
    superseded_at: datetime | None
    revoked_at: datetime | None
    parameter_specs: tuple[QueryParameterSpec, ...] = ()


@dataclass(frozen=True)
class CorpusManifest:
    data_source_id: str
    corpus_revision: int
    connector_type: str
    wren_revision_id: str
    mdl_digest: str
    content_hash: str
    record_ids: tuple[UUID, ...]
    created_at: datetime


@dataclass(frozen=True)
class RecallDocument:
    """Immutable input row for lexical retrieval from a single source snapshot."""

    id: str
    data_source_id: str
    kind: RecallKind
    title: str
    terms: tuple[str, ...]
    body: str
    connector_type: str
    wren_revision_id: str
    mdl_digest: str
    review_status: str
    publication_status: str
    content_hash: str
    sql_template: str | None = None

    @classmethod
    def from_query_example(cls, example: QueryExample) -> RecallDocument:
        verify_query_example_content_hash(example)
        return cls(
            id=example.id.hex,
            data_source_id=example.data_source_id,
            kind=RecallKind.QUERY_EXAMPLE,
            title=example.normalized_question,
            terms=(),
            body="",
            connector_type=example.connector_type,
            wren_revision_id=example.wren_revision_id,
            mdl_digest=example.mdl_digest,
            review_status=example.review_status.value,
            publication_status=example.publication_status.value,
            content_hash=example.content_hash,
            sql_template=example.sql_template,
        )


@dataclass(frozen=True)
class RecallHit:
    document: RecallDocument
    score: float
    matched_terms: tuple[str, ...]


def normalize_question(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def query_example_content_hash(example: QueryExample) -> str:
    if example.normalized_question != normalize_question(example.normalized_question):
        raise ValueError("query example question is not normalized")
    payload = {
        "data_source_id": example.data_source_id,
        "normalized_question": example.normalized_question,
        "sql_template": example.sql_template,
        "connector_type": example.connector_type,
        "wren_revision_id": example.wren_revision_id,
        "mdl_digest": example.mdl_digest,
        "parameter_specs": [
            {
                "name": item.name,
                "type": item.value_type.value,
                "nullable": item.nullable,
            }
            for item in example.parameter_specs
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_query_example_content_hash(example: QueryExample) -> None:
    if example.content_hash != query_example_content_hash(example):
        raise ValueError("query example content hash does not match its content")


def active_corpus_digest(
    data_source_id: str,
    examples: tuple[QueryExample, ...],
    *,
    suppressed_ids: frozenset[str] = frozenset(),
) -> str:
    """Hash only approved, active, unsuppressed evidence for one source."""
    active = sorted(
        (
            example
            for example in examples
            if example.data_source_id == data_source_id
            and example.review_status == ReviewStatus.APPROVED
            and example.publication_status == PublicationStatus.ACTIVE
            and str(example.id) not in suppressed_ids
        ),
        key=lambda item: str(item.id),
    )
    for example in active:
        verify_query_example_content_hash(example)
    payload = {
        "data_source_id": data_source_id,
        "records": [
            {
                "id": str(example.id),
                "content_hash": example.content_hash,
                "wren_revision_id": example.wren_revision_id,
                "mdl_digest": example.mdl_digest,
                "connector_type": example.connector_type,
                "review_status": example.review_status.value,
                "publication_status": example.publication_status.value,
            }
            for example in active
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
