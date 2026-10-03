from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BusinessRuleCandidateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_source_id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=16, max_length=128)
    idempotency_key: str = Field(
        min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]{16,128}$"
    )
    term: str = Field(min_length=1, max_length=512)
    definition: str = Field(min_length=1, max_length=10000)
    mdl_references: list[str] = Field(default_factory=list, max_length=30)


class BusinessRuleTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)


class BusinessRuleClarificationRequest(BusinessRuleTransition):
    question: str = Field(min_length=1, max_length=2000)


class BusinessRuleClarificationResponse(BusinessRuleTransition):
    term: str = Field(min_length=1, max_length=512)
    definition: str = Field(min_length=1, max_length=10000)
    mdl_references: list[str] = Field(default_factory=list, max_length=30)


class BusinessRuleRejectInput(BusinessRuleTransition):
    reason_code: Literal[
        "duplicate_term",
        "ambiguous_definition",
        "unsupported_scope",
        "invalid_reference",
        "conflicting_rule",
        "other",
    ]


class BusinessRuleRevokeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(
        min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]{16,128}$"
    )


class BusinessRuleCandidateView(BaseModel):
    business_rule_id: str
    data_source_id: str
    term: str | None
    definition: str | None
    mdl_references: list[str]
    base_wren_revision_id: str
    base_mdl_digest: str
    content_hash: str
    review_status: str
    publication_status: str
    has_exact_term_conflict: bool
    clarification_question: str | None
    submitted_by: str
    reviewed_by: str | None
    review_reason_code: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
