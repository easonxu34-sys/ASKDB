from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from askdb_agent.application.conversation_memory import sanitize_turn_text
from askdb_agent.domain.auth import Principal
from askdb_agent.domain.memory_recall import QueryParameterSpec, QueryParameterType, normalize_question
from askdb_agent.domain.query_memory import QueryExampleCandidate
from askdb_agent.application.sql_template import (
    SqlTemplateError,
    ValidatedSqlTemplate,
    validate_query_example_template,
    validate_bound_query_for_use,
)
from askdb_agent.integrations.conversation_store import ConversationMemoryStore
from askdb_agent.integrations.query_memory_store import (
    QueryCorpusRevision,
    QueryMemoryForbidden,
    QueryMemoryStaleSource,
    QueryMemoryStore,
    QueryMemoryUnavailable,
    QueryMemoryValidationError,
)
from askdb_agent.integrations.wren_memory import compute_semantic_digest


_SECRET = re.compile(
    r"\b(?:api[_ -]?key|password|passwd|token|secret|access[_ -]?key)\b\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)
_EMAIL_OR_PHONE = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
    r"|(?<![\w])(?:\+?86[- ]?)?1[3-9]\d{9}(?![\w])",
    re.IGNORECASE,
)
_LABELED_ID = re.compile(
    r"(?i)(?:customer|client|user|member|account|order)[_ -]?(?:id|no|number)\s*[:=]?\s*"
    r"[\"']?[A-Z0-9][A-Z0-9_-]{2,}[\"']?"
    r"|(?:客户|用户|会员|账号|订单)(?:id|ID|编号|号码|号)\s*[:=：#]?\s*"
    r"[\"']?[A-Z0-9][A-Z0-9_-]{2,}[\"']?"
)


class QueryMemoryApplication:
    """Coordinates trusted source semantics, user scope, and corpus governance."""

    def __init__(
        self,
        store: QueryMemoryStore,
        wren_store: Any,
        auth_application: Any,
        *,
        template_validator: Callable[[str, tuple[QueryParameterSpec, ...], str], None] | None = None,
    ) -> None:
        self.store = store
        self.wren_store = wren_store
        self.auth_application = auth_application
        self.template_validator = template_validator

    def _active_semantics(self, source_id: str) -> tuple[str, str, str]:
        try:
            source = self.wren_store.get_data_source(source_id)
        except (LookupError, KeyError) as exc:
            raise QueryMemoryStaleSource("data source is unavailable") from exc
        if not source.enabled or source.runtime_status != "ready" or not source.active_revision_id:
            raise QueryMemoryStaleSource("data source has no active semantic revision")
        revision = self.wren_store.get_revision(source_id, source.active_revision_id)
        if revision.status != "active" or not revision.project_dir or not revision.mdl_digest:
            raise QueryMemoryStaleSource("active semantic digest is unavailable")
        digest = compute_semantic_digest(Path(revision.project_dir), source.connector_type)
        if digest != revision.mdl_digest:
            raise QueryMemoryStaleSource("active semantics require a runtime refresh")
        return revision.id, digest, source.connector_type

    @staticmethod
    def _question(value: str) -> str:
        result = normalize_question(sanitize_turn_text(value, max_chars=1000))
        if not result or len(result) > 500:
            raise QueryMemoryValidationError("query-example question is invalid")
        return result

    @staticmethod
    def _template(value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 20_000:
            raise QueryMemoryValidationError("SQL template is invalid")
        if _SECRET.search(value) or _EMAIL_OR_PHONE.search(value) or _LABELED_ID.search(value):
            raise QueryMemoryValidationError("SQL template contains a secret or personal identifier")
        return value.strip()

    @staticmethod
    def _parameters(values: tuple[Any, ...]) -> tuple[QueryParameterSpec, ...]:
        if len(values) > 32:
            raise QueryMemoryValidationError("too many SQL template parameters")
        result: list[QueryParameterSpec] = []
        names: set[str] = set()
        for value in values:
            name = getattr(value, "name", None)
            type_value = getattr(value, "value_type", None)
            if not isinstance(type_value, QueryParameterType):
                try:
                    type_value = QueryParameterType(type_value)
                except (TypeError, ValueError) as exc:
                    raise QueryMemoryValidationError("unsupported SQL template parameter type") from exc
            if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) or name in names:
                raise QueryMemoryValidationError("SQL template parameter names must be unique identifiers")
            nullable = getattr(value, "nullable", False)
            if not isinstance(nullable, bool):
                raise QueryMemoryValidationError("SQL template nullability is invalid")
            result.append(QueryParameterSpec(name, type_value, nullable))
            names.add(name)
        return tuple(sorted(result, key=lambda item: item.name))

    def _validate_template(
        self, candidate: QueryExampleCandidate, toolkit: Any = None,
        connector_type: str | None = None,
    ) -> None:
        if candidate.sql_template is None:
            raise QueryMemoryValidationError("query example content has been removed")
        try:
            if self.template_validator is not None:
                self.template_validator(
                    candidate.sql_template, candidate.parameter_specs, candidate.connector_type
                )
            elif toolkit is not None:
                validate_query_example_template(
                    candidate.sql_template,
                    parameter_specs=candidate.parameter_specs,
                    connector_type=connector_type or candidate.connector_type,
                    toolkit=toolkit,
                )
            else:
                raise QueryMemoryUnavailable("SQL template validation is not installed")
        except SqlTemplateError as exc:
            raise QueryMemoryValidationError("SQL template failed validation") from exc

    def submit(
        self, *, principal: Principal, thread_id: str,
        source_turn_id: str | None, idempotency_key: str, question: str,
        sql_template: str, parameter_specs: tuple[Any, ...],
    ) -> QueryExampleCandidate:
        data_source_id = self.store.resolve_thread_source(
            actor_id=principal.user_id, thread_id=thread_id
        )
        if not self.auth_application.can_access_data_source(principal, data_source_id):
            raise QueryMemoryForbidden("source grant required")
        revision_id, digest, connector_type = self._active_semantics(data_source_id)
        normalized = self._question(question)
        template = self._template(sql_template)
        parameters = self._parameters(parameter_specs)
        # Structural SQL and placeholder checks are repeated at approval time.
        # Submission only stages a pending candidate and never enables retrieval.
        return self.store.submit(
            actor_id=principal.user_id, data_source_id=data_source_id,
            thread_id=thread_id, source_turn_id=source_turn_id,
            idempotency_key=idempotency_key, normalized_question=normalized,
            sql_template=template, parameter_specs=parameters,
            connector_type=connector_type, wren_revision_id=revision_id, mdl_digest=digest,
        )

    def list_candidates(
        self, *, principal: Principal, data_source_id: str,
        review_status: str | None = None, limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[tuple[QueryExampleCandidate, ...], str | None]:
        if not self.auth_application.can_access_data_source(principal, data_source_id) and principal.role != "admin":
            raise QueryMemoryForbidden("source grant required")
        return self.store.list_candidates(
            actor_id=principal.user_id, is_admin=principal.role == "admin",
            data_source_id=data_source_id, review_status=review_status,
            limit=limit, cursor=cursor,
        )

    def approve(
        self, example_id: str, *, principal: Principal, expected_version: int,
        toolkit: Any = None,
    ) -> tuple[QueryExampleCandidate, QueryCorpusRevision]:
        if principal.role != "admin":
            raise QueryMemoryForbidden("admin role required")
        candidate = self.store.get_candidate(example_id)
        if candidate.review_status.value == "pending":
            revision_id, digest, connector_type = self._active_semantics(candidate.data_source_id)
            if candidate.wren_revision_id != revision_id or candidate.mdl_digest != digest:
                self.store.mark_needs_revalidation(
                    example_id, actor_id=principal.user_id, expected_version=expected_version
                )
                raise QueryMemoryStaleSource("query example must be revalidated against current MDL")
            self._validate_template(candidate, toolkit)
            return self.store.approve(
                example_id, actor_id=principal.user_id, expected_version=expected_version,
                connector_type=connector_type, wren_revision_id=revision_id, mdl_digest=digest,
            )
        raise QueryMemoryValidationError("query example is not awaiting review")

    def revalidate(
        self, example_id: str, *, principal: Principal, expected_version: int,
        toolkit: Any = None,
    ) -> QueryExampleCandidate:
        if principal.role != "admin":
            raise QueryMemoryForbidden("admin role required")
        candidate = self.store.get_candidate(example_id)
        revision_id, digest, connector_type = self._active_semantics(candidate.data_source_id)
        self._validate_template(candidate, toolkit, connector_type=connector_type)
        return self.store.revalidate(
            example_id, actor_id=principal.user_id, expected_version=expected_version,
            connector_type=connector_type, wren_revision_id=revision_id, mdl_digest=digest,
        )

    def reject(
        self, example_id: str, *, principal: Principal, expected_version: int,
        reason_code: str,
    ) -> QueryExampleCandidate:
        if principal.role != "admin":
            raise QueryMemoryForbidden("admin role required")
        return self.store.reject(
            example_id, actor_id=principal.user_id,
            expected_version=expected_version, reason_code=reason_code,
        )

    def withdraw(
        self, example_id: str, *, principal: Principal, expected_version: int,
    ) -> QueryExampleCandidate:
        return self.store.withdraw(
            example_id, actor_id=principal.user_id, expected_version=expected_version,
        )

    def revoke(
        self, example_id: str, *, principal: Principal, idempotency_key: str,
        conversation_store: ConversationMemoryStore,
    ) -> dict[str, Any]:
        if principal.role != "admin":
            raise QueryMemoryForbidden("admin role required")
        candidate = self.store.get_candidate(example_id)
        operation = conversation_store.revoke_query_example(
            data_source_id=candidate.data_source_id,
            query_example_id=str(candidate.id), actor_user_id=principal.user_id,
            idempotency_key=idempotency_key,
        )
        return {
            "query_example_id": str(candidate.id),
            "data_source_id": candidate.data_source_id,
            "status": operation.status,
            "journal_sequence": operation.journal_sequence,
            "operation_id": operation.operation_id,
        }

    def validate_for_use(
        self, *, data_source_id: str, mdl_digest: str, example_id: str,
        values: dict[str, Any], toolkit: Any,
    ) -> ValidatedSqlTemplate:
        """Return SQL only for an active, current-digest example after live Wren checks."""
        active = self.store.active_examples(data_source_id=data_source_id, mdl_digest=mdl_digest)
        example = next((item for item in active if item.id.hex == example_id.replace("-", "")), None)
        if example is None:
            raise QueryMemoryNotFound("query example is not active for the current source revision")
        try:
            return validate_bound_query_for_use(
                example.sql_template,
                parameter_specs=example.parameter_specs,
                values=values,
                connector_type=example.connector_type,
                toolkit=toolkit,
            )
        except SqlTemplateError as exc:
            raise QueryMemoryValidationError("bound query example failed current SQL validation") from exc
