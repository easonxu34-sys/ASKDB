from __future__ import annotations

from collections import defaultdict
import re
from typing import Any

from askdb_agent.integrations.mysql_schema import ColumnSchema, ForeignKeySchema, TableSchema
from askdb_agent.wren_settings import WrenConfigurationError


class WrenMetadataSchemaReader:
    """Connects through Wren and reads catalog metadata, never business rows."""

    _SYSTEM_SCHEMAS = {
        "information_schema", "pg_catalog", "sys", "system", "performance_schema",
        "mysql", "pg_toast",
    }

    def __init__(
        self,
        connector_type: str,
        connection_config: dict[str, Any],
        secrets: dict[str, str] | None = None,
    ):
        self.connector_type = connector_type
        self.connection_config = connection_config
        self.secrets = secrets or {}
        self.warnings: list[str] = []

    def _connect(self) -> Any:
        try:
            from wren.connector.factory import get_connector  # noqa: PLC0415
            from wren.model.data_source import DataSource  # noqa: PLC0415
            from askdb_agent.integrations.wren_connectors import (  # noqa: PLC0415
                build_connection_info,
                connector_fields,
            )

            datasource = DataSource(self.connector_type)
            allowed_fields = {
                field.name for field in connector_fields(self.connector_type, self.connection_config)
            }
            connection_info = build_connection_info(
                self.connector_type,
                {key: value for key, value in self.connection_config.items() if key in allowed_fields},
                {key: value for key, value in self.secrets.items() if key in allowed_fields},
            )
            return get_connector(datasource, connection_info)
        except Exception as exc:
            raise WrenConfigurationError(
                "Wren 数据源连接失败，请检查连接字段、驱动和网络策略。",
                code="WREN_CONNECTION_FAILED",
            ) from exc

    @staticmethod
    def _rows(result: Any) -> list[dict[str, Any]]:
        if hasattr(result, "to_pylist"):
            return result.to_pylist()
        if hasattr(result, "to_dicts"):
            return result.to_dicts()
        if hasattr(result, "to_dict"):
            try:
                rows = result.to_dict(orient="records")
                if isinstance(rows, list):
                    return [dict(row) for row in rows]
            except TypeError:
                pass
        if isinstance(result, list):
            return [dict(row) for row in result]
        return []

    @staticmethod
    def _value(row: dict[str, Any], name: str, default: Any = "") -> Any:
        normalized = {str(key).lower(): value for key, value in row.items()}
        return normalized.get(name.lower(), default)

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value).decode("utf-8", errors="replace")
        return str(value)

    @staticmethod
    def _literal(value: Any) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    @staticmethod
    def _identifier(value: Any, quote: str = '"') -> str:
        return quote + str(value).replace(quote, quote + quote) + quote

    def test_connection(self) -> None:
        connector = None
        try:
            connector = self._connect()
            rows = self._rows(connector.query("SELECT 1", limit=1))
            if not rows or next(iter(rows[0].values()), None) not in (1, True, "1"):
                raise WrenConfigurationError(
                    "数据库连接检查未返回预期结果。", code="WREN_CONNECTION_FAILED"
                )
        except WrenConfigurationError:
            raise
        except Exception as exc:
            raise WrenConfigurationError(
                "数据库连接失败，请检查连接字段、驱动和网络策略。",
                code="WREN_CONNECTION_FAILED",
            ) from exc
        finally:
            if connector is not None:
                try:
                    connector.close()
                except Exception:
                    pass

    def _information_schema(self, relation: str) -> str:
        config = self.connection_config
        connector = self.connector_type
        if connector == "bigquery":
            dataset = str(config.get("dataset_id", ""))
            if dataset:
                project = str(config.get("project_id", ""))
                if not project:
                    raise WrenConfigurationError("BigQuery 必须配置 Project ID。")
                if not re.fullmatch(r"[A-Za-z0-9_-]+", project) or not re.fullmatch(r"[A-Za-z0-9_]+", dataset):
                    raise WrenConfigurationError("BigQuery Project ID 或 Dataset ID 格式无效。")
                path = f"{project}.{dataset}.INFORMATION_SCHEMA.{relation}"
            else:
                billing_project = str(config.get("billing_project_id", ""))
                region = str(config.get("region", ""))
                if not billing_project or not region:
                    raise WrenConfigurationError("BigQuery 项目模式必须配置 Billing Project ID 和 Region。")
                if not re.fullmatch(r"[A-Za-z0-9_-]+", billing_project) or not re.fullmatch(r"[A-Za-z0-9-]+", region):
                    raise WrenConfigurationError("BigQuery Billing Project ID 或 Region 格式无效。")
                path = f"{billing_project}.region-{region}.INFORMATION_SCHEMA.{relation}"
            return f"`{path}`"
        if connector == "snowflake":
            database = str(config.get("database", ""))
            return f"{self._identifier(database)}.INFORMATION_SCHEMA.{relation}"
        if connector == "trino":
            catalog = str(config.get("catalog", ""))
            return f"{self._identifier(catalog)}.information_schema.{relation}"
        if connector == "databricks":
            catalog = str(config.get("catalog", "system") or "system")
            return f"{self._identifier(catalog)}.information_schema.{relation}"
        return f"information_schema.{relation}"

    def _scope_predicate(self, alias: str) -> str:
        connector = self.connector_type
        config = self.connection_config
        if connector == "bigquery":
            dataset = config.get("dataset_id")
            return f"{alias}.table_schema = {self._literal(dataset)}" if dataset else "1 = 1"
        schema_key = {
            "athena": "schema_name",
            "snowflake": "sf_schema",
            "trino": "trino_schema",
        }.get(connector)
        if schema_key and config.get(schema_key):
            return f"{alias}.table_schema = {self._literal(config[schema_key])}"
        schema = config.get("schema")
        if schema:
            return f"{alias}.table_schema = {self._literal(schema)}"
        if connector in {"mysql", "doris", "clickhouse", "duckdb"} and config.get("database"):
            return f"{alias}.table_schema = {self._literal(config['database'])}"
        blocked = ", ".join(self._literal(item) for item in sorted(self._SYSTEM_SCHEMAS))
        return f"LOWER({alias}.table_schema) NOT IN ({blocked})"

    def _column_rows(self, connector: Any) -> list[dict[str, Any]]:
        if self.connector_type == "oracle":
            owner = str(self.connection_config.get("user", ""))
            sql = (
                "SELECT owner AS schema_name, table_name, column_name, data_type, "
                "nullable AS is_nullable, column_id AS ordinal_position "
                "FROM all_tab_columns WHERE owner = "
                f"UPPER({self._literal(owner)}) ORDER BY table_name, column_id"
            )
            return self._rows(connector.query(sql, limit=None))
        if self.connector_type == "spark":
            return self._spark_column_rows(connector)
        relation = self._information_schema("columns")
        sql = (
            "SELECT c.table_catalog AS catalog_name, c.table_schema AS schema_name, "
            "c.table_name AS table_name, c.column_name AS column_name, "
            "c.data_type AS data_type, c.is_nullable AS is_nullable, "
            "c.ordinal_position AS ordinal_position "
            f"FROM {relation} c WHERE {self._scope_predicate('c')} "
            "ORDER BY c.table_schema, c.table_name, c.ordinal_position"
        )
        return self._rows(connector.query(sql, limit=None))

    def _athena_table_descriptions(self) -> dict[str, dict[str, Any]]:
        """Read Athena table and column comments from the Glue Data Catalog."""
        import boto3  # noqa: PLC0415

        region = self.connection_config.get("region_name")
        session_options: dict[str, Any] = {}
        web_identity_token = self.secrets.get("web_identity_token")
        role_arn = self.secrets.get("role_arn")
        access_key = self.secrets.get("aws_access_key_id")
        secret_key = self.secrets.get("aws_secret_access_key")
        if web_identity_token and role_arn:
            sts = boto3.client("sts", region_name=region)
            response = sts.assume_role_with_web_identity(
                RoleArn=role_arn,
                RoleSessionName=str(
                    self.connection_config.get("role_session_name") or "wren-oidc-session"
                ),
                WebIdentityToken=web_identity_token,
            )
            credentials = response["Credentials"]
            session_options = {
                "aws_access_key_id": credentials["AccessKeyId"],
                "aws_secret_access_key": credentials["SecretAccessKey"],
                "aws_session_token": credentials["SessionToken"],
            }
        elif access_key and secret_key:
            session_options = {
                "aws_access_key_id": access_key,
                "aws_secret_access_key": secret_key,
            }
            session_token = self.secrets.get("aws_session_token")
            if session_token:
                session_options["aws_session_token"] = session_token

        session = boto3.Session(**session_options)
        client_options = {"region_name": region} if region else {}
        glue = session.client("glue", **client_options)
        database = str(self.connection_config.get("schema_name") or "default")
        request: dict[str, Any] = {"DatabaseName": database, "MaxResults": 100}
        catalog_id = self.connection_config.get("catalog_id")
        if catalog_id:
            request["CatalogId"] = str(catalog_id)

        result: dict[str, dict[str, Any]] = {}
        while True:
            page = glue.get_tables(**request)
            for table in page.get("TableList", []):
                name = str(table.get("Name") or table.get("TableName") or "")
                if not name:
                    continue
                columns = {
                    str(column.get("Name", "")): str(column.get("Comment") or "")
                    for column in table.get("StorageDescriptor", {}).get("Columns", [])
                    if column.get("Name")
                }
                columns.update({
                    str(column.get("Name", "")): str(column.get("Comment") or "")
                    for column in table.get("PartitionKeys", [])
                    if column.get("Name")
                })
                result[name] = {
                    "description": str(table.get("Description") or ""),
                    "columns": columns,
                }
            next_token = page.get("NextToken")
            if not next_token:
                break
            request["NextToken"] = next_token
        return result

    def _athena_description_rows(self, connector: Any) -> list[dict[str, Any]]:
        columns = self._information_schema("columns")
        sql = (
            "SELECT c.table_catalog AS catalog_name, c.table_schema AS schema_name, "
            "c.table_name AS table_name, c.column_name AS column_name, "
            "c.comment AS column_description, '' AS table_description "
            f"FROM {columns} c WHERE {self._scope_predicate('c')} "
            "ORDER BY c.table_schema, c.table_name, c.ordinal_position"
        )
        rows = self._rows(connector.query(sql, limit=None))
        try:
            table_metadata = self._athena_table_descriptions()
        except Exception:
            self.warnings.append(
                "Athena 字段说明已读取，但 Glue Data Catalog 表说明不可读；请检查 Glue 元数据权限。"
            )
            return rows
        for row in rows:
            name = str(self._value(row, "table_name", "") or "")
            column = str(self._value(row, "column_name", "") or "")
            metadata = table_metadata.get(name, {})
            row["table_description"] = metadata.get("description", "")
            row["column_description"] = metadata.get("columns", {}).get(
                column, self._value(row, "column_description", "")
            )
        return rows

    def _description_rows(self, connector: Any) -> list[dict[str, Any]]:
        """Read comments through the source's native metadata catalog."""
        source = self.connector_type
        if source == "spark":
            return []

        if source in {"mysql", "doris"}:
            columns = self._information_schema("columns")
            tables = self._information_schema("tables")
            sql = (
                "SELECT c.table_catalog AS catalog_name, c.table_schema AS schema_name, "
                "c.table_name AS table_name, c.column_name AS column_name, "
                "c.column_comment AS column_description, "
                "t.table_comment AS table_description "
                f"FROM {columns} c LEFT JOIN {tables} t "
                "ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
                f"WHERE {self._scope_predicate('c')} "
                "ORDER BY c.table_schema, c.table_name, c.ordinal_position"
            )
            return self._rows(connector.query(sql, limit=None))

        if source == "athena":
            return self._athena_description_rows(connector)

        if source == "trino":
            columns = self._information_schema("columns")
            sql = (
                "SELECT c.table_catalog AS catalog_name, c.table_schema AS schema_name, "
                "c.table_name AS table_name, c.column_name AS column_name, "
                "c.comment AS column_description, t.comment AS table_description "
                f"FROM {columns} c LEFT JOIN system.metadata.table_comments t "
                "ON t.catalog_name = c.table_catalog AND t.schema_name = c.table_schema "
                "AND t.table_name = c.table_name "
                f"WHERE {self._scope_predicate('c')} "
                "ORDER BY c.table_schema, c.table_name, c.ordinal_position"
            )
            return self._rows(connector.query(sql, limit=None))

        if source in {"snowflake", "databricks"}:
            columns = self._information_schema("columns")
            tables = self._information_schema("tables")
            sql = (
                "SELECT c.table_catalog AS catalog_name, c.table_schema AS schema_name, "
                "c.table_name AS table_name, c.column_name AS column_name, "
                "c.comment AS column_description, t.comment AS table_description "
                f"FROM {columns} c LEFT JOIN {tables} t "
                "ON t.table_catalog = c.table_catalog "
                "AND t.table_schema = c.table_schema AND t.table_name = c.table_name "
                f"WHERE {self._scope_predicate('c')} "
                "ORDER BY c.table_schema, c.table_name, c.ordinal_position"
            )
            return self._rows(connector.query(sql, limit=None))

        if source == "bigquery":
            columns = self._information_schema("columns")
            field_paths = self._information_schema("column_field_paths")
            table_options = self._information_schema("table_options")
            sql = (
                "SELECT c.table_catalog AS catalog_name, c.table_schema AS schema_name, "
                "c.table_name AS table_name, c.column_name AS column_name, "
                "f.description AS column_description, t.table_description AS table_description "
                f"FROM {columns} c LEFT JOIN {field_paths} f "
                "ON f.table_catalog = c.table_catalog AND f.table_schema = c.table_schema "
                "AND f.table_name = c.table_name AND f.field_path = c.column_name "
                "LEFT JOIN (SELECT table_catalog, table_schema, table_name, "
                "ANY_VALUE(option_value) AS table_description "
                f"FROM {table_options} WHERE option_name = 'description' "
                "GROUP BY table_catalog, table_schema, table_name) t "
                "ON t.table_catalog = c.table_catalog AND t.table_schema = c.table_schema "
                "AND t.table_name = c.table_name "
                f"WHERE {self._scope_predicate('c')} "
                "ORDER BY c.table_schema, c.table_name, c.ordinal_position"
            )
            return self._rows(connector.query(sql, limit=None))

        if source == "clickhouse":
            database = self.connection_config.get("database")
            scope = (
                f"c.database = {self._literal(database)}"
                if database
                else "LOWER(c.database) NOT IN ("
                + ", ".join(self._literal(item) for item in sorted(self._SYSTEM_SCHEMAS))
                + ")"
            )
            sql = (
                "SELECT '' AS catalog_name, c.database AS schema_name, "
                "c.table AS table_name, c.name AS column_name, "
                "c.comment AS column_description, t.comment AS table_description "
                "FROM system.columns c LEFT JOIN system.tables t "
                "ON t.database = c.database AND t.name = c.table "
                f"WHERE {scope} ORDER BY c.database, c.table, c.position"
            )
            return self._rows(connector.query(sql, limit=None))

        if source == "duckdb":
            schema = self.connection_config.get("schema")
            scope = (
                f"c.schema_name = {self._literal(schema)}"
                if schema
                else "c.internal = false"
            )
            sql = (
                "SELECT c.database_name AS catalog_name, c.schema_name AS schema_name, "
                "c.table_name AS table_name, c.column_name AS column_name, "
                "c.comment AS column_description, t.comment AS table_description "
                "FROM duckdb_columns() c LEFT JOIN ("
                "SELECT database_name, schema_name, table_name, comment FROM duckdb_tables() "
                "UNION ALL "
                "SELECT database_name, schema_name, view_name AS table_name, comment FROM duckdb_views()"
                ") t ON t.database_name = c.database_name AND t.schema_name = c.schema_name "
                "AND t.table_name = c.table_name "
                f"WHERE {scope} ORDER BY c.schema_name, c.table_name, c.column_index"
            )
            return self._rows(connector.query(sql, limit=None))

        if source == "mssql":
            columns = self._information_schema("columns")
            sql = (
                "SELECT c.table_catalog AS catalog_name, c.table_schema AS schema_name, "
                "c.table_name AS table_name, c.column_name AS column_name, "
                "CAST(cp.value AS NVARCHAR(MAX)) AS column_description, "
                "CAST(tp.value AS NVARCHAR(MAX)) AS table_description "
                f"FROM {columns} c "
                "LEFT JOIN sys.schemas s ON s.name = c.table_schema "
                "LEFT JOIN sys.objects o ON o.schema_id = s.schema_id "
                "AND o.name = c.table_name AND o.type IN ('U', 'V') "
                "LEFT JOIN sys.columns sc ON sc.object_id = o.object_id "
                "AND sc.name = c.column_name "
                "LEFT JOIN sys.extended_properties cp ON cp.major_id = o.object_id "
                "AND cp.minor_id = sc.column_id AND cp.name = N'MS_Description' "
                "LEFT JOIN sys.extended_properties tp ON tp.major_id = o.object_id "
                "AND tp.minor_id = 0 AND tp.name = N'MS_Description' "
                f"WHERE {self._scope_predicate('c')} "
                "ORDER BY c.table_schema, c.table_name, c.ordinal_position"
            )
            return self._rows(connector.query(sql, limit=None))

        if source == "oracle":
            owner = str(self.connection_config.get("user", "")).upper()
            sql = (
                "SELECT '' AS catalog_name, c.owner AS schema_name, c.table_name AS table_name, "
                "c.column_name AS column_name, cc.comments AS column_description, "
                "tc.comments AS table_description FROM all_tab_columns c "
                "LEFT JOIN all_col_comments cc ON cc.owner = c.owner "
                "AND cc.table_name = c.table_name AND cc.column_name = c.column_name "
                "LEFT JOIN all_tab_comments tc ON tc.owner = c.owner "
                "AND tc.table_name = c.table_name "
                f"WHERE c.owner = {self._literal(owner)} "
                "ORDER BY c.owner, c.table_name, c.column_id"
            )
            return self._rows(connector.query(sql, limit=None))

        if source == "postgres":
            schema = self.connection_config.get("schema")
            scope = (
                f"n.nspname = {self._literal(schema)}"
                if schema
                else "LOWER(n.nspname) NOT IN ("
                + ", ".join(self._literal(item) for item in sorted(self._SYSTEM_SCHEMAS))
                + ")"
            )
            sql = (
                "SELECT current_database() AS catalog_name, n.nspname AS schema_name, "
                "t.relname AS table_name, a.attname AS column_name, "
                "obj_description(t.oid, 'pg_class') AS table_description, "
                "col_description(t.oid, a.attnum) AS column_description "
                "FROM pg_catalog.pg_class t "
                "JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace "
                "JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid "
                "WHERE t.relkind IN ('r', 'v', 'm', 'f', 'p') "
                "AND a.attnum > 0 AND NOT a.attisdropped "
                f"AND {scope} ORDER BY n.nspname, t.relname, a.attnum"
            )
            return self._rows(connector.query(sql, limit=None))

        if source == "redshift":
            schema = self.connection_config.get("schema")
            scope = (
                f"c.schema_name = {self._literal(schema)}"
                if schema
                else "LOWER(c.schema_name) NOT IN ("
                + ", ".join(self._literal(item) for item in sorted(self._SYSTEM_SCHEMAS))
                + ")"
            )
            sql = (
                "SELECT c.database_name AS catalog_name, c.schema_name AS schema_name, "
                "c.table_name AS table_name, c.column_name AS column_name, "
                "c.remarks AS column_description, t.remarks AS table_description "
                "FROM svv_redshift_columns c LEFT JOIN svv_redshift_tables t "
                "ON t.database_name = c.database_name AND t.schema_name = c.schema_name "
                "AND t.table_name = c.table_name "
                f"WHERE {scope} ORDER BY c.schema_name, c.table_name, c.ordinal_position"
            )
            return self._rows(connector.query(sql, limit=None))

        return []

    def _catalog_name(self, value: Any) -> str:
        if self.connector_type in {"bigquery", "snowflake", "trino", "databricks"}:
            return str(value or "")
        return ""

    def _spark_column_rows(self, connector: Any) -> list[dict[str, Any]]:
        tables = self._rows(connector.query("SHOW TABLES", limit=None))
        result: list[dict[str, Any]] = []
        for table in tables:
            name = str(self._value(table, "tableName", self._value(table, "table_name")))
            schema = str(self._value(table, "namespace", self._value(table, "database")))
            if not name or name.startswith("#"):
                continue
            qualified = ".".join(
                self._identifier(part, "`") for part in (schema, name) if part
            )
            for column in self._rows(connector.query(f"DESCRIBE TABLE {qualified}", limit=None)):
                column_name = str(self._value(column, "col_name", self._value(column, "column_name"))).strip()
                data_type = str(self._value(column, "data_type", "VARCHAR")).strip()
                if not column_name or column_name.startswith("#"):
                    continue
                result.append({
                    "catalog_name": "",
                    "schema_name": schema,
                    "table_name": name,
                    "column_name": column_name,
                    "data_type": data_type,
                    "is_nullable": "YES",
                    "ordinal_position": len(result) + 1,
                })
        return result

    def _key_rows(self, connector: Any) -> list[dict[str, Any]]:
        if self.connector_type == "oracle":
            owner = str(self.connection_config.get("user", ""))
            sql = (
                "SELECT c.owner AS schema_name, c.table_name, cc.column_name, "
                "c.constraint_name, CASE c.constraint_type WHEN 'P' THEN 'PRIMARY KEY' "
                "WHEN 'R' THEN 'FOREIGN KEY' ELSE c.constraint_type END AS constraint_type, "
                "r.owner AS ref_schema_name, r.table_name AS ref_table_name, "
                "rcc.column_name AS ref_column_name "
                "FROM all_constraints c JOIN all_cons_columns cc "
                "ON cc.owner = c.owner AND cc.constraint_name = c.constraint_name "
                "AND cc.table_name = c.table_name "
                "LEFT JOIN all_constraints r ON r.owner = c.r_owner "
                "AND r.constraint_name = c.r_constraint_name "
                "LEFT JOIN all_cons_columns rcc ON rcc.owner = r.owner "
                "AND rcc.constraint_name = r.constraint_name "
                "AND rcc.table_name = r.table_name AND rcc.position = cc.position "
                "WHERE c.owner = UPPER(" + self._literal(owner) + ") "
                "AND c.constraint_type IN ('P', 'R') "
                "ORDER BY c.table_name, c.constraint_name, cc.position"
            )
            try:
                return self._rows(connector.query(sql, limit=None))
            except Exception:
                return []
        if self.connector_type in {"spark", "duckdb"}:
            return []
        try:
            relation = self._information_schema("key_column_usage")
            sql = (
                "SELECT k.table_catalog AS catalog_name, k.table_schema AS schema_name, "
                "k.table_name AS table_name, k.column_name AS column_name, "
                "k.constraint_name AS constraint_name, t.constraint_type AS constraint_type, "
                "k.referenced_table_catalog AS ref_catalog_name, "
                "k.referenced_table_schema AS ref_schema_name, "
                "k.referenced_table_name AS ref_table_name, "
                "k.referenced_column_name AS ref_column_name "
                f"FROM {relation} k LEFT JOIN {self._information_schema('table_constraints')} t "
                "ON t.constraint_catalog = k.constraint_catalog "
                "AND t.constraint_schema = k.constraint_schema "
                "AND t.constraint_name = k.constraint_name "
                f"WHERE {self._scope_predicate('k')} "
                "ORDER BY k.table_schema, k.table_name, k.constraint_name"
            )
            return self._rows(connector.query(sql, limit=None))
        except Exception:
            # Several warehouses do not publish key metadata or the user may
            # not have permission to inspect it. Table and column discovery is
            # still useful; users can add relationships manually.
            return []

    @staticmethod
    def _table_id(catalog: Any, schema: Any, table: Any) -> str:
        return ".".join(str(part) for part in (catalog, schema, table) if part)

    def introspect(self) -> list[TableSchema]:
        connector = None
        self.warnings = []
        try:
            connector = self._connect()
            column_rows = self._column_rows(connector)
            if not column_rows:
                return []
            try:
                description_rows = self._description_rows(connector)
            except Exception:
                description_rows = []
                self.warnings.append(
                    "表结构已读取，但当前连接器或账号无法读取表和字段说明。"
                )
            table_descriptions: dict[str, str] = {}
            column_descriptions: dict[tuple[str, str], str] = {}
            for row in description_rows:
                catalog = self._catalog_name(self._value(row, "catalog_name", ""))
                schema = str(self._value(row, "schema_name", "") or "")
                table = str(self._value(row, "table_name", "") or "")
                column = str(self._value(row, "column_name", "") or "")
                if not table:
                    continue
                table_id = self._table_id(catalog, schema, table)
                table_description = self._text(self._value(row, "table_description", ""))
                if table_description:
                    table_descriptions[table_id] = table_description
                column_description = self._text(self._value(row, "column_description", ""))
                if column and column_description:
                    column_descriptions[(table_id, column)] = column_description
            key_rows = self._key_rows(connector)
            primary_keys: dict[str, set[str]] = defaultdict(set)
            foreign_keys: dict[str, list[ForeignKeySchema]] = defaultdict(list)
            for row in key_rows:
                catalog = self._catalog_name(self._value(row, "catalog_name"))
                schema = self._value(row, "schema_name")
                table = self._value(row, "table_name")
                column = self._value(row, "column_name")
                table_id = self._table_id(catalog, schema, table)
                constraint = str(self._value(row, "constraint_name", ""))
                ref_table = self._value(row, "ref_table_name", None)
                if ref_table:
                    foreign_keys[table_id].append(ForeignKeySchema(
                        name=constraint,
                        column=str(column),
                        referenced_table=str(ref_table),
                        referenced_column=str(self._value(row, "ref_column_name", "")),
                        referenced_catalog=str(self._value(row, "ref_catalog_name", "") or ""),
                        referenced_schema=str(self._value(row, "ref_schema_name", "") or ""),
                    ))
                elif constraint and str(self._value(row, "constraint_type", "")).upper() == "PRIMARY KEY":
                    primary_keys[table_id].add(str(column))

            grouped: dict[str, dict[str, Any]] = {}
            for row in column_rows:
                catalog = self._catalog_name(self._value(row, "catalog_name", ""))
                schema = str(self._value(row, "schema_name", "") or "")
                table = str(self._value(row, "table_name", "") or "")
                column = str(self._value(row, "column_name", "") or "")
                if not table or not column:
                    continue
                table_id = self._table_id(catalog, schema, table)
                group = grouped.setdefault(table_id, {
                    "name": table,
                    "catalog": catalog,
                    "schema": schema,
                    "description": table_descriptions.get(table_id, ""),
                    "columns": [],
                })
                group["columns"].append(ColumnSchema(
                    name=column,
                    type=str(self._value(row, "data_type", "VARCHAR") or "VARCHAR"),
                    nullable=str(self._value(row, "is_nullable", "YES")).upper()
                    in {"YES", "TRUE", "1", "Y"},
                    primary_key=column in primary_keys.get(table_id, set()),
                    description=column_descriptions.get((table_id, column), ""),
                ))
            return [
                TableSchema(
                    name=value["name"],
                    columns=value["columns"],
                    foreign_keys=foreign_keys.get(table_id, []),
                    catalog=value["catalog"],
                    schema=value["schema"],
                    description=value["description"],
                )
                for table_id, value in grouped.items()
            ]
        except WrenConfigurationError:
            raise
        except Exception as exc:
            raise WrenConfigurationError(
                "读取数据源表结构失败，请检查元数据查看权限和连接器配置。",
                code="WREN_SCHEMA_DISCOVERY_FAILED",
            ) from exc
        finally:
            if connector is not None:
                try:
                    connector.close()
                except Exception:
                    pass
