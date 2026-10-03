from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from application.lexical_recall import lexical_recall
from domain.memory_recall import RecallDocument, RecallKind


class RecallEvalCategory(StrEnum):
    SCHEMA_RULE = "schema_rule"
    QUERY_EXAMPLE = "query_example"
    NO_MATCH_OR_ISOLATION = "no_match_or_isolation"


@dataclass(frozen=True)
class RecallEvalCase:
    case_id: str
    category: RecallEvalCategory
    query: str
    data_source_id: str
    connector_type: str
    wren_revision_id: str
    mdl_digest: str
    expected_document_ids: frozenset[str]
    prohibited_document_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RecallCategoryMetrics:
    category: RecallEvalCategory
    question_count: int
    hit_at_k: float | None
    precision_at_k: float | None
    no_match_false_recall_count: int
    prohibited_recall_count: int


@dataclass(frozen=True)
class RecallEvaluationReport:
    total_questions: int
    categories: tuple[RecallCategoryMetrics, ...]
    minimum_dataset_complete: bool
    release_eligible: bool


_MINIMUM_CASES = {
    RecallEvalCategory.SCHEMA_RULE: 20,
    RecallEvalCategory.QUERY_EXAMPLE: 20,
    RecallEvalCategory.NO_MATCH_OR_ISOLATION: 10,
}


def evaluate_lexical_recall(
    cases: tuple[RecallEvalCase, ...],
    documents: tuple[RecallDocument, ...],
    *,
    suppressed_ids_by_source: Mapping[str, frozenset[str]] | None = None,
) -> RecallEvaluationReport:
    """Compute per-category metrics and the fixed lexical release gate."""
    suppressed = suppressed_ids_by_source or {}
    category_reports: list[RecallCategoryMetrics] = []
    false_recalls = 0
    prohibited_recalls = 0
    thresholds_passed = True
    counts = {category: 0 for category in RecallEvalCategory}

    for category in RecallEvalCategory:
        selected_cases = tuple(case for case in cases if case.category == category)
        counts[category] = len(selected_cases)
        hit_count = 0
        relevant_retrieved = 0
        false_recall_count = 0
        prohibited_recall_count = 0
        applicable_count = 0
        k = 3 if category is RecallEvalCategory.QUERY_EXAMPLE else 5

        for case in selected_cases:
            allowed = (
                frozenset({RecallKind.QUERY_EXAMPLE})
                if category is RecallEvalCategory.QUERY_EXAMPLE
                else (
                    frozenset({RecallKind.SCHEMA, RecallKind.BUSINESS_RULE})
                    if category is RecallEvalCategory.SCHEMA_RULE
                    else frozenset(RecallKind)
                )
            )
            hits = lexical_recall(
                documents,
                data_source_id=case.data_source_id,
                connector_type=case.connector_type,
                wren_revision_id=case.wren_revision_id,
                mdl_digest=case.mdl_digest,
                query=case.query,
                suppressed_ids=suppressed.get(case.data_source_id, frozenset()),
                allowed_kinds=allowed,
            )
            top_hits = hits[:k]
            returned_ids = {hit.document.id for hit in top_hits}
            matched = returned_ids & case.expected_document_ids
            prohibited = returned_ids & case.prohibited_document_ids
            prohibited_recall_count += len(prohibited)
            if not case.expected_document_ids:
                if top_hits:
                    false_recall_count += 1
            else:
                applicable_count += 1
                if matched:
                    hit_count += 1
                relevant_retrieved += len(matched)

        hit_at_k = hit_count / applicable_count if applicable_count else None
        precision_at_k = (
            relevant_retrieved / (k * applicable_count)
            if applicable_count
            else None
        )
        false_recalls += false_recall_count
        prohibited_recalls += prohibited_recall_count
        category_reports.append(
            RecallCategoryMetrics(
                category=category,
                question_count=len(selected_cases),
                hit_at_k=hit_at_k,
                precision_at_k=precision_at_k,
                no_match_false_recall_count=false_recall_count,
                prohibited_recall_count=prohibited_recall_count,
            )
        )
        if category is RecallEvalCategory.SCHEMA_RULE and (
            hit_at_k is None or hit_at_k < 0.8
        ):
            thresholds_passed = False
        if category is RecallEvalCategory.QUERY_EXAMPLE and (
            hit_at_k is None or hit_at_k < 0.8
        ):
            thresholds_passed = False

    dataset_complete = all(
        counts[category] >= minimum for category, minimum in _MINIMUM_CASES.items()
    )
    release_eligible = (
        dataset_complete
        and thresholds_passed
        and false_recalls == 0
        and prohibited_recalls == 0
    )
    return RecallEvaluationReport(
        total_questions=len(cases),
        categories=tuple(category_reports),
        minimum_dataset_complete=dataset_complete,
        release_eligible=release_eligible,
    )
