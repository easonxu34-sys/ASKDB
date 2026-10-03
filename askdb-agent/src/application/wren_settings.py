from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import traceback
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from agent.graph import build_graph
from config import Settings
from integrations.models import build_model
from integrations.wren import build_wren_toolkit
from integrations.wren_cli import WrenCli
from integrations.wren_project import WrenProjectBuilder
from integrations.wren_memory import (
    compute_semantic_digest,
    load_semantic_recall_documents,
)
from domain.memory_recall import RecallDocument
from integrations.schema_readers import SchemaReaderRegistry
from integrations.wren_connectors import (
    SUPPORTED_DATABASE_CONNECTORS,
    connector_fields,
    is_sensitive_field,
    normalize_connection,
    profile_fields as make_profile_fields,
    secret_environment_name,
)
from application.runtime_manager import RuntimeManager, RuntimeSnapshot
from model_settings import ModelSettingsStore
from wren_settings import (
    WrenConfigurationError,
    WrenDataSource,
    WrenSettingsStore,
)


logger = logging.getLogger(__name__)


class WrenSettingsApplication:
    """Coordinates source drafts, Wren CLI operations, revisions, and runtime swaps."""

    def __init__(
        self,
        store: WrenSettingsStore | None = None,
        model_store: ModelSettingsStore | None = None,
        runtime_manager: RuntimeManager | None = None,
        *,
        settings: Settings | None = None,
        cli: WrenCli | None = None,
        schema_reader_factory: Any | None = None,
    ):
        self.store = store or WrenSettingsStore()
        self.model_store = model_store or ModelSettingsStore()
        self.settings = settings or Settings.from_env()
        self.data_root = self.settings.wren_data_dir or Path(__file__).resolve().parents[2] / "data" / "wren"
        self.wren_home = self.settings.wren_home or Path.home() / ".wren"
        self.cli = cli or WrenCli(wren_home=self.wren_home)
        self.project_builder = WrenProjectBuilder(self.data_root)
        self.schema_reader_factory = schema_reader_factory
        self.runtime_manager = runtime_manager or RuntimeManager(
            self.store,
            self.model_store,
            self._build_runtime_snapshot,
        )
        self.query_memory_store: Any | None = None
        self.runtime_manager.runtime_identity_resolver = self._resolve_runtime_identity
        self._operations: set[asyncio.Task[Any]] = set()
        self._initialization_lock = asyncio.Lock()
        self._initialization_attempted = False

    def attach_query_memory_store(self, query_memory_store: Any | None) -> None:
        self.query_memory_store = query_memory_store

    async def activate_query_corpus_revision(
        self, *, data_source_id: str, corpus_revision: int, actor_id: str,
    ) -> Any:
        """Prepare, durably activate, then atomically swap one approved corpus runtime."""
        query_memory_store = self.query_memory_store
        if query_memory_store is None:
            raise WrenSettingsUnavailable("查询语料记忆服务尚未启用。")
        prepared, corpus_generation = await asyncio.to_thread(
            query_memory_store.prepared_revision_for_activation,
            data_source_id=data_source_id,
            target_revision=corpus_revision,
            actor_id=actor_id,
        )
        source = self.store.get_data_source(data_source_id)
        if (
            not source.enabled or source.runtime_status != "ready"
            or source.active_revision_id != prepared.wren_revision_id
        ):
            raise WrenConfigurationError(
                "查询语料不属于当前可用的活动 Wren 版本。",
                code="SOURCE_GENERATION_STALE",
            )
        revision = self.store.get_revision(data_source_id, prepared.wren_revision_id)
        if not revision.project_dir or revision.status != "active":
            raise WrenConfigurationError(
                "活动 Wren 版本尚未准备好，不能激活查询语料。",
                code="DATA_SOURCE_UNAVAILABLE",
            )
        semantic_digest = compute_semantic_digest(
            Path(revision.project_dir), source.connector_type
        )
        if semantic_digest != prepared.mdl_digest or semantic_digest != revision.mdl_digest:
            raise WrenConfigurationError(
                "查询语料与当前 MDL 不匹配，请重新审核示例。",
                code="SOURCE_GENERATION_STALE",
            )
        if prepared.status == "active":
            active_identity = query_memory_store.active_revision_identity(
                data_source_id=data_source_id,
                connector_type=source.connector_type,
                wren_revision_id=prepared.wren_revision_id,
                mdl_digest=semantic_digest,
            )
            if active_identity != query_memory_store.revision_identity(prepared):
                raise WrenSettingsUnavailable("活动查询语料无法验证。")
            return prepared
        operation_state = await asyncio.to_thread(
            self.store.source_operation_state, data_source_id
        )
        operation = await asyncio.to_thread(
            self.store.begin_source_operation,
            data_source_id,
            "query_corpus_activate",
            base_revision_id=prepared.wren_revision_id,
            target_revision_id=f"query_corpus_{corpus_revision}",
            expected_generation=operation_state["generation"],
            require_no_draft=True,
            base_mdl_digest=semantic_digest,
            actor_id=actor_id,
            payload={
                "corpus_revision": corpus_revision,
                "content_hash": prepared.content_hash,
                "corpus_generation": corpus_generation,
            },
        )
        try:
            await asyncio.to_thread(
                self.store.update_operation,
                operation["id"], status="running", phase="preparing_runtime",
            )
            identity_override = (
                semantic_digest, query_memory_store.revision_identity(prepared)
            )
            candidates = await self.runtime_manager.prepare_source_revision(
                data_source_id,
                prepared.wren_revision_id,
                identity_override=identity_override,
                candidate_builder=lambda active_source, active_revision, model: (
                    self._build_runtime_snapshot(
                        active_source,
                        active_revision,
                        model,
                        query_corpus_revision=prepared,
                    )
                ),
            )
            activated = await asyncio.to_thread(
                query_memory_store.activate_prepared_revision,
                data_source_id=data_source_id,
                target_revision=corpus_revision,
                expected_generation=corpus_generation,
                wren_revision_id=prepared.wren_revision_id,
                mdl_digest=semantic_digest,
                actor_id=actor_id,
                wren_operation_id=operation["id"],
                wren_generation=operation["base_generation"],
            )
            await self.runtime_manager.activate_source_revision(
                data_source_id, prepared.wren_revision_id, candidates
            )
            await asyncio.to_thread(
                self.store.update_operation,
                operation["id"], status="running", phase="runtime_activated",
            )
            await asyncio.to_thread(self.store.complete_source_operation, operation["id"])
            return activated
        except BaseException as exc:
            try:
                current = await asyncio.shield(
                    asyncio.to_thread(self.store.get_operation, operation["id"])
                )
                if current["status"] == "running":
                    if current.get("activated_generation") is None:
                        await asyncio.shield(asyncio.to_thread(
                            self.store.fail_source_operation,
                            operation["id"],
                            error_code=(
                                getattr(exc, "code", None)
                                or "QUERY_CORPUS_ACTIVATION_FAILED"
                            ),
                            message="查询语料运行时准备失败，旧活动版本仍可用。",
                        ))
                    else:
                        await asyncio.shield(asyncio.to_thread(
                            self.store.update_operation,
                            operation["id"],
                            status="running",
                            phase="recovery_required",
                            error_code="SOURCE_RECOVERY_REQUIRED",
                            message="查询语料版本已持久化，等待运行时恢复。",
                        ))
            except BaseException:
                # Preserve the durable source lock if the final state is uncertain.
                pass
            raise

    def _resolve_runtime_identity(self, source: Any, revision: Any) -> tuple[str, str]:
        query_memory_store = self.query_memory_store
        if not revision.project_dir and query_memory_store is not None:
            raise WrenConfigurationError(
                "活动 Wren 版本缺少项目目录，无法解析记忆版本。",
                code="DATA_SOURCE_UNAVAILABLE",
            )
        semantic_digest = (
            compute_semantic_digest(Path(revision.project_dir), source.connector_type)
            if revision.project_dir
            else str(getattr(revision, "mdl_digest", None) or "")
        )
        if query_memory_store is None:
            return semantic_digest, "none"
        corpus_revision = query_memory_store.active_revision_identity(
            data_source_id=source.id,
            connector_type=source.connector_type,
            wren_revision_id=revision.id,
            mdl_digest=semantic_digest,
        )
        return semantic_digest, corpus_revision

    async def initialize(self) -> None:
        """Import the legacy environment-backed Wren project once, if present."""
        async with self._initialization_lock:
            if self._initialization_attempted:
                return
            self._initialization_attempted = True
            if self.store.list_data_sources():
                self.store.set_migration_status("complete")
                return
            if not self.settings.wren_project_dir or not self.settings.wren_profile:
                self.store.set_migration_status("not_configured")
                return
            try:
                await asyncio.to_thread(self._migrate_legacy_project)
                self.store.set_migration_status("complete")
            except Exception:
                # Keep the old environment-backed runtime usable. The status is
                # visible to the settings UI, while diagnostics stay server-side.
                self.store.set_migration_status("failed")

    @staticmethod
    def _resolve_legacy_value(value: Any) -> str:
        if value is None:
            return ""
        text = str(value)
        match = re.fullmatch(r"\$\{([A-Z][A-Z0-9_]*)\}", text)
        if match:
            resolved = os.environ.get(match.group(1))
            if resolved is None:
                raise WrenConfigurationError("旧 Wren profile 引用了未配置的环境变量。")
            return resolved
        return text

    def _legacy_connection(self) -> tuple[str, dict[str, Any], dict[str, str]]:
        legacy_home = self.settings.legacy_wren_home or Path.home() / ".wren"
        profiles_path = legacy_home / "profiles.yml"
        try:
            profile_store = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise WrenConfigurationError("未能读取旧 Wren profile。") from exc
        profiles = profile_store.get("profiles") if isinstance(profile_store, dict) else None
        profile = profiles.get(self.settings.wren_profile) if isinstance(profiles, dict) else None
        if not isinstance(profile, dict):
            raise WrenConfigurationError("找不到环境变量指定的旧 Wren profile。")
        if isinstance(profile.get("properties"), dict):
            profile = {**profile, **profile["properties"]}
        datasource = str(profile.get("datasource", "")).lower()
        if datasource not in SUPPORTED_DATABASE_CONNECTORS:
            raise WrenConfigurationError("旧 Wren profile 的数据库/数仓类型不在当前支持范围内。")
        raw_connection: dict[str, Any] = {}
        for field in connector_fields(datasource, profile):
            value = profile.get(field.alias or field.name, profile.get(field.name))
            if value is None:
                continue
            value = self._resolve_legacy_value(value)
            if field.name == "ssl_ca" and isinstance(value, str):
                ca_path = Path(value).expanduser()
                if ca_path.is_file():
                    value = ca_path.read_text(encoding="utf-8")
            raw_connection[field.name] = value
        try:
            connection, secrets = normalize_connection(datasource, raw_connection)
        except Exception as exc:
            raise WrenConfigurationError("旧 Wren 连接配置无法迁移，请检查连接字段。") from exc
        return datasource, connection, secrets

    def _legacy_semantic(self, project_dir: Path) -> dict[str, Any]:
        models: list[dict[str, Any]] = []
        tables: list[str] = []
        for metadata_path in sorted((project_dir / "models").glob("*/metadata.yml")):
            metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
            table_ref = metadata.get("table_reference", {})
            table = str(table_ref.get("table", ""))
            if not table:
                continue
            tables.append(table)
            columns = [
                {
                    "name": column.get("name", ""),
                    "description": (column.get("properties") or {}).get("description", ""),
                    "hidden": bool(column.get("is_hidden", (column.get("properties") or {}).get("hidden", False))),
                    "primary_key": bool(column.get("is_primary_key", False)),
                }
                for column in metadata.get("columns", [])
            ]
            models.append({
                "table": table,
                "name": metadata.get("name", metadata_path.parent.name),
                "description": (metadata.get("properties") or {}).get("description", ""),
                "columns": columns,
            })
        relationships = []
        relationships_path = project_dir / "relationships.yml"
        if relationships_path.is_file():
            content = yaml.safe_load(relationships_path.read_text(encoding="utf-8")) or {}
            for relationship in content.get("relationships", []):
                pair = relationship.get("models", [])
                if len(pair) == 2:
                    relationships.append({
                        "name": relationship.get("name", ""),
                        "left_model": pair[0],
                        "right_model": pair[1],
                        "join_type": relationship.get("join_type", "many_to_one"),
                        "condition": relationship.get("condition", ""),
                    })
        rules = []
        for rule_path in sorted((project_dir / "knowledge" / "rules").glob("*.md")):
            rules.append({"name": rule_path.stem, "content": rule_path.read_text(encoding="utf-8")})
        views = []
        for metadata_path in sorted((project_dir / "views").glob("*/metadata.yml")):
            sql_path = metadata_path.parent / "sql.yml"
            if not sql_path.is_file():
                continue
            metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8")) or {}
            sql = yaml.safe_load(sql_path.read_text(encoding="utf-8")) or {}
            views.append({
                "name": metadata.get("name", metadata_path.parent.name),
                "description": (metadata.get("properties") or {}).get("description", ""),
                "sql": sql.get("statement", ""),
            })
        return {"tables": tables, "models": models, "relationships": relationships, "rules": rules, "views": views}

    def _migrate_legacy_project(self) -> None:
        source_project = self.settings.wren_project_dir
        if source_project is None:
            return
        source_project = source_project.expanduser().resolve()
        if not (source_project / "wren_project.yml").is_file() or not (source_project / "target" / "mdl.json").is_file():
            raise WrenConfigurationError("旧 Wren 项目未完成构建。")
        manifest = yaml.safe_load((source_project / "wren_project.yml").read_text(encoding="utf-8")) or {}
        connector_type, connection, secrets = self._legacy_connection()
        if secrets:
            self.store._cipher()
        display_name = str(manifest.get("name") or "默认数据源")[:120]
        source_id, revision_id = "ds_legacy_wren", "rev_legacy_wren"
        profile_name = "askdb_legacy_wren"
        profile_fields, secret_values = make_profile_fields(
            connector_type,
            connection,
            secrets,
            self._token(source_id),
            self._token(revision_id),
        )
        os.environ.update(secret_values)

        target = self.data_root / "sources" / source_id / "revisions" / revision_id
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copytree(
            source_project,
            target,
            ignore=shutil.ignore_patterns(".env", ".wren", "__pycache__", "*.pyc"),
        )
        target_manifest = yaml.safe_load((target / "wren_project.yml").read_text(encoding="utf-8")) or {}
        target_manifest["profile"] = profile_name
        (target / "wren_project.yml").write_text(
            yaml.safe_dump(target_manifest, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        redactions = list(secrets.values())
        profile_options: dict[str, Any] = {
            "secret_values": secret_values,
            "secrets_to_redact": redactions,
        }
        if isinstance(self.cli, WrenCli):
            profile_options["sensitive_field_names"] = {
                field.alias or field.name
                for field in connector_fields(connector_type, connection)
                if is_sensitive_field(field)
            }
        self.cli.add_profile(profile_name, profile_fields, **profile_options)
        self.cli.validate(target, secrets_to_redact=redactions)
        self.cli.build(target, secrets_to_redact=redactions)
        config = connection | self._legacy_semantic(source_project)
        source = self.store.create_data_source(
            display_name,
            connector_type,
            config,
            secrets,
            source_id=source_id,
            revision_id=revision_id,
        )
        self.store.update_revision_artifacts(
            source.id,
            revision_id,
            project_dir=target,
            profile_name=profile_name,
            mdl_digest=compute_semantic_digest(target, connector_type),
        )
        self.store.activate_revision(source.id, revision_id)
        self.store.set_default(source.id)

    @staticmethod
    def _clean_connection(
        connection: dict[str, Any],
        connector_type: str = "mysql",
    ) -> tuple[dict[str, Any], dict[str, str]]:
        try:
            return normalize_connection(connector_type, connection)
        except Exception as exc:
            if isinstance(exc, WrenConfigurationError):
                raise
            raise WrenConfigurationError("连接配置与所选 Wren 数据源类型不匹配。") from exc

    @staticmethod
    def _clean_semantic(semantic: dict[str, Any] | None) -> dict[str, Any]:
        semantic = semantic or {}
        if not isinstance(semantic, dict):
            raise WrenConfigurationError("语义模型配置格式无效。")
        allowed = {"tables", "models", "relationships", "rules", "views"}
        if set(semantic) - allowed:
            raise WrenConfigurationError("语义模型配置包含不支持的字段。")
        return semantic

    def create_source(
        self,
        display_name: str,
        connection: dict[str, Any],
        semantic: dict[str, Any] | None = None,
        connector_type: str = "mysql",
    ) -> dict[str, Any]:
        config, secrets = self._clean_connection(connection, connector_type)
        config |= self._clean_semantic(semantic)
        source = self.store.create_data_source(display_name, connector_type, config, secrets)
        return self.store.source_detail(source.id)

    def update_draft(
        self,
        source_id: str,
        display_name: str,
        connection: dict[str, Any],
        semantic: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source = self.store.get_data_source(source_id)
        previous_revision = source.draft_revision_id or source.active_revision_id
        previous_secrets = (
            self.store.get_secrets(source_id, previous_revision)
            if previous_revision else {}
        )
        accepted_secret_names = {
            field.name for field in connector_fields(source.connector_type, connection)
            if is_sensitive_field(field)
        }
        validation_fields = {
            key: value for key, value in previous_secrets.items()
            if key in accepted_secret_names
        } | connection
        config, supplied_secrets = self._clean_connection(validation_fields, source.connector_type)
        config |= self._clean_semantic(semantic)
        self.store.save_draft(
            source_id, config, supplied_secrets, display_name=display_name
        )
        return self.store.source_detail(source_id)

    def detail(self, source_id: str) -> dict[str, Any]:
        return self.store.source_detail(source_id)

    def revision_detail(self, source_id: str, revision_id: str) -> dict[str, Any]:
        source = self.store.get_data_source(source_id)
        revision = self.store.get_revision(source_id, revision_id)
        return {
            "data_source": {
                "id": source.id,
                "display_name": source.display_name,
                "connector_type": source.connector_type,
            },
            "revision": {
                "id": revision.id,
                "status": revision.status,
                "error_code": revision.error_code,
                "mdl_digest": revision.mdl_digest,
                "created_at": revision.created_at,
                "updated_at": revision.updated_at,
            },
            # Connection secrets are stored separately and never included in revision.config.
            "config": revision.config,
        }

    def catalog(self) -> dict[str, Any]:
        return self.store.public_catalog()

    def _source_and_draft(self, source_id: str):
        source = self.store.get_data_source(source_id)
        revision_id = source.draft_revision_id or source.active_revision_id
        if not revision_id:
            raise WrenConfigurationError("数据源尚无可测试的配置。")
        revision = self.store.get_revision(source_id, revision_id)
        secrets = self.store.get_secrets(source_id, revision_id)
        return source, revision, secrets

    def _reader(
        self,
        source_id: str,
        revision_id: str,
        connector_type: str,
        config: dict[str, Any],
        secrets: dict[str, str],
    ):
        reader_config = dict(config)
        if self.schema_reader_factory is not None:
            return self.schema_reader_factory(reader_config, secrets)
        return SchemaReaderRegistry.create(connector_type, reader_config, secrets)

    def _materialize_ca(self, source_id: str, revision_id: str, contents: str | None) -> Path | None:
        if not contents:
            return None
        cert_dir = self.wren_home / "certs"
        cert_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        filename = f"{self._token(source_id)}_{self._token(revision_id)}.pem"
        path = (cert_dir / filename).resolve()
        if cert_dir.resolve() not in path.parents:
            raise WrenConfigurationError("CA 证书路径无效。")
        path.write_text(contents, encoding="utf-8")
        os.chmod(path, 0o600)
        return path

    @staticmethod
    def _token(value: str) -> str:
        token = re.sub(r"[^A-Za-z0-9]", "", value).upper()
        return token[:32] or uuid.uuid4().hex[:16].upper()

    def _install_profile_secrets(
        self,
        source_id: str,
        revision_id: str,
        secrets: dict[str, str],
    ) -> None:
        source_token = self._token(source_id)
        revision_token = self._token(revision_id)
        for field_name, value in secrets.items():
            env_name = secret_environment_name(source_token, revision_token, field_name)
            os.environ[env_name] = value

    def test_connection(self, source_id: str) -> dict[str, Any]:
        source, revision, secrets = self._source_and_draft(source_id)
        self._reader(source.id, revision.id, source.connector_type, revision.config, secrets).test_connection()
        return {"ok": True, "data_source_id": source_id, "revision_id": revision.id}

    def refresh_schema(self, source_id: str) -> dict[str, Any]:
        source, revision, secrets = self._source_and_draft(source_id)
        reader = self._reader(
            source.id, revision.id, source.connector_type, revision.config, secrets
        )
        tables = reader.introspect()
        return {
            "data_source_id": source_id,
            "revision_id": revision.id,
            "warnings": list(getattr(reader, "warnings", [])),
            "tables": [
                {
                    "id": str(getattr(table, "id", "") or ".".join(
                        part for part in (
                            str(getattr(table, "catalog", "") or ""),
                            str(getattr(table, "schema", "") or ""),
                            str(table.name),
                        ) if part
                    )),
                    "name": table.name,
                    "catalog": str(getattr(table, "catalog", "") or ""),
                    "schema": str(getattr(table, "schema", "") or ""),
                    "description": str(getattr(table, "description", "") or ""),
                    "columns": [
                        {
                            "name": column.name,
                            "type": column.type,
                            "nullable": column.nullable,
                            "primary_key": column.primary_key,
                            "description": str(getattr(column, "description", "") or ""),
                        }
                        for column in table.columns
                    ],
                    "foreign_keys": [
                        {
                            "name": foreign_key.name,
                            "column": foreign_key.column,
                            "referenced_table": ".".join(part for part in (
                                str(getattr(foreign_key, "referenced_catalog", "") or ""),
                                str(getattr(foreign_key, "referenced_schema", "") or getattr(table, "schema", "") or ""),
                                foreign_key.referenced_table,
                            ) if part),
                            "referenced_column": foreign_key.referenced_column,
                        }
                        for foreign_key in table.foreign_keys
                    ],
                }
                for table in tables
            ],
        }

    async def start_apply(self, source_id: str) -> dict[str, Any]:
        source, revision, _secrets = self._source_and_draft(source_id)
        if not source.enabled:
            raise WrenConfigurationError("请先启用数据源后再应用。", code="DATA_SOURCE_UNAVAILABLE")
        generation = self.store.source_operation_state(source_id)["generation"]
        operation = self.store.begin_source_operation(
            source_id,
            "settings_apply",
            base_revision_id=source.active_revision_id,
            target_revision_id=revision.id,
            expected_generation=generation,
            expected_draft_revision_id=revision.id,
        )
        task = asyncio.create_task(self._apply(operation["id"], source, revision))
        self._operations.add(task)
        task.add_done_callback(self._operations.discard)
        return operation

    async def _apply(self, operation_id: str, source: WrenDataSource, revision: Any) -> None:
        secrets = self.store.get_secrets(source.id, revision.id)
        profile_name = f"askdb_{self._token(source.id).lower()}_{self._token(revision.id).lower()}"
        redactions = list(secrets.values())
        try:
            self.store.update_operation(operation_id, status="running", phase="testing_connection")
            reader = self._reader(
                source.id, revision.id, source.connector_type, revision.config, secrets
            )
            await asyncio.to_thread(reader.test_connection)
            self.store.update_operation(operation_id, status="running", phase="introspecting")
            schema = await asyncio.to_thread(reader.introspect)

            profile_fields, secret_values = make_profile_fields(
                source.connector_type,
                revision.config,
                secrets,
                self._token(source.id),
                self._token(revision.id),
            )
            os.environ.update(secret_values)
            self.store.update_operation(operation_id, status="running", phase="validating")
            profile_options: dict[str, Any] = {
                "secret_values": secret_values,
                "secrets_to_redact": redactions,
            }
            if isinstance(self.cli, WrenCli):
                profile_options["sensitive_field_names"] = {
                    field.alias or field.name
                    for field in connector_fields(source.connector_type, revision.config)
                    if is_sensitive_field(field)
                }
            await asyncio.to_thread(
                self.cli.add_profile,
                profile_name,
                profile_fields,
                **profile_options,
            )
            project_dir = await asyncio.to_thread(
                self.project_builder.build,
                source.id,
                revision.id,
                profile_name,
                source.connector_type,
                revision.config,
                schema,
            )
            self.store.update_revision_artifacts(
                source.id,
                revision.id,
                project_dir=project_dir,
                profile_name=profile_name,
            )
            await asyncio.to_thread(self.cli.validate, project_dir, secrets_to_redact=redactions)
            self.store.update_operation(operation_id, status="running", phase="building")
            await asyncio.to_thread(self.cli.build, project_dir, secrets_to_redact=redactions)
            semantic_digest = compute_semantic_digest(project_dir, source.connector_type)
            self.store.update_revision_artifacts(
                source.id,
                revision.id,
                project_dir=project_dir,
                profile_name=profile_name,
                mdl_digest=semantic_digest,
            )
            self.store.update_revision_status(source.id, revision.id, "building")
            self.store.record_operation_target_digest(
                operation_id, revision.id, semantic_digest
            )
            self.store.update_operation(operation_id, status="running", phase="initializing_runtime")
            candidates = await self.runtime_manager.prepare_source_revision(source.id, revision.id)
            self.store.activate_revision_for_operation(
                operation_id, source_id=source.id, target_revision_id=revision.id
            )
            await self.runtime_manager.activate_source_revision(source.id, revision.id, candidates)
            self.store.update_operation(operation_id, status="running", phase="runtime_activated")
            self.store.complete_source_operation(operation_id)
        except Exception as exc:
            code = getattr(exc, "code", None) or "WREN_RUNTIME_INIT_FAILED"
            if code not in {
                "WREN_CONNECTION_FAILED", "WREN_SCHEMA_DISCOVERY_FAILED", "WREN_VALIDATION_FAILED",
                "WREN_BUILD_FAILED", "WREN_CONFIGURATION_INVALID", "DATA_SOURCE_UNAVAILABLE",
            }:
                code = "WREN_RUNTIME_INIT_FAILED"
            failure_phase = "unknown"
            try:
                failure_phase = str(self.store.get_operation(operation_id).get("phase") or "unknown")
            except Exception:
                pass
            location = "unknown"
            try:
                frames = traceback.extract_tb(exc.__traceback__)
                if frames:
                    frame = frames[-1]
                    location = f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
            except Exception:
                pass
            logger.error(
                "Wren apply failed operation_id=%s source_id=%s revision_id=%s phase=%s "
                "error_code=%s exception_type=%s location=%s",
                operation_id,
                source.id,
                revision.id,
                failure_phase,
                code,
                type(exc).__name__,
                location,
            )
            safe_message = str(exc) if isinstance(exc, WrenConfigurationError) else "Wren 版本应用失败，请修正配置后重试。"
            try:
                self.store.fail_source_operation(
                    operation_id, error_code=code, message=safe_message
                )
            except WrenConfigurationError as recovery_error:
                if recovery_error.code == "SOURCE_RECOVERY_REQUIRED":
                    try:
                        self.store.update_operation(
                            operation_id,
                            status="running",
                            phase="recovery_required",
                            error_code="SOURCE_RECOVERY_REQUIRED",
                            message="活动版本已持久化，等待启动恢复运行时。",
                        )
                    except Exception:
                        pass
            except Exception:
                # Keep the durable operation locked if its outcome cannot be established.
                pass

    @staticmethod
    def _business_rule_name(rule_id: str) -> str:
        if len(rule_id) != 32 or any(character not in "0123456789abcdef" for character in rule_id):
            raise WrenConfigurationError("业务规则标识无效。")
        return f"askdb_br_{rule_id}"

    @staticmethod
    def _business_rule_markdown(candidate: Any) -> str:
        if not candidate.term or not candidate.definition:
            raise WrenConfigurationError("已审核规则内容不完整。", code="SOURCE_GENERATION_STALE")
        lines = [f"# {candidate.term}", "", candidate.definition.strip()]
        if candidate.mdl_references:
            lines.extend(["", "## MDL references", *[f"- {value}" for value in candidate.mdl_references]])
        return "\n".join(lines).strip() + "\n"

    def _clone_rule_revision_project(
        self,
        *,
        source: WrenDataSource,
        base_revision: Any,
        target_revision_id: str,
        operation_id: str,
        business_rule_ids: tuple[str, ...],
        candidate: Any | None,
    ) -> tuple[Path, dict[str, Any], str, dict[str, str]]:
        if not base_revision.project_dir or not base_revision.profile_name:
            raise WrenConfigurationError("活动版本没有可复制的 Wren 项目。", code="DATA_SOURCE_UNAVAILABLE")
        base_dir = Path(base_revision.project_dir).expanduser().resolve()
        source_root = (self.data_root / "sources" / source.id).resolve()
        if source_root not in base_dir.parents or not (base_dir / "wren_project.yml").is_file():
            raise WrenConfigurationError("活动 Wren 项目路径无效。", code="DATA_SOURCE_UNAVAILABLE")
        if compute_semantic_digest(base_dir, source.connector_type) != base_revision.mdl_digest:
            raise WrenConfigurationError(
                "活动 Wren 项目与记录的 MDL digest 不一致。",
                code="SOURCE_GENERATION_STALE",
            )

        target_dir = (source_root / "revisions" / target_revision_id).resolve()
        if source_root not in target_dir.parents or target_dir.exists():
            raise WrenConfigurationError("目标 Wren 项目路径无效。")

        config = dict(base_revision.config)
        rules = list(config.get("rules", []))
        names_to_remove = {self._business_rule_name(rule_id) for rule_id in business_rule_ids}
        if candidate is not None:
            name = self._business_rule_name(candidate.business_rule_id)
            if any(
                isinstance(rule, dict) and str(rule.get("name", "")) == name
                for rule in rules
            ):
                raise WrenConfigurationError("此规则已存在于当前 MDL。", code="SOURCE_GENERATION_STALE")
            rules.append({"name": name, "content": self._business_rule_markdown(candidate)})
        else:
            rules = [
                rule for rule in rules
                if not (isinstance(rule, dict) and str(rule.get("name", "")) in names_to_remove)
            ]
        config["rules"] = rules

        secrets = self.store.get_secrets(source.id, base_revision.id)
        profile_name = f"askdb_{self._token(source.id).lower()}_{self._token(target_revision_id).lower()}"
        profile_fields, secret_values = make_profile_fields(
            source.connector_type,
            config,
            secrets,
            self._token(source.id),
            self._token(target_revision_id),
        )
        os.environ.update(secret_values)
        self._install_profile_secrets(source.id, target_revision_id, secrets)

        target_revision = self.store.create_revision_for_operation(
            operation_id,
            config,
            target_revision_id=target_revision_id,
            copy_secrets_from=base_revision.id,
        )
        shutil.copytree(
            base_dir,
            target_dir,
            ignore=shutil.ignore_patterns(".wren", "__pycache__", "*.pyc"),
        )
        manifest_path = target_dir / "wren_project.yml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        if not isinstance(manifest, dict):
            raise WrenConfigurationError("活动 Wren manifest 格式无效。")
        manifest["profile"] = profile_name
        manifest_path.write_text(
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        if compute_semantic_digest(target_dir, source.connector_type) != base_revision.mdl_digest:
            raise WrenConfigurationError(
                "目标版本未能保留发布基线的语义内容。",
                code="SOURCE_GENERATION_STALE",
            )

        rule_dir = (target_dir / "knowledge" / "rules").resolve()
        if target_dir not in rule_dir.parents:
            raise WrenConfigurationError("Wren 规则目录无效。")
        rule_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        for rule_id in business_rule_ids:
            (rule_dir / f"{self._business_rule_name(rule_id)}.md").unlink(missing_ok=True)
        if candidate is not None:
            rule_file = rule_dir / f"{self._business_rule_name(candidate.business_rule_id)}.md"
            if rule_file.exists():
                raise WrenConfigurationError("此规则已存在于当前 Wren 项目。", code="SOURCE_GENERATION_STALE")
            rule_file.write_text(self._business_rule_markdown(candidate), encoding="utf-8")
            os.chmod(rule_file, 0o600)

        redactions = list(secrets.values())
        profile_options: dict[str, Any] = {
            "secret_values": secret_values,
            "secrets_to_redact": redactions,
        }
        if isinstance(self.cli, WrenCli):
            profile_options["sensitive_field_names"] = {
                field.alias or field.name
                for field in connector_fields(source.connector_type, config)
                if is_sensitive_field(field)
            }
        self.cli.add_profile(profile_name, profile_fields, **profile_options)
        self.cli.validate(target_dir, secrets_to_redact=redactions)
        self.cli.build(target_dir, secrets_to_redact=redactions)
        digest = compute_semantic_digest(target_dir, source.connector_type)
        self.store.update_revision_artifacts(
            source.id,
            target_revision_id,
            project_dir=target_dir,
            profile_name=profile_name,
            mdl_digest=digest,
        )
        self.store.update_revision_status(source.id, target_revision_id, "building")
        self.store.record_operation_target_digest(operation_id, target_revision_id, digest)
        return target_dir, config, profile_name, secrets

    def _launch_operation(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._operations.add(task)
        task.add_done_callback(self._operations.discard)

    def start_business_rule_publication(
        self,
        business_rule_store: Any,
        *,
        business_rule_id: str,
        actor_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        candidate = business_rule_store.get_candidate(business_rule_id)
        if not candidate.term or not candidate.definition:
            raise WrenConfigurationError("已审核规则内容不完整。", code="SOURCE_GENERATION_STALE")
        source = self.store.get_data_source(candidate.data_source_id)
        base = self.store.get_revision(candidate.data_source_id, source.active_revision_id or "")
        if (
            candidate.review_status.value != "approved"
            or candidate.base_wren_revision_id != base.id
            or candidate.base_mdl_digest != base.mdl_digest
        ):
            raise WrenConfigurationError("MDL 已变化，请重新审核候选。", code="SOURCE_GENERATION_STALE")
        target_id = f"rev_{uuid.uuid4().hex}"
        generation = self.store.source_operation_state(source.id)["generation"]
        operation = self.store.begin_source_operation(
            source.id,
            "business_rule_publish",
            base_revision_id=base.id,
            target_revision_id=target_id,
            expected_generation=generation,
            require_no_draft=True,
            base_mdl_digest=base.mdl_digest,
            actor_id=actor_id,
            payload={"business_rule_id": business_rule_id},
        )
        try:
            publishing = business_rule_store.mark_publishing(
                business_rule_id,
                actor_id=actor_id,
                expected_version=expected_version,
                base_wren_revision_id=base.id,
                base_mdl_digest=base.mdl_digest or "",
            )
        except Exception:
            self.store.fail_source_operation(
                operation["id"],
                error_code="BUSINESS_RULE_CANDIDATE_CHANGED",
                message="候选状态已变化，未开始 Wren 发布。",
            )
            raise
        self._launch_operation(
            self._publish_business_rule(operation["id"], publishing, business_rule_store)
        )
        return {"operation": operation, "candidate": publishing}

    async def _publish_business_rule(
        self, operation_id: str, candidate: Any, business_rule_store: Any
    ) -> None:
        operation = self.store.get_operation(operation_id)
        source = self.store.get_data_source(operation["data_source_id"])
        base = self.store.get_revision(source.id, operation["base_revision_id"])
        target_id = operation["target_revision_id"]
        try:
            self.store.update_operation(operation_id, status="running", phase="building_target")
            await asyncio.to_thread(
                self._clone_rule_revision_project,
                source=source,
                base_revision=base,
                target_revision_id=target_id,
                operation_id=operation_id,
                business_rule_ids=(candidate.business_rule_id,),
                candidate=candidate,
            )
            self.store.update_operation(operation_id, status="running", phase="initializing_runtime")
            snapshots = (
                await self.runtime_manager.prepare_source_revision(source.id, target_id)
                if source.enabled else None
            )
            self.store.activate_revision_for_operation(
                operation_id, source_id=source.id, target_revision_id=target_id
            )
            if snapshots is not None:
                await self.runtime_manager.activate_source_revision(source.id, target_id, snapshots)
            self.store.update_operation(operation_id, status="running", phase="runtime_activated")
            try:
                business_rule_store.mark_published(
                    candidate.business_rule_id,
                    data_source_id=source.id,
                    operation_id=operation_id,
                    base_wren_revision_id=operation["base_revision_id"],
                    base_mdl_digest=operation["base_mdl_digest"] or "",
                    wren_revision_id=target_id,
                )
            except Exception:
                if not business_rule_store.is_removal_pending(source.id, candidate.business_rule_id):
                    raise
            self.store.complete_source_operation(operation_id)
        except Exception as exc:
            self._record_operation_failure(
                operation_id,
                source_id=source.id,
                candidate_store=business_rule_store,
                business_rule_id=candidate.business_rule_id,
                exc=exc,
            )

    async def _publish_rule_removals(
        self, operation_id: str, business_rule_store: Any
    ) -> None:
        operation = self.store.get_operation(operation_id)
        source = self.store.get_data_source(operation["data_source_id"])
        base = self.store.get_revision(source.id, operation["base_revision_id"])
        rule_ids = tuple(operation["payload"].get("business_rule_ids", ()))
        target_id = operation["target_revision_id"]
        try:
            self.store.update_operation(operation_id, status="running", phase="building_target")
            await asyncio.to_thread(
                self._clone_rule_revision_project,
                source=source,
                base_revision=base,
                target_revision_id=target_id,
                operation_id=operation_id,
                business_rule_ids=rule_ids,
                candidate=None,
            )
            self.store.update_operation(operation_id, status="running", phase="initializing_runtime")
            snapshots = (
                await self.runtime_manager.prepare_source_revision(source.id, target_id)
                if source.enabled else None
            )
            self.store.activate_revision_for_operation(
                operation_id, source_id=source.id, target_revision_id=target_id
            )
            if snapshots is not None:
                await self.runtime_manager.activate_source_revision(source.id, target_id, snapshots)
            self.store.update_operation(operation_id, status="running", phase="runtime_activated")
            business_rule_store.mark_removed(
                data_source_id=source.id,
                business_rule_ids=rule_ids,
                operation_id=operation_id,
                wren_revision_id=target_id,
            )
            self.store.complete_source_operation(operation_id)
        except Exception as exc:
            self._record_operation_failure(operation_id, source_id=source.id, exc=exc)

    def _record_operation_failure(
        self,
        operation_id: str,
        *,
        source_id: str,
        candidate_store: Any | None = None,
        business_rule_id: str | None = None,
        exc: Exception,
    ) -> None:
        code = getattr(exc, "code", None) or "WREN_RUNTIME_INIT_FAILED"
        try:
            operation = self.store.get_operation(operation_id)
            source = self.store.get_data_source(source_id)
            if (
                operation.get("activated_generation") is not None
                and source.active_revision_id == operation.get("target_revision_id")
            ):
                self.store.update_operation(
                    operation_id,
                    status="running",
                    phase="recovery_required",
                    error_code="SOURCE_RECOVERY_REQUIRED",
                    message="活动版本已持久化，等待启动恢复运行时。",
                )
                return
            self.store.fail_source_operation(
                operation_id,
                error_code=code,
                message="Wren 规则发布失败，请稍后重试。",
            )
            if candidate_store is not None and business_rule_id:
                candidate_store.mark_publication_failed(
                    business_rule_id, reason_code="wren_publication_failed"
                )
        except Exception:
            # Leave the source locked if the durable outcome cannot be established.
            logger.error(
                "Wren publication outcome requires recovery operation_id=%s source_id=%s error_type=%s",
                operation_id,
                source_id,
                type(exc).__name__,
            )

    async def sweep_pending_rule_removals(self, business_rule_store: Any) -> int:
        """Start one durable Wren removal operation per source with suppressed rules."""
        started = 0
        for source_id, rule_ids in business_rule_store.list_pending_removals().items():
            try:
                source = self.store.get_data_source(source_id)
                if not source.active_revision_id:
                    continue
                base = self.store.get_revision(source_id, source.active_revision_id)
                if not base.mdl_digest:
                    continue
                target_id = f"rev_{uuid.uuid4().hex}"
                generation = self.store.source_operation_state(source_id)["generation"]
                operation = self.store.begin_source_operation(
                    source_id,
                    "business_rule_remove",
                    base_revision_id=base.id,
                    target_revision_id=target_id,
                    expected_generation=generation,
                    base_mdl_digest=base.mdl_digest,
                    payload={"business_rule_ids": list(rule_ids)},
                )
            except (LookupError, WrenConfigurationError):
                continue
            self._launch_operation(self._publish_rule_removals(operation["id"], business_rule_store))
            started += 1
        return started

    async def recover_source_operations(self, business_rule_store: Any | None = None) -> None:
        """Reconcile durable Wren pointers and runtime snapshots before opening traffic."""
        for operation in self.store.pending_source_operations():
            operation_id = operation["id"]
            source_id = operation["data_source_id"]
            source = self.store.get_data_source(source_id)
            if operation["operation_type"] == "query_corpus_activate":
                query_memory_store = self.query_memory_store
                if query_memory_store is None:
                    raise WrenSettingsUnavailable(
                        "查询语料激活恢复依赖记忆数据库，服务保持关闭。"
                    )
                corpus_revision = operation["payload"].get("corpus_revision")
                content_hash = operation["payload"].get("content_hash")
                if not isinstance(corpus_revision, int) or not isinstance(content_hash, str):
                    raise WrenSettingsUnavailable("查询语料激活操作记录无效，服务保持关闭。")
                if operation.get("activated_generation") is None:
                    if source.active_revision_id != operation["base_revision_id"]:
                        self.store.update_runtime_status(source_id, "unavailable")
                        raise WrenSettingsUnavailable(
                            "查询语料操作与活动 Wren 版本不一致，服务保持关闭。"
                        )
                    self.store.fail_source_operation(
                        operation_id,
                        error_code="OPERATION_INTERRUPTED_BEFORE_ACTIVATION",
                        message="查询语料运行时准备期间中断，可安全重试。",
                    )
                    continue
                expected_identity = f"query-corpus:{corpus_revision}:{content_hash}"
                actual_identity = query_memory_store.active_revision_identity(
                    data_source_id=source_id,
                    connector_type=source.connector_type,
                    wren_revision_id=operation["base_revision_id"],
                    mdl_digest=operation["base_mdl_digest"] or "",
                )
                if (
                    source.active_revision_id != operation["base_revision_id"]
                    or actual_identity != expected_identity
                    or not source.enabled
                ):
                    self.store.update_runtime_status(source_id, "unavailable")
                    raise WrenSettingsUnavailable(
                        "查询语料 generation 与持久状态不一致，服务保持关闭。"
                    )
                snapshots = await self.runtime_manager.prepare_source_revision(
                    source_id, operation["base_revision_id"]
                )
                await self.runtime_manager.activate_source_revision(
                    source_id, operation["base_revision_id"], snapshots
                )
                self.store.update_operation(
                    operation_id, status="running", phase="runtime_activated"
                )
                self.store.complete_source_operation(operation_id)
                continue
            if operation.get("base_generation") is None or not operation.get("target_revision_id"):
                # Operations written by the pre-coordinator implementation lack a
                # durable base/target binding. The current DB active pointer remains
                # authoritative; retire only the stale operation record.
                self.store.update_operation(
                    operation_id,
                    status="failed",
                    phase="legacy_interrupted",
                    error_code="LEGACY_OPERATION_INTERRUPTED",
                    message="旧版本操作已中断，请检查当前活动版本。",
                )
                continue
            if (
                operation.get("activated_generation") is not None
                and source.active_revision_id == operation["target_revision_id"]
            ):
                if operation["operation_type"] in {"business_rule_publish", "business_rule_remove"} and business_rule_store is None:
                    raise WrenSettingsUnavailable("业务规则发布恢复依赖记忆数据库，服务保持关闭。")
                target_id = operation["target_revision_id"]
                if source.enabled:
                    snapshots = await self.runtime_manager.prepare_source_revision(source_id, target_id)
                    await self.runtime_manager.activate_source_revision(source_id, target_id, snapshots)
                self.store.update_operation(operation_id, status="running", phase="runtime_activated")
                if operation["operation_type"] == "business_rule_publish":
                    rule_id = operation["payload"].get("business_rule_id")
                    if not isinstance(rule_id, str):
                        raise WrenSettingsUnavailable("业务规则发布操作记录无效，服务保持关闭。")
                    if not business_rule_store.is_removal_pending(source_id, rule_id):
                        business_rule_store.mark_published(
                            rule_id,
                            data_source_id=source_id,
                            operation_id=operation_id,
                            base_wren_revision_id=operation["base_revision_id"],
                            base_mdl_digest=operation["base_mdl_digest"] or "",
                            wren_revision_id=target_id,
                        )
                elif operation["operation_type"] == "business_rule_remove":
                    business_rule_store.mark_removed(
                        data_source_id=source_id,
                        business_rule_ids=tuple(operation["payload"].get("business_rule_ids", ())),
                        operation_id=operation_id,
                        wren_revision_id=target_id,
                    )
                self.store.complete_source_operation(operation_id)
            elif source.active_revision_id == operation["base_revision_id"]:
                self.store.fail_source_operation(
                    operation_id,
                    error_code="OPERATION_INTERRUPTED_BEFORE_ACTIVATION",
                    message="Wren 操作在活动切换前中断，可安全重试。",
                )
                if operation["operation_type"] == "business_rule_publish" and business_rule_store is not None:
                    business_rule_store.mark_publication_failed(
                        operation["payload"].get("business_rule_id", ""),
                        reason_code="publisher_restart_before_activation",
                    )
            else:
                self.store.update_runtime_status(source_id, "unavailable")
                raise WrenSettingsUnavailable("Wren 活动版本与未完成操作不一致，服务保持关闭。")

    async def prune_expired_wren_revisions(self, *, limit: int = 500) -> int:
        """Remove 30-day superseded Wren artifacts after runtime leases drain."""
        cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        candidates = self.store.list_revision_cleanup_candidates(cutoff=cutoff, limit=limit)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in candidates:
            grouped.setdefault(item["source_id"], []).append(item)
        cleaned = 0
        for source_id, items in grouped.items():
            try:
                source = self.store.get_data_source(source_id)
                if not source.active_revision_id:
                    continue
                generation = self.store.source_operation_state(source_id)["generation"]
                revision_ids = tuple(item["id"] for item in items)
                operation = self.store.begin_source_operation(
                    source_id,
                    "revision_cleanup",
                    base_revision_id=source.active_revision_id,
                    target_revision_id=source.active_revision_id,
                    expected_generation=generation,
                    payload={"revision_ids": list(revision_ids)},
                )
            except (LookupError, WrenConfigurationError):
                continue
            try:
                marked = self.store.mark_revision_cleanup_pending(
                    operation["id"], revision_ids=revision_ids, cutoff=cutoff
                )
                revisions_root = (self.data_root / "sources" / source_id / "revisions").resolve()
                for revision_id in marked:
                    if await self.runtime_manager.has_in_flight_revision(source_id, revision_id):
                        continue
                    revision = self.store.get_revision(source_id, revision_id)
                    if revision.project_dir:
                        project_dir = Path(revision.project_dir).expanduser().resolve()
                        if revisions_root not in project_dir.parents:
                            raise WrenSettingsUnavailable("历史 Wren 项目路径越界，清理已停止。")
                        if project_dir.exists():
                            await asyncio.to_thread(shutil.rmtree, project_dir)
                    self.store.finalize_revision_cleanup(operation["id"], revision_id)
                    cleaned += 1
                self.store.complete_maintenance_operation(operation["id"])
            except Exception as exc:
                try:
                    self.store.fail_source_operation(
                        operation["id"],
                        error_code="REVISION_CLEANUP_FAILED",
                        message="历史 Wren 版本清理失败，将在下次维护时重试。",
                    )
                except Exception:
                    pass
                logger.error(
                    "Wren revision cleanup deferred source_id=%s error_type=%s",
                    source_id,
                    type(exc).__name__,
                )
        return cleaned

    async def run_revision_retention_sweeper(self, *, interval_seconds: int = 3600) -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                await self.prune_expired_wren_revisions()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Wren revision retention sweep failed (%s)", type(exc).__name__)

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        return self.store.get_operation(operation_id)

    async def rollback(self, source_id: str, revision_id: str) -> dict[str, Any]:
        source = self.store.get_data_source(source_id)
        revision = self.store.get_revision(source_id, revision_id)
        if revision.status not in {"active", "retired"} or not revision.project_dir or not revision.profile_name:
            raise WrenConfigurationError("该版本尚未成功构建，无法回滚。")
        target_digest = compute_semantic_digest(
            Path(revision.project_dir), source.connector_type
        )
        if revision.mdl_digest != target_digest:
            revision = self.store.update_revision_artifacts(
                source_id,
                revision_id,
                project_dir=Path(revision.project_dir),
                profile_name=revision.profile_name,
                mdl_digest=target_digest,
            )
        generation = self.store.source_operation_state(source_id)["generation"]
        operation = self.store.begin_source_operation(
            source_id,
            "rollback",
            base_revision_id=source.active_revision_id,
            target_revision_id=revision_id,
            expected_generation=generation,
            require_no_draft=True,
        )
        try:
            secrets = self.store.get_secrets(source_id, revision_id)
            self._install_profile_secrets(source_id, revision_id, secrets)
            self.store.record_operation_target_digest(operation["id"], revision_id, target_digest)
            candidates = await self.runtime_manager.prepare_source_revision(source_id, revision_id)
            self.store.activate_revision_for_operation(
                operation["id"], source_id=source_id, target_revision_id=revision_id
            )
            await self.runtime_manager.activate_source_revision(source_id, revision_id, candidates)
            self.store.update_operation(operation["id"], status="running", phase="runtime_activated")
            self.store.complete_source_operation(operation["id"])
        except Exception as exc:
            try:
                self.store.fail_source_operation(
                    operation["id"],
                    error_code=getattr(exc, "code", None) or "WREN_RUNTIME_INIT_FAILED",
                    message="Wren 版本回滚失败，请稍后重试。",
                )
            except WrenConfigurationError as recovery_error:
                if recovery_error.code == "SOURCE_RECOVERY_REQUIRED":
                    self.store.update_operation(
                        operation["id"], status="running", phase="recovery_required",
                        error_code="SOURCE_RECOVERY_REQUIRED",
                        message="活动版本已持久化，等待启动恢复运行时。",
                    )
            raise
        return self.store.source_detail(source_id)

    def deactivate(self, source_id: str) -> dict[str, Any]:
        source = self.store.set_enabled(source_id, False)
        self.store.update_runtime_status(source_id, "disabled")
        return self.store.source_detail(source.id)

    def enable(self, source_id: str) -> dict[str, Any]:
        source = self.store.set_enabled(source_id, True)
        if source.active_revision_id:
            self.store.update_runtime_status(source_id, "ready")
        return self.store.source_detail(source.id)

    def set_default(self, source_id: str | None) -> dict[str, Any]:
        return {"default_data_source_id": self.store.set_default(source_id)}

    def _build_runtime_snapshot(
        self, source: Any, revision: Any, model: Any, *,
        query_corpus_revision: Any | None = None,
    ) -> RuntimeSnapshot:
        if not revision.project_dir or not revision.profile_name:
            raise WrenConfigurationError("数据源版本尚未生成 Wren 项目和 profile。", code="DATA_SOURCE_UNAVAILABLE")
        secrets = self.store.get_secrets(source.id, revision.id)
        self._install_profile_secrets(source.id, revision.id, secrets)
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
        )
        query_memory_store = self.query_memory_store
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
            runtime_identity = self._resolve_runtime_identity(source, revision)
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
            else self._resolve_runtime_identity(source, revision)
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
