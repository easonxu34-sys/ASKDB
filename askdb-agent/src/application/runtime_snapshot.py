"""Build immutable runtime snapshots for one Wren source revision."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from agent.graph import build_graph
from domain.memory_recall import RecallDocument
from integrations.models import build_model
from integrations.wren import build_wren_toolkit
from integrations.wren_memory import (
    compute_semantic_digest,
    load_semantic_recall_documents,
)
from application.runtime_manager import RuntimeSnapshot
from wren_settings import WrenConfigurationError, WrenSettingsStore


class RuntimeSnapshotBuilder:
    """Assemble a Wren runtime from an explicit source revision and model."""

    def __init__(
        self,
        *,
        store: WrenSettingsStore,
        wren_home: Path,
        install_profile_secrets: Callable[[str, str, dict[str, str]], None],
    ) -> None:
        self.store = store
        self.wren_home = wren_home
        self.install_profile_secrets = install_profile_secrets

    def build(
        self,
        source: Any,
        revision: Any,
        model: Any,
        *,
        query_memory_store: Any | None,
        query_corpus_revision: Any | None = None,
        runtime_identity_resolver: Callable[[Any, Any], tuple[str, str]],
    ) -> RuntimeSnapshot:
        if not revision.project_dir or not revision.profile_name:
            raise WrenConfigurationError("数据源版本尚未生成 Wren 项目和 profile。", code="DATA_SOURCE_UNAVAILABLE")
        secrets = self.store.get_secrets(source.id, revision.id)
        self.install_profile_secrets(source.id, revision.id, secrets)
        toolkit = build_wren_toolkit(
            Path(revision.project_dir),
            revision.profile_name,
            wren_home=self.wren_home,
        )
        graph = build_graph(
            model=build_model(
                model,
                **(
                    {"max_tokens": model.max_output_tokens}
                    if model.max_output_tokens is not None
                    else {}
                ),
            ),
            toolkit=toolkit,
            dialect=source.connector_type,
        )
        semantic_digest = compute_semantic_digest(
            Path(revision.project_dir), source.connector_type
        )
        if revision.mdl_digest != semantic_digest:
            # Upgrade the metadata of pre-memory revisions on first runtime build.
            # The immutable Wren files are unchanged; only the digest definition
            # now includes connector type and reviewed rule content.
            revision = self.store.update_revision_artifacts(
                source.id,
                revision.id,
                project_dir=Path(revision.project_dir),
                profile_name=revision.profile_name,
                mdl_digest=semantic_digest,
            )
        memory_documents = load_semantic_recall_documents(
            Path(revision.project_dir),
            data_source_id=source.id,
            wren_revision_id=revision.id,
            connector_type=source.connector_type,
            mdl_digest=semantic_digest,
            configured_rules=revision.config.get("rules", []),
        )
        if query_corpus_revision is not None:
            if query_memory_store is None:
                raise WrenConfigurationError(
                    "查询语料存储未启用，无法准备候选运行时。",
                    code="QUERY_MEMORY_UNAVAILABLE",
                )
            if (
                query_corpus_revision.data_source_id != source.id
                or query_corpus_revision.connector_type != source.connector_type
                or query_corpus_revision.wren_revision_id != revision.id
                or query_corpus_revision.mdl_digest != semantic_digest
            ):
                raise WrenConfigurationError(
                    "候选查询语料与活动 Wren 语义版本不匹配。",
                    code="MEMORY_REVISION_CHANGED",
                )
            runtime_identity = (
                semantic_digest,
                query_memory_store.revision_identity(query_corpus_revision),
            )
            examples = query_memory_store.prepared_examples(query_corpus_revision)
        else:
            runtime_identity = runtime_identity_resolver(source, revision)
            examples = (
                query_memory_store.active_examples(
                    data_source_id=source.id,
                    mdl_digest=semantic_digest,
                )
                if query_memory_store is not None and runtime_identity[1] != "none"
                else ()
            )
        memory_revision = runtime_identity[1]
        if query_memory_store is not None and memory_revision != "none":
            query_documents = tuple(
                RecallDocument.from_query_example(example)
                for example in examples
                if example.data_source_id == source.id
                and example.connector_type == source.connector_type
                and example.wren_revision_id == revision.id
                and example.mdl_digest == semantic_digest
            )
            memory_documents = (*memory_documents, *query_documents)
        identity_after_build = (
            (
                semantic_digest,
                query_memory_store.revision_identity(query_corpus_revision),
            )
            if query_corpus_revision is not None and query_memory_store is not None
            else runtime_identity_resolver(source, revision)
        )
        if query_corpus_revision is not None and query_memory_store is not None:
            query_memory_store.prepared_examples(query_corpus_revision)
        if runtime_identity != identity_after_build:
            raise WrenConfigurationError(
                "查询记忆版本在运行时准备期间发生变化，请重试。",
                code="MEMORY_REVISION_CHANGED",
            )
        return RuntimeSnapshot(
            source.id,
            revision.id,
            model.id,
            model.updated_at,
            toolkit,
            graph,
            model.context_window_tokens,
            model.max_output_tokens,
            model.tokenizer_id,
            source.connector_type,
            semantic_digest,
            memory_documents,
            memory_revision,
        )
