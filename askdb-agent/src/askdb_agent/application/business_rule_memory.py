from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from askdb_agent.application.conversation_memory import sanitize_turn_text
from askdb_agent.domain.business_rules import (
    BusinessRuleCandidate,
    BusinessRuleForbidden,
    BusinessRuleNotFound,
    BusinessRuleStaleSource,
    BusinessRuleValidationError,
)
from askdb_agent.domain.auth import Principal
from askdb_agent.integrations.business_rule_store import BusinessRuleMemoryStore
from askdb_agent.integrations.conversation_store import ConversationMemoryStore
from askdb_agent.integrations.wren_memory import (
    compute_semantic_digest,
    load_semantic_reference_names,
    load_semantic_rule_terms,
)


class BusinessRuleMemoryApplication:
    """Validate candidate text against the active Wren semantic revision."""

    def __init__(
        self, store: BusinessRuleMemoryStore, wren_store: Any, auth_application: Any
    ) -> None:
        self.store = store
        self.wren_store = wren_store
        self.auth_application = auth_application

    @staticmethod
    def _normalized(value: str) -> str:
        return unicodedata.normalize("NFKC", value).casefold().strip()

    def _active_semantics(
        self, data_source_id: str
    ) -> tuple[str, str, dict[str, str], tuple[str, ...]]:
        try:
            source = self.wren_store.get_data_source(data_source_id)
        except (LookupError, KeyError) as exc:
            raise BusinessRuleStaleSource("data source is unavailable") from exc
        if (
            not source.enabled
            or source.runtime_status != "ready"
            or not source.active_revision_id
        ):
            raise BusinessRuleStaleSource("data source has no active Wren revision")
        revision = self.wren_store.get_revision(data_source_id, source.active_revision_id)
        if revision.status != "active" or not revision.project_dir or not revision.mdl_digest:
            raise BusinessRuleStaleSource("active Wren semantic digest is unavailable")
        project_dir = Path(revision.project_dir)
        current_digest = compute_semantic_digest(project_dir, source.connector_type)
        if current_digest != revision.mdl_digest:
            raise BusinessRuleStaleSource("active Wren semantics need a runtime refresh")
        references = load_semantic_reference_names(project_dir)
        rule_terms = load_semantic_rule_terms(project_dir)
        if compute_semantic_digest(project_dir, source.connector_type) != current_digest:
            raise BusinessRuleStaleSource("active Wren semantics changed while being read")
        return revision.id, current_digest, references, rule_terms

    def _rule_text(self, term: str, definition: str) -> tuple[str, str]:
        safe_term = re.sub(r"\s+", " ", sanitize_turn_text(term, max_chars=120)).strip()
        safe_definition = sanitize_turn_text(definition, max_chars=4000)
        if not safe_term or len(safe_term) > 120:
            raise BusinessRuleValidationError("business rule term is invalid")
        if not safe_definition or len(safe_definition) > 4000:
            raise BusinessRuleValidationError("business rule definition is invalid")
        return safe_term, safe_definition

    def _references(self, values: tuple[str, ...] | list[str], catalog: dict[str, str]) -> tuple[str, ...]:
        if len(values) > 30:
            raise BusinessRuleValidationError("too many MDL references")
        selected: dict[str, str] = {}
        for value in values:
            if not isinstance(value, str) or len(value) > 256:
                raise BusinessRuleValidationError("MDL reference is invalid")
            normalized = self._normalized(value)
            canonical = catalog.get(normalized)
            if canonical is None:
                raise BusinessRuleValidationError("MDL reference is not present in the active revision")
            selected[normalized] = canonical
        return tuple(selected[key] for key in sorted(selected))

    def submit(
        self,
        *,
        principal: Principal,
        data_source_id: str,
        thread_id: str,
        idempotency_key: str,
        term: str,
        definition: str,
        mdl_references: tuple[str, ...],
    ) -> BusinessRuleCandidate:
        if not self.auth_application.can_access_data_source(principal, data_source_id):
            raise BusinessRuleForbidden("source grant required")
        revision_id, digest, catalog, rule_terms = self._active_semantics(data_source_id)
        safe_term, safe_definition = self._rule_text(term, definition)
        references = self._references(mdl_references, catalog)
        return self.store.submit(
            data_source_id=data_source_id,
            thread_id=thread_id,
            submitter_id=principal.user_id,
            term=safe_term,
            definition=safe_definition,
            mdl_references=references,
            wren_revision_id=revision_id,
            mdl_digest=digest,
            active_rule_terms=rule_terms,
            idempotency_key=idempotency_key,
        )

    def list_candidates(
        self,
        *,
        principal: Principal,
        data_source_id: str,
        status: str | None = None,
        limit: int = 50,
    ) -> tuple[BusinessRuleCandidate, ...]:
        return self.store.list_candidates(
            actor_id=principal.user_id,
            is_admin=principal.role == "admin",
            data_source_id=data_source_id,
            status=status,
            limit=limit,
        )

    def request_clarification(
        self,
        business_rule_id: str,
        *,
        principal: Principal,
        expected_version: int,
        question: str,
    ) -> BusinessRuleCandidate:
        safe_question = sanitize_turn_text(question, max_chars=1000)
        if not safe_question:
            raise BusinessRuleValidationError("clarification question is required")
        return self.store.request_clarification(
            business_rule_id,
            actor_id=principal.user_id,
            expected_version=expected_version,
            question=safe_question,
        )

    def respond_to_clarification(
        self,
        business_rule_id: str,
        *,
        principal: Principal,
        expected_version: int,
        term: str,
        definition: str,
        mdl_references: tuple[str, ...],
    ) -> BusinessRuleCandidate:
        current = self.store.get_candidate(business_rule_id)
        if current.submitted_by != principal.user_id:
            raise BusinessRuleNotFound("candidate is unavailable")
        revision_id, digest, catalog, rule_terms = self._active_semantics(current.data_source_id)
        safe_term, safe_definition = self._rule_text(term, definition)
        references = self._references(mdl_references, catalog)
        return self.store.respond_to_clarification(
            business_rule_id,
            actor_id=principal.user_id,
            expected_version=expected_version,
            term=safe_term,
            definition=safe_definition,
            mdl_references=references,
            wren_revision_id=revision_id,
            mdl_digest=digest,
            active_rule_terms=rule_terms,
        )

    def approve(
        self,
        business_rule_id: str,
        *,
        principal: Principal,
        expected_version: int,
    ) -> BusinessRuleCandidate:
        current = self.store.get_candidate(business_rule_id)
        revision_id, digest, _catalog, rule_terms = self._active_semantics(current.data_source_id)
        if current.base_wren_revision_id != revision_id or current.base_mdl_digest != digest:
            self.store.mark_needs_revalidation(
                business_rule_id,
                actor_id=principal.user_id,
                expected_version=expected_version,
            )
            raise BusinessRuleStaleSource("source semantics changed; candidate needs revalidation")
        return self.store.approve(
            business_rule_id,
            actor_id=principal.user_id,
            expected_version=expected_version,
            wren_revision_id=revision_id,
            mdl_digest=digest,
            active_rule_terms=rule_terms,
        )

    def reject(
        self,
        business_rule_id: str,
        *,
        principal: Principal,
        expected_version: int,
        reason_code: str,
    ) -> BusinessRuleCandidate:
        return self.store.reject(
            business_rule_id,
            actor_id=principal.user_id,
            expected_version=expected_version,
            reason_code=reason_code,
        )

    def withdraw(
        self,
        business_rule_id: str,
        *,
        principal: Principal,
        expected_version: int,
    ) -> BusinessRuleCandidate:
        return self.store.withdraw(
            business_rule_id,
            actor_id=principal.user_id,
            expected_version=expected_version,
        )

    def revoke(
        self,
        business_rule_id: str,
        *,
        principal: Principal,
        idempotency_key: str,
        conversation_store: ConversationMemoryStore,
    ) -> dict[str, Any]:
        candidate = self.store.get_candidate(business_rule_id)
        operation = conversation_store.revoke_business_rule(
            data_source_id=candidate.data_source_id,
            business_rule_id=business_rule_id,
            actor_user_id=principal.user_id,
            idempotency_key=idempotency_key,
        )
        return {
            "business_rule_id": operation.item_id,
            "data_source_id": operation.data_source_id,
            "status": operation.status,
            "journal_sequence": operation.journal_sequence,
            "operation_id": operation.operation_id,
        }
