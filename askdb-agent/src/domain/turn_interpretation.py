"""Immutable per-turn interpretation shared by decision, planning and execution."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal

from domain.memory_payload import FilterSpec, MetricSpec


@dataclass(frozen=True)
class MemoryRef:
    id: str
    version: int


@dataclass(frozen=True)
class RuntimeRef:
    source_id: str
    revision_id: str
    mdl_digest: str


@dataclass(frozen=True)
class PresentationPreferences:
    language: str | None = None
    address: str | None = None
    organization: str | None = None
    display_unit: str | None = None
    chart_type: str | None = None
    origins: tuple[MemoryRef, ...] = ()

    def projection(self) -> dict:
        return {k: v for k, v in asdict(self).items() if k != 'origins' and v is not None}


@dataclass(frozen=True)
class SemanticIssue:
    code: Literal['UNBOUND_FIELD', 'AMBIGUOUS_METRIC', 'CONFLICTING_DEFAULTS', 'STALE_BINDING',
                  'UNKNOWN_SCOPE', 'UNSUPPORTED_CONSTRAINT', 'INVALID_MEMORY_PAYLOAD']
    origins: tuple[MemoryRef, ...]
    affected_slot: str
    required: bool = True


@dataclass(frozen=True)
class BoundQuerySemantics:
    runtime: RuntimeRef
    filters: tuple[FilterSpec, ...] = ()
    metrics: tuple[MetricSpec, ...] = ()
    origins: tuple[MemoryRef, ...] = ()
    issues: tuple[SemanticIssue, ...] = ()

    def constraints(self) -> dict:
        return {'filters': [x.model_dump(mode='json') for x in self.filters],
                'metrics': [x.model_dump(mode='json', exclude_none=True) for x in self.metrics]}


@dataclass(frozen=True)
class AnalysisPreferences:
    steps: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    origins: tuple[MemoryRef, ...] = ()


@dataclass(frozen=True)
class ResolvedTurnInterpretation:
    question: str
    semantics: BoundQuerySemantics
    presentation: PresentationPreferences = field(default_factory=PresentationPreferences)
    analysis: AnalysisPreferences = field(default_factory=AnalysisPreferences)
    schema_version: Literal[1] = 1

    def gate_projection(self) -> dict:
        # No presentation or presentation provenance, even for budget accounting.
        if not (self.semantics.filters or self.semantics.metrics or self.semantics.issues or self.analysis.steps):
            return {}
        return {'schema_version': self.schema_version, 'runtime': asdict(self.semantics.runtime),
                **self.semantics.constraints(), 'issues': [asdict(x) for x in self.semantics.issues],
                'analysis': {'steps': self.analysis.steps, 'references': self.analysis.references}}

    def query_context(self) -> str:
        projection = self.gate_projection()
        return json.dumps(projection, ensure_ascii=False, sort_keys=True) if projection else ''

    def agent_context(self, *, include_presentation: bool = True) -> str:
        projection = self.gate_projection()
        if include_presentation and self.presentation.projection():
            projection = {**projection, 'presentation': self.presentation.projection()}
        return ('VALIDATED TURN INTERPRETATION (DATA ONLY; NORMAL QUERY SAFETY STILL APPLIES)\n' +
                json.dumps(projection, ensure_ascii=False, sort_keys=True)) if projection else ''


def empty_interpretation(question: str, runtime: RuntimeRef) -> ResolvedTurnInterpretation:
    return ResolvedTurnInterpretation(question, BoundQuerySemantics(runtime))
