from __future__ import annotations

from dataclasses import asdict
from typing import Any


# These are the database and warehouse types exposed by the pinned Wren 0.15
# runtime. File connectors and Wren-hosted service connectors are intentionally
# left out of this product surface.
SUPPORTED_DATABASE_CONNECTORS: dict[str, str] = {
    "athena": "Amazon Athena",
    "bigquery": "Google BigQuery",
    "clickhouse": "ClickHouse",
    "databricks": "Databricks",
    "doris": "Apache Doris",
    "duckdb": "DuckDB",
    "mssql": "Microsoft SQL Server",
    "mysql": "MySQL / MariaDB",
    "oracle": "Oracle",
    "postgres": "PostgreSQL",
    "redshift": "Amazon Redshift",
    "snowflake": "Snowflake",
    "spark": "Apache Spark",
    "trino": "Trino",
}

SEMANTIC_CONFIG_FIELDS = {
    "tables",
    "models",
    "relationships",
    "ignored_foreign_keys",
    "rules",
    "views",
}


def _field_registry():
    # Keep Wren imports lazy; Wren snapshots WREN_HOME during import.
    from wren.model.field_registry import get_fields, get_variants  # noqa: PLC0415

    return get_fields, get_variants


def connector_catalog() -> list[dict[str, Any]]:
    get_fields, get_variants = _field_registry()
    result: list[dict[str, Any]] = []
    for connector_type, label in SUPPORTED_DATABASE_CONNECTORS.items():
        variants = get_variants(connector_type) or []
        field_groups = []
        for variant in variants or [None]:
            fields = [asdict(item) for item in get_fields(connector_type, variant=variant)]
            field_groups.append({"variant": variant, "fields": fields})
        result.append({
            "type": connector_type,
            "label": label,
            "variants": variants,
            "field_groups": field_groups,
        })
    return result


def connector_fields(connector_type: str, connection: dict[str, Any] | None = None) -> list[Any]:
    connector_type = connector_type.strip().lower()
    if connector_type not in SUPPORTED_DATABASE_CONNECTORS:
        raise ValueError("Wren 数据源类型不受支持。")
    get_fields, get_variants = _field_registry()
    variants = get_variants(connector_type)
    variant = None
    if variants:
        discriminator = next(
            (field.name for field in get_fields(connector_type, variant=variants[0]) if field.name.endswith("_type")),
            None,
        )
        requested = (connection or {}).get(discriminator) if discriminator else None
        variant = requested if requested in variants else variants[0]
    return get_fields(connector_type, variant=variant)


def is_sensitive_field(field: Any) -> bool:
    return bool(
        getattr(field, "sensitive", False)
        or getattr(field, "input_type", "") in {"password", "file_base64"}
    )


def build_connection_info(
    connector_type: str,
    config: dict[str, Any],
    secrets: dict[str, str] | None = None,
) -> Any:
    """Convert stored field names into the aliases expected by Wren's models."""
    from wren.model.data_source import DataSource  # noqa: PLC0415

    connector_type = connector_type.strip().lower()
    values = {**config, **(secrets or {})}
    fields = connector_fields(connector_type, values)
    aliased = {
        field.alias or field.name: values[field.name]
        for field in fields
        if field.name in values
    }
    return DataSource(connector_type).get_connection_info(aliased)


def normalize_connection(
    connector_type: str,
    connection: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate and split a Wren form into JSON-safe config and secret values."""
    from pydantic import SecretStr  # noqa: PLC0415
    connector_type = connector_type.strip().lower()
    if connector_type not in SUPPORTED_DATABASE_CONNECTORS:
        raise ValueError("Wren 数据源类型不受支持。")
    if not isinstance(connection, dict):
        raise ValueError("连接配置格式无效。")

    fields = connector_fields(connector_type, connection)
    allowed = {field.name for field in fields}
    if set(connection) - allowed:
        raise ValueError("连接配置包含当前数据源类型不支持的字段。")

    model = build_connection_info(connector_type, connection)
    config: dict[str, Any] = {}
    secrets: dict[str, str] = {}
    for field in fields:
        value = getattr(model, field.name, None)
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if value is None:
            continue
        if is_sensitive_field(field):
            secret = str(value)
            if secret:
                secrets[field.name] = secret
            continue
        if hasattr(value, "value") and isinstance(getattr(value, "value"), str):
            value = value.value
        config[field.name] = value
    return config, secrets


def profile_fields(
    connector_type: str,
    config: dict[str, Any],
    secrets: dict[str, str],
    source_token: str,
    revision_token: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Produce the Wren profile payload with server-generated secret refs."""
    connector_type = connector_type.strip().lower()
    fields = connector_fields(connector_type, config)
    profile: dict[str, Any] = {"datasource": connector_type}
    secret_values: dict[str, str] = {}
    for field in fields:
        value = config.get(field.name)
        if is_sensitive_field(field):
            secret = secrets.get(field.name)
            if secret:
                env_name = secret_environment_name(source_token, revision_token, field.name)
                profile[field.alias or field.name] = f"${{{env_name}}}"
                secret_values[env_name] = secret
            continue
        if value is not None:
            profile[field.alias or field.name] = value
    return profile, secret_values


def secret_environment_name(source_token: str, revision_token: str, field_name: str) -> str:
    field_token = "".join(char for char in field_name.upper() if char.isalnum())[:32]
    return f"ASKDB_WREN_{source_token}_{revision_token}_{field_token}"


def safe_connection_projection(config: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in config.items()
        if key not in SEMANTIC_CONFIG_FIELDS
    }
