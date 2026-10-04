#!/usr/bin/env python3
"""Load an offline recall fixture and emit a reproducible evaluation report."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


AGENT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = Path(__file__).with_name("askdb_tpcc_recall_gold_draft.json")
PLACEHOLDER = "[已脱敏记录标识]"


def _load_runtime():
    sys.path.insert(0, str(AGENT_ROOT / "src"))
    from application.lexical_recall import lexical_recall
    from application.recall_evaluation import (
        RecallEvalCase,
        RecallEvalCategory,
        evaluate_lexical_recall,
    )
    from domain.memory_recall import RecallDocument, RecallKind

    return lexical_recall, RecallEvalCase, RecallEvalCategory, evaluate_lexical_recall, RecallDocument, RecallKind


def _read_fixture(path: Path, RecallEvalCase: Any, RecallEvalCategory: Any, RecallDocument: Any, RecallKind: Any):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported fixture schema_version")
    if payload.get("purpose") != "offline lexical recall evaluation only; never load into application memory":
        raise ValueError("fixture purpose must explicitly remain offline-only")
    docs = payload.get("documents")
    rows = payload.get("cases")
    if not isinstance(docs, list) or not isinstance(rows, list):
        raise ValueError("fixture must contain documents and cases arrays")
    if not docs or not rows:
        raise ValueError("fixture documents and cases must not be empty")

    document_ids: set[str] = set()
    documents = []
    for item in docs:
        if not isinstance(item, dict):
            raise ValueError("each document must be an object")
        if item.get("id") in document_ids:
            raise ValueError(f"duplicate document id: {item.get('id')}")
        document_ids.add(item.get("id"))
        body = item.get("body", "")
        if PLACEHOLDER in body:
            raise ValueError(f"unrepaired schema placeholder in document {item.get('id')}")
        kind = RecallKind(item["kind"])
        if kind is RecallKind.SCHEMA:
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if item.get("content_hash") != digest:
                raise ValueError(f"schema content hash mismatch in document {item.get('id')}")
        documents.append(
            RecallDocument(
                id=item["id"], data_source_id=item["data_source_id"], kind=kind,
                title=item["title"], terms=tuple(item.get("terms", ())), body=body,
                connector_type=item["connector_type"], wren_revision_id=item["wren_revision_id"],
                mdl_digest=item["mdl_digest"], review_status=item["review_status"],
                publication_status=item["publication_status"], content_hash=item["content_hash"],
                sql_template=item.get("sql_template"),
            )
        )

    cases = []
    case_ids: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError("each case must be an object")
        case_id = item["case_id"]
        if case_id in case_ids:
            raise ValueError(f"duplicate case_id: {case_id}")
        case_ids.add(case_id)
        expected = frozenset(item.get("expected_document_ids", ()))
        prohibited = frozenset(item.get("prohibited_document_ids", ()))
        missing_ids = (expected | prohibited) - document_ids
        if missing_ids:
            raise ValueError(f"case {case_id} references missing documents: {sorted(missing_ids)}")
        cases.append(
            RecallEvalCase(
                case_id=case_id, category=RecallEvalCategory(item["category"]),
                query=item["query"], data_source_id=item["data_source_id"],
                connector_type=item["connector_type"], wren_revision_id=item["wren_revision_id"],
                mdl_digest=item["mdl_digest"], expected_document_ids=expected,
                prohibited_document_ids=prohibited,
            )
        )

    observed_counts = Counter(case.category.value for case in cases)
    declared = payload.get("case_counts", {})
    if declared.get("total") != len(cases):
        raise ValueError("case_counts.total does not match loaded cases")
    for name in ("schema_rule", "query_example", "no_match_or_isolation"):
        if declared.get(name) != observed_counts.get(name, 0):
            raise ValueError(f"case_counts.{name} does not match loaded cases")
    return payload, tuple(documents), tuple(cases)


def _build_report(payload: dict[str, Any], documents: tuple[Any, ...], cases: tuple[Any, ...], lexical_recall: Any, evaluate: Any) -> dict[str, Any]:
    metric_report = evaluate(cases, documents)
    category_rows = []
    for row in metric_report.categories:
        category_rows.append({
            "category": row.category.value,
            "question_count": row.question_count,
            "hit_at_k": row.hit_at_k,
            "precision_at_k": row.precision_at_k,
            "no_match_false_recall_count": row.no_match_false_recall_count,
            "prohibited_recall_count": row.prohibited_recall_count,
        })

    per_case = []
    for case in cases:
        allowed = None
        if case.category.value == "query_example":
            allowed = frozenset(
                document.kind for document in documents
                if document.kind.value == "query_example"
            )
        elif case.category.value == "schema_rule":
            allowed = frozenset(
                document.kind for document in documents
                if document.kind.value in {"schema", "business_rule"}
            )
        else:
            allowed = frozenset(kind for kind in {doc.kind for doc in documents})
        k = 3 if case.category.value == "query_example" else 5
        hits = lexical_recall(
            documents, data_source_id=case.data_source_id, connector_type=case.connector_type,
            wren_revision_id=case.wren_revision_id, mdl_digest=case.mdl_digest,
            query=case.query, allowed_kinds=allowed,
        )[:k]
        returned = [hit.document.id for hit in hits]
        matched = sorted(set(returned) & case.expected_document_ids)
        prohibited = sorted(set(returned) & case.prohibited_document_ids)
        per_case.append({
            "case_id": case.case_id,
            "category": case.category.value,
            "query": case.query,
            "expected_document_ids": sorted(case.expected_document_ids),
            "returned_document_ids": returned,
            "matched_document_ids": matched,
            "prohibited_document_ids_returned": prohibited,
            "pass": (
                bool(matched) and not prohibited
                if case.expected_document_ids
                else not returned and not prohibited
            ),
        })

    review = payload.get("human_review") or {}
    reviewed_case_ids = set(review.get("reviewed_case_ids", ()))
    expected_case_ids = {case.case_id for case in cases}
    human_review_complete = (
        payload.get("dataset_status") == "approved"
        and review.get("status") == "approved"
        and bool(review.get("reviewer"))
        and bool(review.get("reviewed_at"))
        and review.get("required_case_count") == len(cases)
        and review.get("reviewed_case_count") == len(cases)
        and reviewed_case_ids == expected_case_ids
    )
    eligible = metric_report.release_eligible and human_review_complete
    return {
        "report_version": 1,
        "fixture": payload.get("fixture_name", DEFAULT_FIXTURE.name),
        "dataset_status": payload.get("dataset_status"),
        "human_review_complete": human_review_complete,
        "total_questions": metric_report.total_questions,
        "document_count": len(documents),
        "minimum_dataset_complete": metric_report.minimum_dataset_complete,
        "metric_release_eligible": metric_report.release_eligible,
        "release_eligible": eligible,
        "release_criteria": {
            "minimum_cases": {
                "schema_rule": 20,
                "query_example": 20,
                "no_match_or_isolation": 10,
            },
            "schema_rule_hit_at_5_minimum": 0.8,
            "query_example_hit_at_3_minimum": 0.8,
            "no_match_false_recall_maximum": 0,
            "prohibited_document_recall_maximum": 0,
            "numeric_metrics_pass": metric_report.release_eligible,
            "all_cases_human_reviewed": human_review_complete,
        },
        "categories": category_rows,
        "cases": per_case,
        "notes": [
            "Offline retrieval selection and source-isolation metrics only; this does not verify SQL result correctness.",
            "A draft or unreviewed fixture can never be release_eligible, even when its retrieval metrics pass.",
        ],
    }


def _render_text(report: dict[str, Any]) -> str:
    lines = [
        f"Recall evaluation: {report['fixture']}",
        f"Dataset: {report['dataset_status']} | questions: {report['total_questions']} | documents: {report['document_count']}",
        f"Review complete: {report['human_review_complete']} | minimum sample counts met: {report['minimum_dataset_complete']}",
        f"Metric gate: {report['metric_release_eligible']} | release eligible: {report['release_eligible']}",
        "",
        "Category metrics:",
    ]
    for category in report["categories"]:
        lines.append(
            f"- {category['category']}: n={category['question_count']}, Hit@k={category['hit_at_k']}, "
            f"Precision@k={category['precision_at_k']}, false_no_match={category['no_match_false_recall_count']}, "
            f"prohibited={category['prohibited_recall_count']}"
        )
    failures = [case for case in report["cases"] if not case["pass"]]
    lines.extend(["", f"Failed cases: {len(failures)}"])
    for case in failures:
        lines.append(
            f"- {case['case_id']}: expected={case['expected_document_ids']}, "
            f"returned={case['returned_document_ids']}, prohibited={case['prohibited_document_ids_returned']}"
        )
    lines.extend(["", *report["notes"]])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", nargs="?", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path, help="write the report to this path instead of stdout")
    args = parser.parse_args(argv)
    try:
        lexical_recall, Case, Category, evaluate, Document, Kind = _load_runtime()
        payload, documents, cases = _read_fixture(args.fixture, Case, Category, Document, Kind)
        payload["fixture_name"] = args.fixture.name
        report = _build_report(payload, documents, cases, lexical_recall, evaluate)
        output = json.dumps(report, ensure_ascii=False, indent=2) if args.format == "json" else _render_text(report)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + "\n", encoding="utf-8")
            print(f"Report written to {args.output}")
        else:
            print(output)
        return 0 if report["release_eligible"] else 2
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"recall evaluation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
