from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from wren_settings import WrenConfigurationError


@dataclass(frozen=True)
class ColumnSchema:
    name: str
    type: str
    nullable: bool
    primary_key: bool
    description: str = ""


@dataclass(frozen=True)
class ForeignKeySchema:
    name: str
    column: str
    referenced_table: str
    referenced_column: str
    referenced_catalog: str = ""
    referenced_schema: str = ""


@dataclass(frozen=True)
class TableSchema:
    name: str
    columns: list[ColumnSchema]
    foreign_keys: list[ForeignKeySchema]
    catalog: str = ""
    schema: str = ""
    description: str = ""

    @property
    def id(self) -> str:
        return ".".join(part for part in (self.catalog, self.schema, self.name) if part)


class MysqlSchemaReader:
    """Connection and metadata-only schema reader for a single MySQL database."""

    def __init__(
        self,
        connection_config: dict[str, Any],
        secrets: dict[str, str] | None = None,
        *,
        connector_factory: Callable[..., Any] | None = None,
    ):
        self.connection_config = connection_config
        self.secrets = secrets or {}
        self.connector_factory = connector_factory or self._mysql_connect

    @staticmethod
    def _mysql_connect(**kwargs: Any) -> Any:
        try:
            import MySQLdb  # type: ignore[import-not-found] # noqa: PLC0415
        except ImportError as exc:
            raise WrenConfigurationError(
                "MySQL 驱动未安装，请重新安装带 MySQL 支持的 Agent。",
                code="WREN_CONNECTION_FAILED",
            ) from exc
        return MySQLdb.connect(**kwargs)

    def _connect(self) -> Any:
        config = self.connection_config
        host = str(config.get("host", "")).strip()
        database = str(config.get("database", "")).strip()
        user = str(config.get("user", "")).strip()
        try:
            port = int(config.get("port", 3306))
        except (TypeError, ValueError) as exc:
            raise WrenConfigurationError("MySQL 端口必须是有效端口。") from exc
        if not host or not database or not user or not 1 <= port <= 65535:
            raise WrenConfigurationError("MySQL 主机、端口、数据库和用户名不能为空。")
        kwargs: dict[str, Any] = {
            "host": host,
            "port": port,
            "user": user,
            "passwd": self.secrets.get("password", ""),
            "db": database,
            "connect_timeout": 10,
            "charset": "utf8mb4",
            "use_unicode": True,
            "autocommit": True,
        }
        ssl_mode = str(config.get("ssl_mode", "preferred")).lower()
        if ssl_mode not in {"disabled", "preferred", "required", "verify_ca", "verify_identity"}:
            raise WrenConfigurationError("MySQL SSL 模式无效。")
        ca_pem = self.secrets.get("ca_pem")
        if ssl_mode in {"verify_ca", "verify_identity"} and not ca_pem:
            raise WrenConfigurationError("所选 SSL 模式需要 CA 证书。")
        # MySQLdb accepts a CA path, not certificate bytes. The application
        # supplies a generated, mode-0600 path when custom CA is configured.
        ca_path = config.get("ca_path")
        if ssl_mode != "disabled":
            kwargs["ssl"] = {"ca": ca_path} if ca_path else {}
            if ssl_mode in {"verify_ca", "verify_identity"}:
                kwargs["ssl_mode"] = "VERIFY_CA" if ssl_mode == "verify_ca" else "VERIFY_IDENTITY"
            else:
                kwargs["ssl_mode"] = "REQUIRED" if ssl_mode == "required" else "PREFERRED"
        return self.connector_factory(**kwargs)

    def test_connection(self) -> None:
        connection = None
        cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            row = cursor.fetchone()
            if not row or row[0] != 1:
                raise WrenConfigurationError(
                    "MySQL 连通性检查未返回预期结果。", code="WREN_CONNECTION_FAILED"
                )
        except WrenConfigurationError:
            raise
        except Exception as exc:
            raise WrenConfigurationError(
                "MySQL 连接失败，请检查地址、账号、密码和网络策略。",
                code="WREN_CONNECTION_FAILED",
            ) from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def introspect(self) -> list[TableSchema]:
        connection = None
        cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor()
            schema = str(self.connection_config["database"])
            cursor.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME",
                (schema,),
            )
            table_names = [str(row[0]) for row in cursor.fetchall()]
            tables = []
            for table_name in table_names:
                cursor.execute(
                    "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY "
                    "FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
                    (schema, table_name),
                )
                columns = [
                    ColumnSchema(
                        name=str(row[0]),
                        type=str(row[1]),
                        nullable=str(row[2]).upper() == "YES",
                        primary_key=str(row[3]).upper() == "PRI",
                    )
                    for row in cursor.fetchall()
                ]
                cursor.execute(
                    "SELECT CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, "
                    "REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "AND REFERENCED_TABLE_NAME IS NOT NULL ORDER BY CONSTRAINT_NAME, ORDINAL_POSITION",
                    (schema, table_name),
                )
                foreign_keys = [
                    ForeignKeySchema(
                        name=str(row[0]),
                        column=str(row[1]),
                        referenced_table=str(row[2]),
                        referenced_column=str(row[3]),
                    )
                    for row in cursor.fetchall()
                ]
                tables.append(TableSchema(
                    name=table_name,
                    columns=columns,
                    foreign_keys=foreign_keys,
                    schema=schema,
                ))
            return tables
        except WrenConfigurationError:
            raise
        except Exception as exc:
            raise WrenConfigurationError(
                "读取 MySQL 表结构失败，请检查账号的 information_schema 访问权限。",
                code="WREN_SCHEMA_DISCOVERY_FAILED",
            ) from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()
