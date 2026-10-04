"""One-time import of the environment-backed legacy Wren project."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

import yaml

from config import Settings
from integrations.wren_cli import WrenCli
from integrations.wren_connectors import (
    SUPPORTED_DATABASE_CONNECTORS,
    connector_fields,
    is_sensitive_field,
    normalize_connection,
    profile_fields as make_profile_fields,
)
from integrations.wren_memory import compute_semantic_digest
from wren_settings import WrenConfigurationError, WrenSettingsStore


class LegacyWrenProjectImporter:
    """Read the legacy profile/project once and migrate it into managed storage."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: WrenSettingsStore,
        cli: WrenCli,
        data_root: Path,
        token_factory: Callable[[str], str],
    ) -> None:
        self.settings = settings
        self.store = store
        self.cli = cli
        self.data_root = data_root
        self._token = token_factory

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

    def migrate(self) -> None:
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
