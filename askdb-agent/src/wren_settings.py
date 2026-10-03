from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from integrations.wren_connectors import (
    SUPPORTED_DATABASE_CONNECTORS,
    safe_connection_projection,
)


class WrenSettingsUnavailable(RuntimeError):
    """The Wren catalog or its encryption key is unavailable."""


class WrenConfigurationError(ValueError):
    """A submitted source configuration is invalid."""

    def __init__(self, message: str, *, code: str = "WREN_CONFIGURATION_INVALID"):
        self.code = code
        super().__init__(message)


class ChatDataSourceMismatch(ValueError):
    """A chat thread was already bound to a different data source."""


class ChatLegacyThreadRequiresNew(ValueError):
    """A thread created before accounts cannot be claimed by a user."""


class ChatThreadOwnerMismatch(ValueError):
    """A chat thread belongs to a different user."""


class ChatDataSourceForbidden(ValueError):
    """The current user is not assigned to the selected data source."""


class ChatDataSourceUnavailable(ValueError):
    """The selected data source is missing or disabled."""


@dataclass(frozen=True)
class WrenDataSource:
    id: str
    display_name: str
    connector_type: str
    enabled: bool
    active_revision_id: str | None
    draft_revision_id: str | None
    runtime_status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WrenRevision:
    id: str
    source_id: str
    status: str
    config: dict[str, Any]
    project_dir: str | None
    profile_name: str | None
    mdl_digest: str | None
    error_code: str | None
    created_at: str
    updated_at: str


class WrenSettingsStore:
    """SQLite catalog for Wren data sources, revisions, secrets, and chat bindings."""

    _SECRET_FIELDS = {
        "password", "passwd", "ca", "ca_pem", "ssl_ca", "client_key", "private_key",
        "credentials", "access_token", "client_secret", "access_key_id", "access_key_secret",
        "aws_access_key_id", "aws_secret_access_key", "aws_session_token", "web_identity_token",
        "secret_key", "dsn", "secret", "token",
    }

    def __init__(self, database_path: Path | None = None, encryption_key: str | None = None):
        load_dotenv()
        configured_path = database_path or Path(
            os.environ.get(
                "ASKDB_SETTINGS_DB_PATH",
                str(Path(__file__).resolve().parents[1] / "data" / "model-settings.sqlite3"),
            )
        )
        self.database_path = configured_path.expanduser().resolve()
        self._encryption_key = (
            encryption_key
            if encryption_key is not None
            else os.environ.get("ASKDB_SETTINGS_ENCRYPTION_KEY", "")
        ).strip()

    def _cipher(self) -> Fernet:
        if not self._encryption_key:
            raise WrenSettingsUnavailable("Wren 设置加密密钥未配置。")
        try:
            return Fernet(self._encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise WrenSettingsUnavailable("Wren 设置加密密钥格式无效。") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            connection = sqlite3.connect(self.database_path, timeout=5)
            os.chmod(self.database_path, 0o600)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS wren_data_sources (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    connector_type TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    active_revision_id TEXT,
                    draft_revision_id TEXT,
                    runtime_status TEXT NOT NULL DEFAULT 'not_ready',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wren_revisions (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES wren_data_sources(id),
                    status TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    project_dir TEXT,
                    profile_name TEXT,
                    mdl_digest TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_wren_revisions_source
                    ON wren_revisions(source_id, created_at);
                CREATE TABLE IF NOT EXISTS wren_secrets (
                    source_id TEXT NOT NULL REFERENCES wren_data_sources(id),
                    revision_id TEXT NOT NULL REFERENCES wren_revisions(id),
                    secret_name TEXT NOT NULL,
                    ciphertext BLOB NOT NULL,
                    PRIMARY KEY(source_id, revision_id, secret_name)
                );
                CREATE TABLE IF NOT EXISTS wren_operations (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES wren_data_sources(id),
                    revision_id TEXT,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    error_code TEXT,
                    message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wren_source_operation_state (
                    source_id TEXT PRIMARY KEY REFERENCES wren_data_sources(id) ON DELETE CASCADE,
                    generation INTEGER NOT NULL DEFAULT 0,
                    active_operation_id TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_thread_data_sources (
                    thread_id TEXT PRIMARY KEY,
                    data_source_id TEXT NOT NULL REFERENCES wren_data_sources(id),
                    owner_user_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wren_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    default_data_source_id TEXT REFERENCES wren_data_sources(id),
                    migration_status TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )
            additive_columns = {
                "wren_revisions": {
                    "parent_revision_id": "TEXT",
                    "publication_operation_id": "TEXT",
                },
                "wren_operations": {
                    "operation_type": "TEXT NOT NULL DEFAULT 'settings_apply'",
                    "base_revision_id": "TEXT",
                    "base_mdl_digest": "TEXT",
                    "base_generation": "INTEGER",
                    "target_revision_id": "TEXT",
                    "target_mdl_digest": "TEXT",
                    "activated_generation": "INTEGER",
                    "actor_id": "TEXT",
                    "payload_json": "TEXT NOT NULL DEFAULT '{}'",
                },
            }
            for table, columns in additive_columns.items():
                existing = {
                    row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
                }
                for name, definition in columns.items():
                    if name not in existing:
                        connection.execute(
                            f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                        )
            connection.execute(
                """INSERT OR IGNORE INTO wren_source_operation_state
                   (source_id, generation, active_operation_id, updated_at)
                   SELECT id, 0, NULL, ? FROM wren_data_sources""",
                (self._now(),),
            )
            thread_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(chat_thread_data_sources)")
            }
            if "owner_user_id" not in thread_columns:
                connection.execute(
                    "ALTER TABLE chat_thread_data_sources ADD COLUMN owner_user_id TEXT"
                )
            connection.commit()
            return connection
        except (OSError, sqlite3.Error) as exc:
            raise WrenSettingsUnavailable("Wren 设置存储当前不可用。") from exc

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _source_from_row(row: sqlite3.Row) -> WrenDataSource:
        return WrenDataSource(
            id=row["id"],
            display_name=row["display_name"],
            connector_type=row["connector_type"],
            enabled=bool(row["enabled"]),
            active_revision_id=row["active_revision_id"],
            draft_revision_id=row["draft_revision_id"],
            runtime_status=row["runtime_status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _revision_from_row(row: sqlite3.Row) -> WrenRevision:
        return WrenRevision(
            id=row["id"],
            source_id=row["source_id"],
            status=row["status"],
            config=json.loads(row["config_json"]),
            project_dir=row["project_dir"],
            profile_name=row["profile_name"],
            mdl_digest=row["mdl_digest"],
            error_code=row["error_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _safe_config(self, config: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(config, dict):
            raise WrenConfigurationError("数据源配置格式无效。")
        if any(
            key.lower() in self._SECRET_FIELDS
            or key.lower() == "key"
            or key.lower() == "token"
            or key.lower().endswith("_key")
            or key.lower().endswith("_token")
            or (
                not key.lower().endswith("_type")
                and any(
                    marker in key.lower()
                    for marker in (
                        "password", "passwd", "secret", "credential",
                        "private_key", "privatekey", "access_key", "accesskey",
                        "client_key", "clientkey", "certificate", "ca_pem", "ssl_ca",
                    )
                )
            )
            for key in config
        ):
            raise WrenConfigurationError("凭证字段必须单独加密保存。")
        try:
            encoded = json.dumps(config, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise WrenConfigurationError("数据源配置格式无效。") from exc
        if len(encoded) > 256_000:
            raise WrenConfigurationError("数据源配置超过允许大小。")
        return json.loads(encoded)

    def _insert_secrets(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        revision_id: str,
        secrets: dict[str, str] | None,
    ) -> None:
        if not secrets:
            return
        cipher = self._cipher()
        for name, value in secrets.items():
            if not isinstance(name, str) or not name or not isinstance(value, str):
                raise WrenConfigurationError("凭证字段格式无效。")
            encrypted = cipher.encrypt(value.encode("utf-8"))
            connection.execute(
                "INSERT INTO wren_secrets(source_id, revision_id, secret_name, ciphertext) "
                "VALUES (?, ?, ?, ?)",
                (source_id, revision_id, name, encrypted),
            )

    def create_data_source(
        self,
        display_name: str,
        connector_type: str,
        config: dict[str, Any],
        secrets: dict[str, str] | None = None,
        *,
        source_id: str | None = None,
        revision_id: str | None = None,
    ) -> WrenDataSource:
        name = display_name.strip()
        connector = connector_type.strip().lower()
        if not name or len(name) > 120:
            raise WrenConfigurationError("数据源名称不能为空且不能超过 120 个字符。")
        if connector not in SUPPORTED_DATABASE_CONNECTORS:
            raise WrenConfigurationError("Wren 数据源类型不受支持。")
        safe_config = self._safe_config(config)
        source_id = source_id or f"ds_{uuid.uuid4().hex}"
        revision_id = revision_id or f"rev_{uuid.uuid4().hex}"
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO wren_data_sources
                   (id, display_name, connector_type, enabled, draft_revision_id,
                    runtime_status, created_at, updated_at)
                   VALUES (?, ?, ?, 1, ?, 'not_ready', ?, ?)""",
                (source_id, name, connector, revision_id, now, now),
            )
            connection.execute(
                """INSERT INTO wren_revisions
                   (id, source_id, status, config_json, created_at, updated_at)
                   VALUES (?, ?, 'draft', ?, ?, ?)""",
                (revision_id, source_id, json.dumps(safe_config, ensure_ascii=False), now, now),
            )
            connection.execute(
                """INSERT INTO wren_source_operation_state
                   (source_id, generation, active_operation_id, updated_at)
                   VALUES (?, 0, NULL, ?)""",
                (source_id, now),
            )
            self._insert_secrets(connection, source_id, revision_id, secrets)
        return self.get_data_source(source_id)

    def create_revision(
        self,
        source_id: str,
        config: dict[str, Any],
        secrets: dict[str, str] | None = None,
        *,
        copy_secrets_from: str | None = None,
        display_name: str | None = None,
    ) -> WrenRevision:
        safe_config = self._safe_config(config)
        safe_display_name = display_name.strip() if display_name is not None else None
        if safe_display_name is not None and (not safe_display_name or len(safe_display_name) > 120):
            raise WrenConfigurationError("数据源名称不能为空且不能超过 120 个字符。")
        revision_id = f"rev_{uuid.uuid4().hex}"
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM wren_data_sources WHERE id=?", (source_id,)).fetchone() is None:
                raise LookupError("数据源不存在。")
            state = connection.execute(
                "SELECT generation, active_operation_id FROM wren_source_operation_state WHERE source_id=?",
                (source_id,),
            ).fetchone()
            if state is not None and state["active_operation_id"] is not None:
                raise WrenConfigurationError(
                    "数据源正在发布或应用版本，请完成后再修改草稿。",
                    code="SOURCE_OPERATION_IN_PROGRESS",
                )
            source = connection.execute(
                "SELECT active_revision_id FROM wren_data_sources WHERE id=?", (source_id,)
            ).fetchone()
            connection.execute(
                """INSERT INTO wren_revisions
                   (id, source_id, status, config_json, parent_revision_id, created_at, updated_at)
                   VALUES (?, ?, 'draft', ?, ?, ?, ?)""",
                (
                    revision_id,
                    source_id,
                    json.dumps(safe_config, ensure_ascii=False),
                    source["active_revision_id"],
                    now,
                    now,
                ),
            )
            if copy_secrets_from:
                rows = connection.execute(
                    "SELECT secret_name, ciphertext FROM wren_secrets WHERE source_id=? AND revision_id=?",
                    (source_id, copy_secrets_from),
                ).fetchall()
                if rows or secrets:
                    cipher = self._cipher()
                    copied = {}
                    for row in rows:
                        try:
                            copied[row["secret_name"]] = cipher.decrypt(row["ciphertext"]).decode("utf-8")
                        except (InvalidToken, UnicodeDecodeError, TypeError, ValueError) as exc:
                            raise WrenSettingsUnavailable(
                                "Wren 数据源凭证无法解密，请检查部署加密密钥。"
                            ) from exc
                    copied.update(secrets or {})
                    self._insert_secrets(connection, source_id, revision_id, copied)
            else:
                self._insert_secrets(connection, source_id, revision_id, secrets)
            connection.execute(
                "UPDATE wren_data_sources SET draft_revision_id=?, runtime_status=CASE "
                "WHEN active_revision_id IS NULL THEN 'not_ready' ELSE runtime_status END, "
                "display_name=COALESCE(?, display_name), updated_at=? WHERE id=?",
                (revision_id, safe_display_name, now, source_id),
            )
            connection.execute(
                """INSERT INTO wren_source_operation_state
                   (source_id, generation, active_operation_id, updated_at)
                   VALUES (?, 1, NULL, ?)
                   ON CONFLICT(source_id) DO UPDATE SET
                     generation=wren_source_operation_state.generation+1,
                     updated_at=excluded.updated_at""",
                (source_id, now),
            )
            row = connection.execute("SELECT * FROM wren_revisions WHERE id=?", (revision_id,)).fetchone()
        return self._revision_from_row(row)

    def save_draft(
        self,
        source_id: str,
        config: dict[str, Any],
        secrets: dict[str, str] | None = None,
        *,
        display_name: str | None = None,
    ) -> WrenRevision:
        source = self.get_data_source(source_id)
        return self.create_revision(
            source_id,
            config,
            secrets,
            copy_secrets_from=source.draft_revision_id or source.active_revision_id,
            display_name=display_name,
        )

    def update_data_source_name(self, source_id: str, display_name: str) -> WrenDataSource:
        name = display_name.strip()
        if not name or len(name) > 120:
            raise WrenConfigurationError("数据源名称不能为空且不能超过 120 个字符。")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT active_operation_id FROM wren_source_operation_state WHERE source_id=?",
                (source_id,),
            ).fetchone()
            if operation and operation["active_operation_id"] is not None:
                raise WrenConfigurationError(
                    "数据源正在执行版本操作，请稍后修改。",
                    code="SOURCE_OPERATION_IN_PROGRESS",
                )
            cursor = connection.execute(
                "UPDATE wren_data_sources SET display_name=?, updated_at=? WHERE id=?",
                (name, self._now(), source_id),
            )
            if not cursor.rowcount:
                raise LookupError("数据源不存在。")
        return self.get_data_source(source_id)

    def get_data_source(self, source_id: str) -> WrenDataSource:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM wren_data_sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise LookupError("数据源不存在。")
        return self._source_from_row(row)

    def list_data_sources(self) -> list[WrenDataSource]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM wren_data_sources ORDER BY created_at, id"
            ).fetchall()
        return [self._source_from_row(row) for row in rows]

    def list_revisions(self, source_id: str) -> list[dict[str, Any]]:
        self.get_data_source(source_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, status, error_code, mdl_digest, created_at "
                "FROM wren_revisions WHERE source_id=? ORDER BY created_at DESC, id DESC",
                (source_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_revision(self, source_id: str, revision_id: str) -> WrenRevision:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM wren_revisions WHERE source_id=? AND id=?",
                (source_id, revision_id),
            ).fetchone()
        if row is None:
            raise LookupError("Wren 版本不存在。")
        return self._revision_from_row(row)

    def update_revision_artifacts(
        self,
        source_id: str,
        revision_id: str,
        *,
        project_dir: Path,
        profile_name: str,
        mdl_digest: str | None = None,
    ) -> WrenRevision:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE wren_revisions SET project_dir=?, profile_name=?, mdl_digest=?, updated_at=? "
                "WHERE source_id=? AND id=?",
                (str(project_dir.resolve()), profile_name, mdl_digest, self._now(), source_id, revision_id),
            )
            if not cursor.rowcount:
                raise LookupError("Wren 版本不存在。")
        return self.get_revision(source_id, revision_id)

    def update_revision_status(
        self,
        source_id: str,
        revision_id: str,
        status: str,
        *,
        error_code: str | None = None,
    ) -> WrenRevision:
        allowed = {
            "draft", "testing", "building", "active", "retired",
            "cleanup_pending", "expired", "failed",
        }
        if status not in allowed:
            raise ValueError("Wren 版本状态无效。")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE wren_revisions SET status=?, error_code=?, updated_at=? WHERE source_id=? AND id=?",
                (status, error_code, self._now(), source_id, revision_id),
            )
            if not cursor.rowcount:
                raise LookupError("Wren 版本不存在。")
        return self.get_revision(source_id, revision_id)

    def begin_source_operation(
        self,
        source_id: str,
        operation_type: str,
        *,
        base_revision_id: str | None,
        target_revision_id: str | None,
        expected_generation: int | None = None,
        expected_draft_revision_id: str | None = None,
        require_no_draft: bool = False,
        base_mdl_digest: str | None = None,
        actor_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Durably reserve one source generation and bind an operation to exact revisions."""
        allowed_types = {
            "settings_apply", "rollback", "business_rule_publish", "business_rule_remove",
            "revision_cleanup", "query_corpus_activate",
        }
        if operation_type not in allowed_types:
            raise ValueError("Wren 操作类型无效。")
        encoded_payload = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(encoded_payload) > 32_000:
            raise WrenConfigurationError("Wren 操作参数超过允许大小。")
        now = self._now()
        operation_id = f"op_{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            source = connection.execute(
                "SELECT * FROM wren_data_sources WHERE id=?", (source_id,)
            ).fetchone()
            if source is None:
                raise LookupError("数据源不存在。")
            state = connection.execute(
                "SELECT generation, active_operation_id FROM wren_source_operation_state WHERE source_id=?",
                (source_id,),
            ).fetchone()
            if state is None:
                raise WrenSettingsUnavailable("数据源发布状态缺失，请恢复存储后重试。")
            if state["active_operation_id"] is not None:
                raise WrenConfigurationError(
                    "该数据源有版本操作正在执行或等待恢复。",
                    code="SOURCE_OPERATION_IN_PROGRESS",
                )
            generation = int(state["generation"])
            if expected_generation is not None and generation != expected_generation:
                raise WrenConfigurationError(
                    "数据源版本已变化，请刷新后重试。", code="SOURCE_GENERATION_STALE"
                )
            if source["active_revision_id"] != base_revision_id:
                raise WrenConfigurationError(
                    "活动 Wren 版本已变化，请刷新后重试。", code="SOURCE_GENERATION_STALE"
                )
            if operation_type in {"rollback", "business_rule_publish", "query_corpus_activate"} and (
                not source["enabled"] or source["runtime_status"] != "ready"
            ):
                raise WrenConfigurationError(
                    "数据源当前没有可发布的活动 runtime。", code="DATA_SOURCE_UNAVAILABLE"
                )
            if operation_type == "business_rule_remove" and source["enabled"] and source["runtime_status"] != "ready":
                raise WrenConfigurationError(
                    "数据源正在恢复，规则下线暂不可执行。", code="DATA_SOURCE_UNAVAILABLE"
                )
            if expected_draft_revision_id is not None and source["draft_revision_id"] != expected_draft_revision_id:
                raise WrenConfigurationError(
                    "设置草稿已变化，请刷新后重试。", code="SOURCE_GENERATION_STALE"
                )
            if (require_no_draft or operation_type == "query_corpus_activate") and source["draft_revision_id"] is not None:
                raise WrenConfigurationError(
                    "请先应用或放弃当前设置草稿，再执行此版本操作。",
                    code="WREN_DRAFT_PENDING",
                )
            if operation_type in {"business_rule_publish", "rollback"}:
                has_rule_origins = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='business_rule_origins'"
                ).fetchone()
                if has_rule_origins is not None and connection.execute(
                    """SELECT 1 FROM business_rule_origins WHERE data_source_id=?
                       AND publication_status='removal_pending' LIMIT 1""",
                    (source_id,),
                ).fetchone():
                    raise WrenConfigurationError(
                        "该数据源有已撤销规则正在从 Wren 下线，请稍后重试。",
                        code="WREN_RULE_REMOVAL_PENDING",
                    )
            if operation_type == "settings_apply" and target_revision_id:
                draft = connection.execute(
                    "SELECT parent_revision_id FROM wren_revisions WHERE source_id=? AND id=?",
                    (source_id, target_revision_id),
                ).fetchone()
                if draft is None or draft["parent_revision_id"] != base_revision_id:
                    raise WrenConfigurationError(
                        "设置草稿基于旧 Wren 版本，请先重新保存草稿。",
                        code="WREN_DRAFT_STALE",
                    )
            if base_revision_id is not None:
                base = connection.execute(
                    "SELECT status, mdl_digest FROM wren_revisions WHERE source_id=? AND id=?",
                    (source_id, base_revision_id),
                ).fetchone()
                if base is None or base["status"] != "active":
                    raise WrenConfigurationError(
                        "当前数据源没有可用于发布的活动 Wren 版本。",
                        code="DATA_SOURCE_UNAVAILABLE",
                    )
                if base_mdl_digest is not None and base["mdl_digest"] != base_mdl_digest:
                    raise WrenConfigurationError(
                        "MDL 已变化，请重新审核候选。", code="SOURCE_GENERATION_STALE"
                    )
                base_mdl_digest = base["mdl_digest"]
            connection.execute(
                """INSERT INTO wren_operations
                   (id, source_id, revision_id, status, phase, operation_type,
                    base_revision_id, base_mdl_digest, base_generation,
                    target_revision_id, actor_id, payload_json, created_at, updated_at)
                   VALUES (?, ?, ?, 'running', 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    operation_id,
                    source_id,
                    target_revision_id,
                    operation_type,
                    base_revision_id,
                    base_mdl_digest,
                    generation,
                    target_revision_id,
                    actor_id,
                    encoded_payload,
                    now,
                    now,
                ),
            )
            changed = connection.execute(
                """UPDATE wren_source_operation_state
                   SET active_operation_id=?, updated_at=?
                   WHERE source_id=? AND generation=? AND active_operation_id IS NULL""",
                (operation_id, now, source_id, generation),
            )
            if changed.rowcount != 1:
                raise WrenConfigurationError(
                    "数据源正在执行其他版本操作。", code="SOURCE_OPERATION_IN_PROGRESS"
                )
        return self.get_operation(operation_id)

    def create_operation(self, source_id: str, revision_id: str | None, phase: str) -> dict[str, Any]:
        """Compatibility wrapper; new callers must use begin_source_operation."""
        source = self.get_data_source(source_id)
        state = self.source_operation_state(source_id)
        operation = self.begin_source_operation(
            source_id,
            "settings_apply",
            base_revision_id=source.active_revision_id,
            target_revision_id=revision_id,
            expected_generation=state["generation"],
            expected_draft_revision_id=revision_id,
        )
        if phase != "queued":
            self.update_operation(operation["id"], status="running", phase=phase)
        return self.get_operation(operation["id"])

    def source_operation_state(self, source_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT generation, active_operation_id FROM wren_source_operation_state WHERE source_id=?",
                (source_id,),
            ).fetchone()
        if row is None:
            raise LookupError("数据源不存在。")
        return {"generation": int(row["generation"]), "active_operation_id": row["active_operation_id"]}

    def create_revision_for_operation(
        self,
        operation_id: str,
        config: dict[str, Any],
        *,
        target_revision_id: str,
        copy_secrets_from: str,
    ) -> WrenRevision:
        safe_config = self._safe_config(config)
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM wren_operations WHERE id=? AND status='running'", (operation_id,)
            ).fetchone()
            if (
                operation is None
                or operation["target_revision_id"] != target_revision_id
                or operation["source_id"] is None
            ):
                raise WrenConfigurationError("发布操作与目标版本不匹配。", code="SOURCE_GENERATION_STALE")
            source_id = operation["source_id"]
            state = connection.execute(
                "SELECT generation, active_operation_id FROM wren_source_operation_state WHERE source_id=?",
                (source_id,),
            ).fetchone()
            source = connection.execute(
                "SELECT active_revision_id, draft_revision_id, enabled FROM wren_data_sources WHERE id=?",
                (source_id,),
            ).fetchone()
            if (
                state is None
                or state["active_operation_id"] != operation_id
                or state["generation"] != operation["base_generation"]
                or source is None
                or source["active_revision_id"] != operation["base_revision_id"]
                or (
                    source["draft_revision_id"] is not None
                    and operation["operation_type"] != "business_rule_remove"
                )
            ):
                raise WrenConfigurationError("数据源发布基线已变化。", code="SOURCE_GENERATION_STALE")
            connection.execute(
                """INSERT INTO wren_revisions
                   (id, source_id, status, config_json, parent_revision_id,
                    publication_operation_id, created_at, updated_at)
                   VALUES (?, ?, 'draft', ?, ?, ?, ?, ?)""",
                (
                    target_revision_id,
                    source_id,
                    json.dumps(safe_config, ensure_ascii=False),
                    operation["base_revision_id"],
                    operation_id,
                    now,
                    now,
                ),
            )
            rows = connection.execute(
                "SELECT secret_name, ciphertext FROM wren_secrets WHERE source_id=? AND revision_id=?",
                (source_id, copy_secrets_from),
            ).fetchall()
            if rows:
                cipher = self._cipher()
                secrets: dict[str, str] = {}
                for row in rows:
                    try:
                        secrets[row["secret_name"]] = cipher.decrypt(row["ciphertext"]).decode("utf-8")
                    except (InvalidToken, UnicodeDecodeError, TypeError, ValueError) as exc:
                        raise WrenSettingsUnavailable(
                            "Wren 数据源凭证无法解密，请检查部署加密密钥。"
                        ) from exc
                self._insert_secrets(connection, source_id, target_revision_id, secrets)
            connection.execute(
                "UPDATE wren_operations SET phase='building_target', updated_at=? WHERE id=?",
                (now, operation_id),
            )
        return self.get_revision(source_id, target_revision_id)

    def record_operation_target_digest(
        self, operation_id: str, target_revision_id: str, mdl_digest: str
    ) -> None:
        now = self._now()
        with self._connect() as connection:
            operation = connection.execute(
                "SELECT source_id, target_revision_id, status FROM wren_operations WHERE id=?",
                (operation_id,),
            ).fetchone()
            if (
                operation is None or operation["status"] != "running"
                or operation["target_revision_id"] != target_revision_id
            ):
                raise WrenConfigurationError("发布操作与目标版本不匹配。", code="SOURCE_GENERATION_STALE")
            connection.execute(
                "UPDATE wren_operations SET target_mdl_digest=?, updated_at=? WHERE id=?",
                (mdl_digest, now, operation_id),
            )

    def activate_revision_for_operation(
        self, operation_id: str, *, source_id: str, target_revision_id: str
    ) -> WrenDataSource:
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM wren_operations WHERE id=? AND source_id=? AND status='running'",
                (operation_id, source_id),
            ).fetchone()
            state = connection.execute(
                "SELECT generation, active_operation_id FROM wren_source_operation_state WHERE source_id=?",
                (source_id,),
            ).fetchone()
            source = connection.execute(
                "SELECT active_revision_id, draft_revision_id FROM wren_data_sources WHERE id=?",
                (source_id,),
            ).fetchone()
            revision = connection.execute(
                "SELECT status, mdl_digest, parent_revision_id, publication_operation_id, config_json "
                "FROM wren_revisions WHERE source_id=? AND id=?",
                (source_id, target_revision_id),
            ).fetchone()
            if (
                operation is None or state is None or source is None or revision is None
                or operation["target_revision_id"] != target_revision_id
                or state["active_operation_id"] != operation_id
                or state["generation"] != operation["base_generation"]
                or source["active_revision_id"] != operation["base_revision_id"]
                or operation["target_mdl_digest"] != revision["mdl_digest"]
                or not revision["mdl_digest"]
                or revision["status"] not in {"building", "draft", "retired", "active"}
            ):
                raise WrenConfigurationError("发布基线已变化，不能激活目标版本。", code="SOURCE_GENERATION_STALE")
            if operation["operation_type"] in {"business_rule_publish", "business_rule_remove"} and (
                revision["parent_revision_id"] != operation["base_revision_id"]
                or revision["publication_operation_id"] != operation_id
            ):
                raise WrenConfigurationError(
                    "目标 Wren 版本没有绑定到该发布操作的实际基线。",
                    code="SOURCE_GENERATION_STALE",
                )
            if operation["operation_type"] == "settings_apply" and source["draft_revision_id"] != target_revision_id:
                raise WrenConfigurationError("设置草稿已变化，不能激活此版本。", code="SOURCE_GENERATION_STALE")
            if operation["operation_type"] in {"settings_apply", "rollback"}:
                origins_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='business_rule_origins'"
                ).fetchone()
                if origins_table is not None:
                    suppressions_table = connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_memory_suppressions'"
                    ).fetchone()
                    if suppressions_table is None:
                        raise WrenConfigurationError(
                            "业务规则删除状态不可验证。", code="SOURCE_RECOVERY_REQUIRED"
                        )
                    suppressed_rows = connection.execute(
                        """SELECT business_rule_id FROM business_rule_origins
                           WHERE data_source_id=? AND publication_status IN ('removal_pending','removed')""",
                        (source_id,),
                    ).fetchall()
                    suppressed_names = {
                        f"askdb_br_{row['business_rule_id']}" for row in suppressed_rows
                    }
                    journal_suppressions = connection.execute(
                        """SELECT item_id FROM agent_memory_suppressions
                           WHERE data_source_id=? AND item_type='business_rule'""",
                        (source_id,),
                    ).fetchall()
                    suppressed_names.update(
                        f"askdb_br_{row['item_id']}" for row in journal_suppressions
                    )
                    try:
                        configured_rules = json.loads(revision["config_json"]).get("rules", [])
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise WrenConfigurationError(
                            "Wren 版本的规则配置无效。", code="SOURCE_RECOVERY_REQUIRED"
                        ) from exc
                    restored = {
                        str(item.get("name", ""))
                        for item in configured_rules
                        if isinstance(item, dict)
                    } & suppressed_names
                    if restored:
                        raise WrenConfigurationError(
                            "此版本包含已删除的业务规则，不能回滚或发布。",
                            code="WREN_SUPPRESSED_RULE_RESTORE",
                        )
            if operation["operation_type"] == "business_rule_publish":
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                        "('business_rule_candidates','agent_conversation_threads','agent_memory_suppressions')"
                    )
                }
                rule_id = (json.loads(operation["payload_json"] or "{}")).get("business_rule_id")
                required_tables = {
                    "business_rule_candidates", "agent_conversation_threads", "agent_memory_suppressions"
                }
                if not isinstance(rule_id, str) or not required_tables.issubset(tables):
                    raise WrenConfigurationError(
                        "业务规则发布状态不可验证。", code="SOURCE_RECOVERY_REQUIRED"
                    )
                candidate = connection.execute(
                    """SELECT candidate.review_status, candidate.publication_status,
                              thread.status AS thread_status, thread.expires_at
                       FROM business_rule_candidates AS candidate
                       JOIN agent_conversation_threads AS thread
                         ON thread.thread_id=candidate.source_thread_id
                       WHERE candidate.data_source_id=? AND candidate.business_rule_id=?""",
                    (source_id, rule_id),
                ).fetchone()
                suppressed = connection.execute(
                    """SELECT 1 FROM agent_memory_suppressions
                       WHERE data_source_id=? AND item_type='business_rule' AND item_id=? LIMIT 1""",
                    (source_id, rule_id),
                ).fetchone()
                expiry = datetime.fromisoformat(candidate["expires_at"]) if candidate else None
                expiry = expiry.replace(tzinfo=UTC) if expiry and expiry.tzinfo is None else expiry
                if (
                    candidate is None
                    or candidate["review_status"] != "approved"
                    or candidate["publication_status"] != "publishing"
                    or candidate["thread_status"] != "active"
                    or expiry is None
                    or expiry <= datetime.now(UTC)
                    or suppressed is not None
                ):
                    raise WrenConfigurationError(
                        "候选已删除、过期或撤销，不能发布。", code="SOURCE_GENERATION_STALE"
                    )
            if operation["operation_type"] == "business_rule_remove":
                payload = json.loads(operation["payload_json"] or "{}")
                rule_ids = tuple(sorted(set(payload.get("business_rule_ids", ()))))
                if not rule_ids or len(rule_ids) > 500 or any(not isinstance(item, str) for item in rule_ids):
                    raise WrenConfigurationError("规则下线操作没有有效目标。", code="SOURCE_GENERATION_STALE")
                table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='business_rule_origins'"
                ).fetchone()
                if table is None:
                    raise WrenConfigurationError("业务规则下线状态不可验证。", code="SOURCE_RECOVERY_REQUIRED")
                placeholders = ",".join("?" for _ in rule_ids)
                pending = connection.execute(
                    f"""SELECT business_rule_id FROM business_rule_origins
                        WHERE data_source_id=? AND publication_status='removal_pending'
                          AND business_rule_id IN ({placeholders})""",
                    (source_id, *rule_ids),
                ).fetchall()
                if {row["business_rule_id"] for row in pending} != set(rule_ids):
                    raise WrenConfigurationError("规则下线队列已变化。", code="SOURCE_GENERATION_STALE")
            connection.execute(
                "UPDATE wren_revisions SET status='retired', updated_at=? "
                "WHERE source_id=? AND status='active' AND id<>?",
                (now, source_id, target_revision_id),
            )
            connection.execute(
                "UPDATE wren_revisions SET status='active', error_code=NULL, updated_at=? WHERE source_id=? AND id=?",
                (now, source_id, target_revision_id),
            )
            activated_generation = int(operation["base_generation"]) + 1
            source_update = connection.execute(
                """UPDATE wren_data_sources SET active_revision_id=?,
                       draft_revision_id=CASE WHEN draft_revision_id=? THEN NULL ELSE draft_revision_id END,
                       runtime_status=CASE WHEN enabled=1 THEN 'ready' ELSE 'disabled' END,
                       updated_at=? WHERE id=? AND active_revision_id IS ?""",
                (target_revision_id, target_revision_id, now, source_id, operation["base_revision_id"]),
            )
            if source_update.rowcount != 1:
                raise WrenConfigurationError("活动版本指针已变化。", code="SOURCE_GENERATION_STALE")
            state_update = connection.execute(
                "UPDATE wren_source_operation_state SET generation=?, updated_at=? "
                "WHERE source_id=? AND generation=? AND active_operation_id=?",
                (activated_generation, now, source_id, operation["base_generation"], operation_id),
            )
            if state_update.rowcount != 1:
                raise WrenConfigurationError("数据源 generation 已变化。", code="SOURCE_GENERATION_STALE")
            operation_update = connection.execute(
                """UPDATE wren_operations SET phase='generation_persisted',
                       activated_generation=?, updated_at=? WHERE id=?""",
                (activated_generation, now, operation_id),
            )
            if operation_update.rowcount != 1:
                raise WrenConfigurationError("无法持久化活动 Wren 版本。", code="SOURCE_GENERATION_STALE")
        return self.get_data_source(source_id)

    def complete_source_operation(self, operation_id: str) -> dict[str, Any]:
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM wren_operations WHERE id=? AND status='running'", (operation_id,)
            ).fetchone()
            if operation is None:
                raise LookupError("操作不存在或已完成。")
            state = connection.execute(
                "SELECT generation, active_operation_id FROM wren_source_operation_state WHERE source_id=?",
                (operation["source_id"],),
            ).fetchone()
            source = connection.execute(
                "SELECT active_revision_id FROM wren_data_sources WHERE id=?",
                (operation["source_id"],),
            ).fetchone()
            expected_active_revision = (
                operation["base_revision_id"]
                if operation["operation_type"] == "query_corpus_activate"
                else operation["target_revision_id"]
            )
            if (
                state is None or source is None
                or state["active_operation_id"] != operation_id
                or source["active_revision_id"] != expected_active_revision
                or state["generation"] != operation["activated_generation"]
                or operation["phase"] not in {"generation_persisted", "runtime_activated"}
            ):
                raise WrenConfigurationError("运行时尚未与持久活动版本一致。", code="SOURCE_RECOVERY_REQUIRED")
            if operation["operation_type"] == "query_corpus_activate":
                changed = connection.execute(
                    """UPDATE wren_data_sources SET runtime_status='ready', updated_at=?
                       WHERE id=? AND enabled=1 AND active_revision_id=?
                         AND runtime_status='unavailable'""",
                    (now, operation["source_id"], operation["base_revision_id"]),
                )
                if changed.rowcount != 1:
                    raise WrenConfigurationError(
                        "查询语料运行时尚未与持久活动版本一致。",
                        code="SOURCE_RECOVERY_REQUIRED",
                    )
            connection.execute(
                "UPDATE wren_operations SET status='active', phase='active', updated_at=? WHERE id=?",
                (now, operation_id),
            )
            connection.execute(
                "UPDATE wren_source_operation_state SET active_operation_id=NULL, updated_at=? "
                "WHERE source_id=? AND active_operation_id=?",
                (now, operation["source_id"], operation_id),
            )
        return self.get_operation(operation_id)

    def fail_source_operation(
        self, operation_id: str, *, error_code: str, message: str
    ) -> dict[str, Any]:
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM wren_operations WHERE id=? AND status='running'", (operation_id,)
            ).fetchone()
            if operation is None:
                return self.get_operation(operation_id)
            source = connection.execute(
                "SELECT active_revision_id FROM wren_data_sources WHERE id=?",
                (operation["source_id"],),
            ).fetchone()
            state = connection.execute(
                "SELECT generation, active_operation_id FROM wren_source_operation_state WHERE source_id=?",
                (operation["source_id"],),
            ).fetchone()
            if (
                source is None or state is None
                or source["active_revision_id"] != operation["base_revision_id"]
                or state["generation"] != operation["base_generation"]
                or state["active_operation_id"] != operation_id
            ):
                raise WrenConfigurationError(
                    "版本已写入活动指针，必须通过启动恢复完成协调。",
                    code="SOURCE_RECOVERY_REQUIRED",
                )
            connection.execute(
                "UPDATE wren_operations SET status='failed', phase='failed', error_code=?, message=?, updated_at=? WHERE id=?",
                (error_code, message[:500], now, operation_id),
            )
            connection.execute(
                "UPDATE wren_source_operation_state SET active_operation_id=NULL, updated_at=? "
                "WHERE source_id=? AND active_operation_id=?",
                (now, operation["source_id"], operation_id),
            )
            if operation["target_revision_id"] and operation["operation_type"] not in {
                "rollback", "revision_cleanup"
            }:
                connection.execute(
                    "UPDATE wren_revisions SET status='failed', error_code=?, updated_at=? "
                    "WHERE source_id=? AND id=? AND status<>'active'",
                    (error_code, now, operation["source_id"], operation["target_revision_id"]),
                )
        return self.get_operation(operation_id)

    def pending_source_operations(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM wren_operations WHERE status='running' ORDER BY created_at, id"
            ).fetchall()
        return [self.get_operation(row["id"]) for row in rows]

    def list_revision_cleanup_candidates(
        self, *, cutoff: str, limit: int = 500
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 5000:
            raise ValueError("revision cleanup page size is invalid")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT revision.source_id, revision.id, revision.project_dir,
                          source.active_revision_id
                   FROM wren_revisions AS revision
                   JOIN wren_data_sources AS source ON source.id=revision.source_id
                   WHERE revision.id<>source.active_revision_id
                     AND revision.id IS NOT source.draft_revision_id
                     AND (revision.status='cleanup_pending'
                       OR (revision.status='retired' AND revision.project_dir IS NOT NULL
                           AND revision.updated_at<=?)
                       OR (revision.status='failed' AND revision.updated_at<=?))
                   ORDER BY revision.source_id, revision.updated_at, revision.id LIMIT ?""",
                (cutoff, cutoff, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_revision_cleanup_pending(
        self, operation_id: str, *, revision_ids: tuple[str, ...], cutoff: str
    ) -> tuple[str, ...]:
        if not revision_ids or len(revision_ids) > 500:
            return ()
        now = self._now()
        marked: list[str] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM wren_operations WHERE id=? AND status='running'",
                (operation_id,),
            ).fetchone()
            if operation is None or operation["operation_type"] != "revision_cleanup":
                raise WrenConfigurationError("版本清理操作无效。", code="SOURCE_GENERATION_STALE")
            state = connection.execute(
                "SELECT generation, active_operation_id FROM wren_source_operation_state WHERE source_id=?",
                (operation["source_id"],),
            ).fetchone()
            source = connection.execute(
                "SELECT active_revision_id FROM wren_data_sources WHERE id=?",
                (operation["source_id"],),
            ).fetchone()
            if (
                state is None or source is None
                or state["active_operation_id"] != operation_id
                or state["generation"] != operation["base_generation"]
                or source["active_revision_id"] != operation["base_revision_id"]
            ):
                raise WrenConfigurationError("数据源版本已变化，不能清理历史版本。", code="SOURCE_GENERATION_STALE")
            for revision_id in sorted(set(revision_ids)):
                row = connection.execute(
                    "SELECT status, updated_at FROM wren_revisions WHERE source_id=? AND id=?",
                    (operation["source_id"], revision_id),
                ).fetchone()
                if (
                    row is None or row["status"] not in {"retired", "failed", "cleanup_pending"}
                    or operation["base_revision_id"] == revision_id
                ):
                    continue
                if row["status"] in {"retired", "failed"} and row["updated_at"] > cutoff:
                    continue
                connection.execute(
                    "UPDATE wren_revisions SET status='cleanup_pending', updated_at=? "
                    "WHERE source_id=? AND id=? AND status IN ('retired','failed','cleanup_pending')",
                    (now, operation["source_id"], revision_id),
                )
                marked.append(revision_id)
            connection.execute(
                "UPDATE wren_operations SET phase='cleaning_artifacts', payload_json=?, updated_at=? WHERE id=?",
                (
                    json.dumps({"revision_ids": marked}, separators=(",", ":")),
                    now,
                    operation_id,
                ),
            )
        return tuple(marked)

    def finalize_revision_cleanup(self, operation_id: str, revision_id: str) -> None:
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT source_id, status, operation_type FROM wren_operations WHERE id=?",
                (operation_id,),
            ).fetchone()
            state = connection.execute(
                "SELECT active_operation_id, generation FROM wren_source_operation_state WHERE source_id=?",
                (operation["source_id"],),
            ).fetchone() if operation else None
            revision = connection.execute(
                "SELECT status FROM wren_revisions WHERE source_id=? AND id=?",
                (operation["source_id"], revision_id),
            ).fetchone() if operation else None
            if (
                operation is None or operation["status"] != "running"
                or operation["operation_type"] != "revision_cleanup"
                or state is None or state["active_operation_id"] != operation_id
                or revision is None or revision["status"] != "cleanup_pending"
            ):
                raise WrenConfigurationError("历史版本清理状态已变化。", code="SOURCE_GENERATION_STALE")
            connection.execute(
                "DELETE FROM wren_secrets WHERE source_id=? AND revision_id=?",
                (operation["source_id"], revision_id),
            )
            connection.execute(
                """UPDATE wren_revisions SET status='expired', config_json='{}',
                       project_dir=NULL, profile_name=NULL, error_code=NULL, updated_at=?
                   WHERE source_id=? AND id=? AND status='cleanup_pending'""",
                (now, operation["source_id"], revision_id),
            )

    def complete_maintenance_operation(self, operation_id: str) -> dict[str, Any]:
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM wren_operations WHERE id=? AND status='running'",
                (operation_id,),
            ).fetchone()
            if operation is None or operation["operation_type"] != "revision_cleanup":
                raise LookupError("清理操作不存在或已完成。")
            source = connection.execute(
                "SELECT active_revision_id FROM wren_data_sources WHERE id=?",
                (operation["source_id"],),
            ).fetchone()
            state = connection.execute(
                "SELECT generation, active_operation_id FROM wren_source_operation_state WHERE source_id=?",
                (operation["source_id"],),
            ).fetchone()
            if (
                source is None or state is None
                or source["active_revision_id"] != operation["base_revision_id"]
                or state["generation"] != operation["base_generation"]
                or state["active_operation_id"] != operation_id
            ):
                raise WrenConfigurationError("数据源版本已变化，清理操作需要恢复。", code="SOURCE_RECOVERY_REQUIRED")
            connection.execute(
                "UPDATE wren_operations SET status='active', phase='active', updated_at=? WHERE id=?",
                (now, operation_id),
            )
            connection.execute(
                "UPDATE wren_source_operation_state SET active_operation_id=NULL, updated_at=? "
                "WHERE source_id=? AND active_operation_id=?",
                (now, operation["source_id"], operation_id),
            )
        return self.get_operation(operation_id)

    def update_operation(
        self,
        operation_id: str,
        *,
        status: str,
        phase: str,
        error_code: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"running", "active", "failed"}:
            raise ValueError("operation 状态无效。")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE wren_operations SET status=?, phase=?, error_code=?, message=?, updated_at=? WHERE id=?",
                (status, phase, error_code, message, self._now(), operation_id),
            )
            if not cursor.rowcount:
                raise LookupError("操作不存在。")
        return self.get_operation(operation_id)

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM wren_operations WHERE id=?", (operation_id,)).fetchone()
        if row is None:
            raise LookupError("操作不存在。")
        return {
            "id": row["id"],
            "data_source_id": row["source_id"],
            "revision_id": row["revision_id"],
            "status": row["status"],
            "phase": row["phase"],
            "error_code": row["error_code"],
            "message": row["message"],
            "operation_type": row["operation_type"],
            "base_revision_id": row["base_revision_id"],
            "base_mdl_digest": row["base_mdl_digest"],
            "base_generation": row["base_generation"],
            "target_revision_id": row["target_revision_id"],
            "target_mdl_digest": row["target_mdl_digest"],
            "activated_generation": row["activated_generation"],
            "actor_id": row["actor_id"],
            "payload": json.loads(row["payload_json"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def latest_operation(self, source_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM wren_operations WHERE source_id=? ORDER BY created_at DESC LIMIT 1",
                (source_id,),
            ).fetchone()
        return self.get_operation(row["id"]) if row else None

    def source_detail(self, source_id: str) -> dict[str, Any]:
        source = self.get_data_source(source_id)
        revision_id = source.draft_revision_id or source.active_revision_id
        revision = self.get_revision(source_id, revision_id) if revision_id else None
        config = revision.config if revision else {}
        active_revision = (
            self.get_revision(source_id, source.active_revision_id)
            if source.active_revision_id else None
        )
        secrets = self.get_secrets(source_id, revision_id) if revision_id else {}
        return {
            "data_source": {
                "id": source.id,
                "display_name": source.display_name,
                "connector_type": source.connector_type,
                "enabled": source.enabled,
                "active_revision_id": source.active_revision_id,
                "draft_revision_id": source.draft_revision_id,
                "runtime_status": source.runtime_status,
                "created_at": source.created_at,
                "updated_at": source.updated_at,
            },
            "connection": safe_connection_projection(config) | {
                "configured_secret_fields": sorted(secrets),
                "credential_configured": bool(secrets),
            },
            "config": config,
            "active_config": active_revision.config if active_revision else {},
            "revision": {
                "id": revision.id,
                "status": revision.status,
                "error_code": revision.error_code,
                "mdl_digest": revision.mdl_digest,
            } if revision else None,
            "revisions": self.list_revisions(source_id),
            "last_operation": self.latest_operation(source_id),
        }

    def get_secret(self, source_id: str, revision_id: str, secret_name: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT ciphertext FROM wren_secrets WHERE source_id=? AND revision_id=? AND secret_name=?",
                (source_id, revision_id, secret_name),
            ).fetchone()
        if row is None:
            return None
        try:
            return self._cipher().decrypt(row["ciphertext"]).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, TypeError, ValueError) as exc:
            raise WrenSettingsUnavailable("Wren 数据源凭证无法解密，请检查部署加密密钥。") from exc

    def get_secrets(self, source_id: str, revision_id: str) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT secret_name FROM wren_secrets WHERE source_id=? AND revision_id=?",
                (source_id, revision_id),
            ).fetchall()
        return {
            row["secret_name"]: value
            for row in rows
            if (value := self.get_secret(source_id, revision_id, row["secret_name"])) is not None
        }

    def get_default_source(self) -> WrenDataSource | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT s.* FROM wren_state state
                   JOIN wren_data_sources s ON s.id=state.default_data_source_id
                   WHERE state.id=1"""
            ).fetchone()
        return self._source_from_row(row) if row else None

    def set_migration_status(self, status: str) -> None:
        if status not in {"not_started", "complete", "failed", "not_configured"}:
            raise ValueError("迁移状态无效。")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO wren_state(id, migration_status, updated_at) VALUES (1, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET migration_status=excluded.migration_status,
                   updated_at=excluded.updated_at""",
                (status, self._now()),
            )

    def get_migration_status(self) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT migration_status FROM wren_state WHERE id=1"
            ).fetchone()
        return row["migration_status"] if row and row["migration_status"] else "not_started"

    def set_default(self, source_id: str | None) -> str | None:
        now = self._now()
        with self._connect() as connection:
            if source_id is not None:
                source = connection.execute(
                    "SELECT enabled, active_revision_id, runtime_status FROM wren_data_sources WHERE id=?",
                    (source_id,),
                ).fetchone()
                if source is None:
                    raise LookupError("数据源不存在。")
                if not source["enabled"]:
                    raise ValueError("默认数据源必须处于启用状态。")
                if not source["active_revision_id"] or source["runtime_status"] != "ready":
                    raise ValueError("默认数据源必须已有可用的活动 runtime。")
            connection.execute(
                """INSERT INTO wren_state(id, default_data_source_id, updated_at) VALUES (1, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET default_data_source_id=excluded.default_data_source_id,
                   updated_at=excluded.updated_at""",
                (source_id, now),
            )
        return source_id

    def set_enabled(self, source_id: str, enabled: bool) -> WrenDataSource:
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT active_operation_id FROM wren_source_operation_state WHERE source_id=?",
                (source_id,),
            ).fetchone()
            if operation and operation["active_operation_id"] is not None:
                raise WrenConfigurationError(
                    "数据源正在执行版本操作，请稍后再启用或停用。",
                    code="SOURCE_OPERATION_IN_PROGRESS",
                )
            cursor = connection.execute(
                "UPDATE wren_data_sources SET enabled=?, updated_at=? WHERE id=?",
                (int(enabled), now, source_id),
            )
            if cursor.rowcount == 0:
                raise LookupError("数据源不存在。")
            if not enabled:
                connection.execute(
                    "UPDATE wren_state SET default_data_source_id=NULL, updated_at=? WHERE default_data_source_id=?",
                    (now, source_id),
                )
                connection.execute(
                    "UPDATE wren_data_sources SET runtime_status='disabled' WHERE id=?", (source_id,)
                )
            else:
                connection.execute(
                    "UPDATE wren_data_sources SET runtime_status=CASE WHEN active_revision_id IS NULL "
                    "THEN 'not_ready' ELSE 'ready' END WHERE id=?",
                    (source_id,),
                )
            connection.execute(
                "UPDATE wren_source_operation_state SET generation=generation+1, updated_at=? WHERE source_id=?",
                (now, source_id),
            )
        return self.get_data_source(source_id)

    def activate_revision(self, source_id: str, revision_id: str) -> WrenDataSource:
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation_state = connection.execute(
                "SELECT generation, active_operation_id FROM wren_source_operation_state WHERE source_id=?",
                (source_id,),
            ).fetchone()
            if operation_state and operation_state["active_operation_id"] is not None:
                raise WrenConfigurationError(
                    "数据源正在执行版本操作，请稍后重试。",
                    code="SOURCE_OPERATION_IN_PROGRESS",
                )
            revision = connection.execute(
                "SELECT id FROM wren_revisions WHERE id=? AND source_id=?",
                (revision_id, source_id),
            ).fetchone()
            if revision is None:
                raise LookupError("Wren 版本不存在。")
            connection.execute(
                "UPDATE wren_revisions SET status='retired', updated_at=? "
                "WHERE source_id=? AND status='active' AND id<>?",
                (now, source_id, revision_id),
            )
            connection.execute(
                "UPDATE wren_revisions SET status='active', error_code=NULL, updated_at=? WHERE id=?",
                (now, revision_id),
            )
            connection.execute(
                "UPDATE wren_data_sources SET active_revision_id=?, "
                "draft_revision_id=CASE WHEN draft_revision_id=? THEN NULL ELSE draft_revision_id END, "
                "runtime_status='ready', updated_at=? WHERE id=?",
                (revision_id, revision_id, now, source_id),
            )
            connection.execute(
                "UPDATE wren_source_operation_state SET generation=generation+1, updated_at=? WHERE source_id=?",
                (now, source_id),
            )
        return self.get_data_source(source_id)

    def update_runtime_status(self, source_id: str, status: str) -> WrenDataSource:
        allowed = {"not_ready", "ready", "unavailable", "disabled"}
        if status not in allowed:
            raise ValueError("运行状态无效。")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE wren_data_sources SET runtime_status=?, updated_at=? WHERE id=?",
                (status, self._now(), source_id),
            )
            if not cursor.rowcount:
                raise LookupError("数据源不存在。")
        return self.get_data_source(source_id)

    def get_thread_binding(self, thread_id: str) -> tuple[str, str | None] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT data_source_id, owner_user_id FROM chat_thread_data_sources WHERE thread_id=?",
                (thread_id,),
            ).fetchone()
        if row is None:
            return None
        return row["data_source_id"], row["owner_user_id"]

    def bind_thread_source(
        self,
        thread_id: str,
        owner_user_id: str,
        source_id: str,
        role: str,
    ) -> str:
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account = connection.execute(
                "SELECT role, is_active FROM auth_users WHERE id=?", (owner_user_id,)
            ).fetchone()
            if account is None or not account["is_active"] or account["role"] != role:
                raise ChatThreadOwnerMismatch("当前登录状态已变更，请重新登录。")
            existing = connection.execute(
                "SELECT data_source_id, owner_user_id FROM chat_thread_data_sources WHERE thread_id=?",
                (thread_id,),
            ).fetchone()
            if existing:
                if existing["owner_user_id"] is None:
                    raise ChatLegacyThreadRequiresNew("旧匿名会话不能继续使用，请新建会话。")
                if existing["owner_user_id"] != owner_user_id:
                    raise ChatThreadOwnerMismatch("此会话不属于当前账号。")
                if existing["data_source_id"] != source_id:
                    raise ChatDataSourceMismatch("会话已绑定到其他数据源。")
            source = connection.execute(
                "SELECT id, enabled FROM wren_data_sources WHERE id=?", (source_id,)
            ).fetchone()
            if source is None or not source["enabled"]:
                raise ChatDataSourceUnavailable("所选数据源当前不可用。")
            if role == "member":
                grant = connection.execute(
                    """SELECT 1 FROM auth_user_data_sources
                       WHERE user_id=? AND data_source_id=?""",
                    (owner_user_id, source_id),
                ).fetchone()
                if grant is None:
                    raise ChatDataSourceForbidden("当前账号没有权限使用此数据源。")
            if existing:
                return existing["data_source_id"]
            connection.execute(
                """INSERT INTO chat_thread_data_sources
                   (thread_id, data_source_id, owner_user_id, created_at) VALUES (?, ?, ?, ?)""",
                (thread_id, source_id, owner_user_id, now),
            )
        return source_id

    def get_thread_source(self, thread_id: str) -> str | None:
        binding = self.get_thread_binding(thread_id)
        return binding[0] if binding else None

    def source_is_referenced(self, source_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM chat_thread_data_sources WHERE data_source_id=? LIMIT 1",
                (source_id,),
            ).fetchone() is not None

    def public_catalog(self) -> dict[str, Any]:
        default = self.get_default_source()
        sources = self.list_data_sources()
        return {
            "default_data_source_id": default.id if default else None,
            "migration_status": self.get_migration_status(),
            "data_sources": [
                {
                    "id": source.id,
                    "display_name": source.display_name,
                    "connector_type": source.connector_type,
                    "enabled": source.enabled,
                    "runtime_status": source.runtime_status,
                    "is_default": source.id == (default.id if default else None),
                }
                for source in sources
            ],
        }
