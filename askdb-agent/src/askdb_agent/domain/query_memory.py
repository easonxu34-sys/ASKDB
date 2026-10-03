from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from askdb_agent.domain.memory_recall import (
    PublicationStatus,
    QueryParameterSpec,
    QueryExample,
    ReviewStatus,
)


@dataclass(frozen=True)
class QueryExampleCandidate:
    """Governed query-example record plus private review and thread provenance."""

    id: UUID
    data_source_id: str
    source_thread_id: str | None
    source_turn_id: UUID | None
    normalized_question: str | None
    sql_template: str | None
    parameter_specs: tuple[QueryParameterSpec, ...]
    connector_type: str
    wren_revision_id: str
    mdl_digest: str
    content_hash: str
    submitted_by: str
    review_status: ReviewStatus
    publication_status: PublicationStatus
    reviewed_by: str | None
    review_reason_code: str | None
    version: int
    created_at: datetime
    reviewed_at: datetime | None
    activated_at: datetime | None
    superseded_at: datetime | None
    revoked_at: datetime | None
    expires_at: datetime

    def recall_record(self) -> QueryExample | None:
        if self.normalized_question is None or self.sql_template is None:
            return None
        return QueryExample(
            id=self.id,
            data_source_id=self.data_source_id,
            normalized_question=self.normalized_question,
            sql_template=self.sql_template,
            connector_type=self.connector_type,
            wren_revision_id=self.wren_revision_id,
            mdl_digest=self.mdl_digest,
            review_status=self.review_status,
            publication_status=self.publication_status,
            publication_operation_id=None,
            content_hash=self.content_hash,
            source_turn_id=self.source_turn_id,
            submitted_by=self.submitted_by,
            reviewed_by=self.reviewed_by,
            created_at=self.created_at,
            reviewed_at=self.reviewed_at,
            activated_at=self.activated_at,
            superseded_at=self.superseded_at,
            revoked_at=self.revoked_at,
            parameter_specs=self.parameter_specs,
        )


@dataclass(frozen=True)
class QueryCorpusRevision:
    data_source_id: str
    corpus_revision: int
    connector_type: str
    wren_revision_id: str
    mdl_digest: str
    content_hash: str
    record_ids: tuple[UUID, ...]
    canonical_path: str
    status: str
    created_at: datetime
    activated_at: datetime | None
    superseded_at: datetime | None
    delete_after: datetime | None
