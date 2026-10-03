from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class BusinessRuleReviewStatus(StrEnum):
    PENDING = "pending"
    NEEDS_CLARIFICATION = "needs_clarification"
    NEEDS_REVALIDATION = "needs_revalidation"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    REVOKED = "revoked"
    EXPIRED = "expired"


class BusinessRulePublicationStatus(StrEnum):
    NOT_PUBLISHED = "not_published"
    QUEUED = "queued"
    PUBLISHING = "publishing"
    ACTIVE = "active"
    FAILED = "failed"
    REMOVAL_PENDING = "removal_pending"
    REMOVED = "removed"


@dataclass(frozen=True)
class BusinessRuleCandidate:
    business_rule_id: str
    data_source_id: str
    term: str | None
    definition: str | None
    mdl_references: tuple[str, ...]
    base_wren_revision_id: str
    base_mdl_digest: str
    content_hash: str
    review_status: BusinessRuleReviewStatus
    publication_status: BusinessRulePublicationStatus
    has_exact_term_conflict: bool
    clarification_question: str | None
    submitted_by: str
    reviewed_by: str | None
    review_reason_code: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class BusinessRuleError(RuntimeError):
    """Base error for candidate validation and state transitions."""


class BusinessRuleNotFound(BusinessRuleError):
    pass


class BusinessRuleForbidden(BusinessRuleError):
    pass


class BusinessRuleConflict(BusinessRuleError):
    pass


class BusinessRuleQuotaExceeded(BusinessRuleError):
    code = "MEMORY_CANDIDATE_QUOTA_EXCEEDED"

    def __init__(self, retry_after: int):
        super().__init__("memory candidate quota exceeded")
        self.retry_after = max(1, retry_after)


class BusinessRuleStaleSource(BusinessRuleConflict):
    pass


class BusinessRuleValidationError(BusinessRuleError, ValueError):
    pass
