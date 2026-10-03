from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable

from askdb_agent.domain.memory_recall import (
    PublicationStatus,
    RecallDocument,
    RecallHit,
    RecallKind,
    ReviewStatus,
)


_TOKEN_PATTERN = re.compile(
    r"[A-Za-z0-9]+(?:[_$][A-Za-z0-9]+)*"
    r"|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+"
    r"|[\u3040-\u30ff]+|[\uac00-\ud7af]+"
)
_IDENTIFIER_PART_PATTERN = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)|[0-9]+")
_IDENTIFIER_SEPARATOR_PATTERN = re.compile(r"[_$]+")
_TITLE_WEIGHT = 2.0
_BODY_WEIGHT = 1.0
_K1 = 1.2
_B = 0.75
_KIND_LIMITS = {
    RecallKind.SCHEMA: 5,
    RecallKind.BUSINESS_RULE: 5,
    RecallKind.QUERY_EXAMPLE: 3,
}


def _is_cjk_token(value: str) -> bool:
    return bool(value) and ord(value[0]) >= 0x2E80


def tokenize(text: str) -> tuple[str, ...]:
    """NFKC, casefold, retain identifiers and split them, and bigram CJK text."""
    normalized = unicodedata.normalize("NFKC", text)
    tokens: list[str] = []
    for match in _TOKEN_PATTERN.finditer(normalized):
        raw = match.group(0)
        if _is_cjk_token(raw):
            if len(raw) == 1:
                tokens.append(raw.casefold())
            else:
                tokens.extend(raw[index : index + 2].casefold() for index in range(len(raw) - 1))
            continue

        whole = raw.casefold()
        tokens.append(whole)
        separated = _IDENTIFIER_SEPARATOR_PATTERN.sub(" ", raw)
        parts: list[str] = []
        for segment in separated.split():
            pieces = _IDENTIFIER_PART_PATTERN.findall(segment)
            parts.extend(piece.casefold() for piece in pieces if piece)
        tokens.extend(part for part in parts if part != whole)
    return tuple(tokens)


def _is_recallable(document: RecallDocument) -> bool:
    if document.kind == RecallKind.QUERY_EXAMPLE:
        return (
            document.review_status == ReviewStatus.APPROVED.value
            and document.publication_status == PublicationStatus.ACTIVE.value
        )
    return (
        document.review_status in {ReviewStatus.APPROVED.value, "active"}
        and document.publication_status in {PublicationStatus.ACTIVE.value, "published"}
    )


def _bm25_field_score(
    term: str,
    frequencies: Counter[str],
    field_length: int,
    average_length: float,
    inverse_document_frequency: float,
) -> float:
    frequency = frequencies[term]
    if not frequency:
        return 0.0
    length_ratio = field_length / average_length if average_length > 0 else 0.0
    denominator = frequency + _K1 * (1 - _B + _B * length_ratio)
    return inverse_document_frequency * frequency * (_K1 + 1) / denominator


def lexical_recall(
    documents: Iterable[RecallDocument],
    *,
    data_source_id: str,
    connector_type: str,
    wren_revision_id: str,
    mdl_digest: str,
    query: str,
    suppressed_ids: frozenset[str] = frozenset(),
    allowed_kinds: frozenset[RecallKind] = frozenset(RecallKind),
    per_kind_limit: int | None = None,
) -> tuple[RecallHit, ...]:
    """Deterministic source/digest-scoped BM25 recall over a frozen document set.

    Query examples require both review approval and active publication. Suppression
    and semantic identity are applied before corpus statistics are calculated. The
    hard per-kind caps remain in force when callers request a smaller shared limit.
    """
    if per_kind_limit is not None and per_kind_limit < 1:
        return ()
    query_terms = Counter(tokenize(query))
    if not query_terms:
        return ()
    eligible = tuple(
        document
        for document in documents
        if document.data_source_id == data_source_id
        and document.connector_type == connector_type
        and (
            document.kind is RecallKind.QUERY_EXAMPLE
            or document.wren_revision_id == wren_revision_id
        )
        and document.mdl_digest == mdl_digest
        and document.kind in allowed_kinds
        and document.id not in suppressed_ids
        and _is_recallable(document)
    )
    if not eligible:
        return ()

    fields: dict[tuple[RecallKind, str], tuple[Counter[str], Counter[str]]] = {}
    document_frequency: Counter[str] = Counter()
    title_lengths: list[int] = []
    body_lengths: list[int] = []
    for document in eligible:
        title_counts = Counter(tokenize(" ".join((document.title, *document.terms))))
        body_counts = Counter(tokenize(document.body))
        fields[(document.kind, document.id)] = (title_counts, body_counts)
        title_lengths.append(sum(title_counts.values()))
        body_lengths.append(sum(body_counts.values()))
        all_terms = set(title_counts) | set(body_counts)
        document_frequency.update(all_terms)

    average_title_length = sum(title_lengths) / len(title_lengths)
    average_body_length = sum(body_lengths) / len(body_lengths)
    hits: list[RecallHit] = []
    population = len(eligible)
    for document in eligible:
        title_counts, body_counts = fields[(document.kind, document.id)]
        title_length = sum(title_counts.values())
        body_length = sum(body_counts.values())
        score = 0.0
        matched: list[str] = []
        for term, query_frequency in query_terms.items():
            title_frequency = title_counts[term]
            body_frequency = body_counts[term]
            if not title_frequency and not body_frequency:
                continue
            matched.append(term)
            df = document_frequency[term]
            idf = math.log1p((population - df + 0.5) / (df + 0.5))
            field_score = (
                _TITLE_WEIGHT
                * _bm25_field_score(
                    term,
                    title_counts,
                    title_length,
                    average_title_length,
                    idf,
                )
                + _BODY_WEIGHT
                * _bm25_field_score(
                    term,
                    body_counts,
                    body_length,
                    average_body_length,
                    idf,
                )
            )
            score += query_frequency * field_score
        if score > 0:
            hits.append(RecallHit(document, score, tuple(sorted(matched))))

    hits.sort(key=lambda hit: (-hit.score, hit.document.id, hit.document.kind.value))
    selected: list[RecallHit] = []
    query_example_count = 0
    semantic_document_count = 0
    for hit in hits:
        if hit.document.kind == RecallKind.QUERY_EXAMPLE:
            cap = _KIND_LIMITS[RecallKind.QUERY_EXAMPLE]
            count = query_example_count
        else:
            cap = 5
            count = semantic_document_count
        if per_kind_limit is not None:
            cap = min(cap, per_kind_limit)
        if count >= cap:
            continue
        selected.append(hit)
        if hit.document.kind == RecallKind.QUERY_EXAMPLE:
            query_example_count += 1
        else:
            semantic_document_count += 1
    return tuple(selected)
