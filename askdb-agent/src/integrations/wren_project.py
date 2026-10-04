from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable

import yaml

from wren_settings import WrenConfigurationError
from integrations.wren_connectors import SUPPORTED_DATABASE_CONNECTORS


_TOKEN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
_MODEL_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,127}$")


class WrenProjectBuilder:
    """Writes one immutable, credential-free Wren project revision."""

    def __init__(self, data_root: Path):
        self.data_root = data_root.expanduser().resolve()

    @staticmethod
    def _field(value: Any, key: str, default: Any = None) -> Any:
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)

    @staticmethod
    def _model_name(value: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_]", "_", value.strip())
        if not normalized or normalized[0].isdigit():
            normalized = f"model_{normalized}"
        if not _MODEL_NAME.fullmatch(normalized):
            raise WrenConfigurationError("模型名称无效。")
        return normalized

    def build(
        self,
        source_id: str,
        revision_id: str,
        profile_name: str,
        connector_type: str,
        config: dict[str, Any],
        schema: Iterable[Any],
    ) -> Path:
        if not _TOKEN.fullmatch(source_id) or not _TOKEN.fullmatch(revision_id):
            raise WrenConfigurationError("数据源或版本标识无效。")
        if not _TOKEN.fullmatch(profile_name) or connector_type not in SUPPORTED_DATABASE_CONNECTORS:
            raise WrenConfigurationError("Wren profile 或数据库类型无效。")
        if any(str(key).lower() in {
            "password", "passwd", "secret", "credentials", "access_token",
            "access_key_secret", "aws_secret_access_key", "client_secret",
            "private_key", "dsn", "ca_pem", "client_key", "ssl_ca",
        } for key in config):
            raise WrenConfigurationError("凭证字段不能写入 Wren 项目配置。")
        project_dir = (self.data_root / "sources" / source_id / "revisions" / revision_id).resolve()
        if self.data_root not in project_dir.parents:
            raise WrenConfigurationError("项目路径无效。")
        project_dir.mkdir(mode=0o700, parents=True, exist_ok=False)

        selected_tables = config.get("tables")
        has_table_selection = "tables" in config
        selected_names = {
            str(item.get("name")) if isinstance(item, dict) else str(item)
            for item in selected_tables or []
        }
        model_configs = config.get("models", [])
        models_by_table = {
            str(item.get("table")): item for item in model_configs
            if isinstance(item, dict) and item.get("table")
        }
        project = {
            "schema_version": 5,
            "name": source_id,
            "version": "1.0",
            "catalog": "wren",
            "schema": self._schema(connector_type, config),
            "data_source": connector_type,
            "profile": profile_name,
        }
        self._write_yaml(project_dir / "wren_project.yml", project)

        model_names: dict[str, str] = {}
        for table in schema:
            table_name = str(self._field(table, "name", ""))
            table_catalog = str(self._field(table, "catalog", "") or self._catalog(connector_type, config))
            table_schema = str(self._field(table, "schema", "") or self._schema(connector_type, config))
            table_id = str(self._field(table, "id", "") or ".".join(
                part for part in (table_catalog, table_schema, table_name) if part
            ))
            if not table_id:
                table_id = table_name
            if not table_name or (
                has_table_selection and table_id not in selected_names and table_name not in selected_names
            ):
                continue
            model_config = models_by_table.get(table_id, models_by_table.get(table_name, {}))
            model_name = self._model_name(str(model_config.get("name") or table_name))
            if model_name in model_names.values():
                raise WrenConfigurationError("模型名称不能重复。")
            model_names[table_id] = model_name
            field_config = {
                str(item.get("name")): item
                for item in model_config.get("columns", [])
                if isinstance(item, dict) and item.get("name")
            }
            columns = []
            primary_keys = []
            for column in self._field(table, "columns", []):
                column_name = str(self._field(column, "name", ""))
                if not column_name:
                    continue
                override = field_config.get(column_name, {})
                is_primary = bool(override.get("primary_key", self._field(column, "primary_key", False)))
                if is_primary:
                    primary_keys.append(column_name)
                entry: dict[str, Any] = {
                    "name": column_name,
                    "type": str(self._field(column, "type", "VARCHAR")),
                    "not_null": not bool(self._field(column, "nullable", True)),
                }
                if is_primary:
                    entry["is_primary_key"] = True
                description = override.get("description")
                properties: dict[str, Any] = {}
                if description:
                    properties["description"] = str(description)[:2000]
                if override.get("hidden"):
                    entry["is_hidden"] = True
                if properties:
                    entry["properties"] = properties
                columns.append(entry)
            metadata: dict[str, Any] = {
                "name": model_name,
                "table_reference": {
                    "catalog": table_catalog,
                    "schema": table_schema,
                    "table": table_name,
                },
                "columns": columns,
            }
            if primary_keys:
                metadata["primary_key"] = primary_keys[0] if len(primary_keys) == 1 else primary_keys
            description = model_config.get("description")
            if description:
                metadata["properties"] = {"description": str(description)[:4000]}
            self._write_yaml(project_dir / "models" / model_name / "metadata.yml", metadata)
        if not model_names:
            raise WrenConfigurationError("至少选择一个可用的数据表。")

        relationships = []
        for index, item in enumerate(config.get("relationships", [])):
            if not isinstance(item, dict):
                raise WrenConfigurationError("关系配置格式无效。")
            left = str(item.get("left_model", ""))
            right = str(item.get("right_model", ""))
            condition = str(item.get("condition", ""))
            if left not in model_names.values() or right not in model_names.values() or not condition:
                raise WrenConfigurationError("关系必须引用已选择的模型并填写连接条件。")
            name = self._model_name(str(item.get("name") or f"relationship_{index + 1}"))
            join_type = str(item.get("join_type", "many_to_one"))
            if join_type not in {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}:
                raise WrenConfigurationError("关系基数无效。")
            relationships.append({
                "name": name,
                "models": [left, right],
                "join_type": join_type,
                "condition": condition[:4000],
            })
        if relationships:
            self._write_yaml(project_dir / "relationships.yml", {"relationships": relationships})

        self._write_yaml(project_dir / "knowledge" / "knowledge.yml", {"schema_version": 1})
        used_rule_filenames: set[str] = set()
        for index, rule in enumerate(config.get("rules", [])):
            if isinstance(rule, str):
                name, content = f"rule_{index + 1}", rule
            elif isinstance(rule, dict):
                name, content = str(rule.get("name") or f"rule_{index + 1}"), str(rule.get("content", ""))
            else:
                raise WrenConfigurationError("业务规则格式无效。")
            filename = self._filename(name)
            if filename in used_rule_filenames:
                filename = f"{filename[:64]}-{index + 1}"
                suffix = 1
                while filename in used_rule_filenames:
                    suffix += 1
                    filename = f"{filename[:60]}-{index + 1}-{suffix}"
            used_rule_filenames.add(filename)
            if content.strip():
                title = " ".join(name.replace("\r", " ").replace("\n", " ").split())
                managed_rule = re.fullmatch(r"askdb_br_[a-f0-9]{32}", name)
                configured_title = re.search(r"(?m)^#{1,6}\s+(.+?)\s*#*\s*$", content)
                if managed_rule and configured_title:
                    title = configured_title.group(1).strip()
                markdown = f"# {title}\n\n{content[:20_000].rstrip()}\n"
                self._write_text(
                    project_dir / "knowledge" / "rules" / f"{filename}.md",
                    markdown,
                )

        for view in config.get("views", []):
            if not isinstance(view, dict):
                raise WrenConfigurationError("视图配置格式无效。")
            name = self._model_name(str(view.get("name", "")))
            statement = str(view.get("sql", "")).strip()
            if not statement or len(statement) > 50_000:
                raise WrenConfigurationError("视图 SQL 不能为空且不能超过 50000 个字符。")
            self._write_yaml(project_dir / "views" / name / "metadata.yml", {
                "name": name,
                "properties": {"description": str(view.get("description", ""))[:4000]},
            })
            self._write_yaml(project_dir / "views" / name / "sql.yml", {"statement": statement})
        return project_dir

    @staticmethod
    def _catalog(connector_type: str, config: dict[str, Any]) -> str:
        if connector_type == "bigquery":
            return str(config.get("project_id", config.get("billing_project_id", "")))
        if connector_type == "snowflake":
            return str(config.get("database", ""))
        if connector_type == "trino":
            return str(config.get("catalog", ""))
        if connector_type == "databricks":
            return str(config.get("catalog", ""))
        return ""

    @staticmethod
    def _schema(connector_type: str, config: dict[str, Any]) -> str:
        if connector_type == "bigquery":
            return str(config.get("dataset_id", ""))
        if connector_type == "snowflake":
            return str(config.get("sf_schema", ""))
        if connector_type == "trino":
            return str(config.get("trino_schema", ""))
        if connector_type == "athena":
            return str(config.get("schema_name", "default"))
        return str(config.get("database", config.get("schema", "")))

    @staticmethod
    def _filename(value: str) -> str:
        normalized = value.strip()
        cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", normalized).strip("_")
        if not cleaned:
            cleaned = "rule"
        if not normalized.isascii() or cleaned != normalized:
            suffix = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
            return f"{cleaned[:64]}-{suffix}"
        return cleaned[:80]

    @staticmethod
    def _write_yaml(path: Path, value: Any) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")

    @staticmethod
    def _write_text(path: Path, value: str) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
