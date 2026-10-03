from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

from domain.auth import (
    AccountNotFound,
    AdminPrivilegeRequired,
    AuthStoreUnavailable,
    BootstrapAlreadyInitialized,
    DataSourceGrantError,
    InvalidSession,
    LastActiveAdmin,
    StoredUser,
    UserRole,
    UsernameConflict,
)


IDLE_TIMEOUT = timedelta(minutes=30)
ABSOLUTE_TIMEOUT = timedelta(hours=8)
LOGIN_WINDOW = timedelta(minutes=15)
MAX_LOGIN_FAILURES = 5
MAX_LOGIN_BACKOFF = timedelta(minutes=15)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _row_user(row: sqlite3.Row) -> StoredUser:
    role = row["role"]
    if role not in {"admin", "member"}:
        raise AuthStoreUnavailable("账号存储格式无效。")
    return StoredUser(
        id=row["id"],
        username=row["username"],
        username_key=row["username_key"],
        role=role,
        password_hash=row["password_hash"],
        is_active=bool(row["is_active"]),
        must_change_password=bool(row["must_change_password"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_login_at=row["last_login_at"],
    )


class AuthStore:
    """Local account, session, grant, throttle, and security-audit store."""

    def __init__(self, database_path: Path | None = None):
        configured_path = database_path or Path(
            os.environ.get(
                "ASKDB_SETTINGS_DB_PATH",
                str(Path(__file__).resolve().parents[1] / "data" / "model-settings.sqlite3"),
            )
        )
        self.database_path = configured_path.expanduser().resolve()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            connection = sqlite3.connect(self.database_path, timeout=5)
            os.chmod(self.database_path, 0o600)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                connection.rollback()
            raise AuthStoreUnavailable("账号存储当前不可用。") from exc
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS auth_users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    username_key TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
                    password_hash TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    must_change_password INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES auth_users(id),
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    idle_expires_at TEXT NOT NULL,
                    absolute_expires_at TEXT NOT NULL,
                    revoked_at TEXT
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user "
                "ON auth_sessions(user_id, revoked_at)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS auth_user_data_sources (
                    user_id TEXT NOT NULL REFERENCES auth_users(id),
                    data_source_id TEXT NOT NULL,
                    granted_by TEXT NOT NULL REFERENCES auth_users(id),
                    granted_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, data_source_id)
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_user_data_sources_source "
                "ON auth_user_data_sources(data_source_id)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS auth_login_throttles (
                    username_key TEXT PRIMARY KEY,
                    failed_count INTEGER NOT NULL,
                    window_started_at TEXT NOT NULL,
                    blocked_until TEXT,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS auth_audit_events (
                    id TEXT PRIMARY KEY,
                    actor_user_id TEXT,
                    target_user_id TEXT,
                    action TEXT NOT NULL,
                    request_id TEXT,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_audit_created "
                "ON auth_audit_events(created_at)"
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS auth_schema_meta (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    schema_version INTEGER NOT NULL
                )"""
            )
            connection.execute(
                "INSERT OR IGNORE INTO auth_schema_meta(id, schema_version) VALUES (1, 1)"
            )
            connection.commit()

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        *,
        actor_user_id: str | None,
        target_user_id: str | None,
        action: str,
        summary: dict[str, object],
        request_id: str | None = None,
    ) -> None:
        import uuid

        connection.execute(
            """INSERT INTO auth_audit_events
               (id, actor_user_id, target_user_id, action, request_id, summary_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                uuid.uuid4().hex,
                actor_user_id,
                target_user_id,
                action,
                request_id,
                json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                _timestamp(utc_now()),
            ),
        )

    def _insert_user(
        self,
        connection: sqlite3.Connection,
        *,
        user_id: str,
        username: str,
        username_key: str,
        role: UserRole,
        password_hash: str,
        actor_user_id: str | None,
        request_id: str | None = None,
    ) -> StoredUser:
        duplicate = connection.execute(
            "SELECT 1 FROM auth_users WHERE username_key=?", (username_key,)
        ).fetchone()
        if duplicate:
            raise UsernameConflict("用户名已存在。")
        now = _timestamp(utc_now())
        connection.execute(
            """INSERT INTO auth_users
               (id, username, username_key, role, password_hash, is_active,
                must_change_password, created_at, updated_at, last_login_at)
               VALUES (?, ?, ?, ?, ?, 1, 1, ?, ?, NULL)""",
            (user_id, username, username_key, role, password_hash, now, now),
        )
        self._audit(
            connection,
            actor_user_id=actor_user_id,
            target_user_id=user_id,
            action="user.create",
            summary={"role": role},
            request_id=request_id,
        )
        row = connection.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise AuthStoreUnavailable("账号创建后无法读取。")
        return _row_user(row)

    @staticmethod
    def _require_admin_actor(connection: sqlite3.Connection, actor_user_id: str) -> None:
        actor = connection.execute(
            "SELECT role, is_active FROM auth_users WHERE id=?", (actor_user_id,)
        ).fetchone()
        if actor is None or not actor["is_active"] or actor["role"] != "admin":
            raise AdminPrivilegeRequired("当前账号没有管理员权限。")

    def create_bootstrap_admin(
        self, *, user_id: str, username: str, username_key: str, password_hash: str
    ) -> StoredUser:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = connection.execute("SELECT COUNT(*) FROM auth_users").fetchone()[0]
            if count:
                raise BootstrapAlreadyInitialized("首位管理员已经初始化。")
            return self._insert_user(
                connection,
                user_id=user_id,
                username=username,
                username_key=username_key,
                role="admin",
                password_hash=password_hash,
                actor_user_id=None,
            )

    def recover_admin(
        self,
        username_key: str,
        password_hash: str,
    ) -> StoredUser:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM auth_users WHERE username_key=?", (username_key,)
            ).fetchone()
            if row is None:
                raise AccountNotFound("找不到此账号，无法恢复管理员权限。")
            now = _timestamp(utc_now())
            connection.execute(
                """UPDATE auth_users
                   SET role='admin', is_active=1, password_hash=?,
                       must_change_password=1, updated_at=?
                   WHERE id=?""",
                (password_hash, now, row["id"]),
            )
            connection.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (now, row["id"]),
            )
            self._audit(
                connection,
                actor_user_id=None,
                target_user_id=row["id"],
                action="user.admin_recovery",
                summary={"role": "admin", "active": True},
            )
            recovered = connection.execute(
                "SELECT * FROM auth_users WHERE id=?", (row["id"],)
            ).fetchone()
            if recovered is None:
                raise AuthStoreUnavailable("管理员恢复后无法读取账号。")
            return _row_user(recovered)

    def create_user(
        self,
        *,
        user_id: str,
        username: str,
        username_key: str,
        role: UserRole,
        password_hash: str,
        actor_user_id: str,
        request_id: str | None = None,
    ) -> StoredUser:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_admin_actor(connection, actor_user_id)
            return self._insert_user(
                connection,
                user_id=user_id,
                username=username,
                username_key=username_key,
                role=role,
                password_hash=password_hash,
                actor_user_id=actor_user_id,
                request_id=request_id,
            )

    def get_user_for_login(self, username_key: str) -> StoredUser | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM auth_users WHERE username_key=?", (username_key,)
            ).fetchone()
        return _row_user(row) if row else None

    def get_user(self, user_id: str) -> StoredUser:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise AccountNotFound("找不到此账号。")
        return _row_user(row)

    def list_users(self) -> list[tuple[StoredUser, list[str]]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM auth_users ORDER BY username_key, id"
            ).fetchall()
            grants = connection.execute(
                "SELECT user_id, data_source_id FROM auth_user_data_sources "
                "ORDER BY user_id, data_source_id"
            ).fetchall()
        by_user: dict[str, list[str]] = {}
        for row in grants:
            by_user.setdefault(row["user_id"], []).append(row["data_source_id"])
        return [(_row_user(row), by_user.get(row["id"], [])) for row in rows]

    def update_user(
        self,
        user_id: str,
        *,
        username: str | None,
        username_key: str | None,
        role: UserRole | None,
        is_active: bool | None,
        actor_user_id: str,
        request_id: str | None = None,
    ) -> StoredUser:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_admin_actor(connection, actor_user_id)
            row = connection.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                raise AccountNotFound("找不到此账号。")
            current = _row_user(row)
            new_role = role or current.role
            new_active = current.is_active if is_active is None else is_active
            role_changed = new_role != current.role
            active_changed = new_active != current.is_active
            if current.role == "admin" and current.is_active and (
                new_role != "admin" or not new_active
            ):
                active_admins = connection.execute(
                    "SELECT COUNT(*) FROM auth_users WHERE role='admin' AND is_active=1"
                ).fetchone()[0]
                if active_admins <= 1:
                    raise LastActiveAdmin("必须至少保留一名启用的管理员。")

            new_username = current.username if username is None else username
            new_key = current.username_key if username_key is None else username_key
            duplicate = connection.execute(
                "SELECT 1 FROM auth_users WHERE username_key=? AND id<>?", (new_key, user_id)
            ).fetchone()
            if duplicate:
                raise UsernameConflict("用户名已存在。")
            now = _timestamp(utc_now())
            connection.execute(
                """UPDATE auth_users SET username=?, username_key=?, role=?, is_active=?, updated_at=?
                   WHERE id=?""",
                (new_username, new_key, new_role, int(new_active), now, user_id),
            )
            if role_changed or active_changed:
                connection.execute(
                    "UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                    (now, user_id),
                )
            self._audit(
                connection,
                actor_user_id=actor_user_id,
                target_user_id=user_id,
                action="user.update",
                summary={
                    "username_changed": new_key != current.username_key,
                    "role_changed": role_changed,
                    "active_changed": active_changed,
                },
                request_id=request_id,
            )
            updated = connection.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
            if updated is None:
                raise AuthStoreUnavailable("账号更新后无法读取。")
            return _row_user(updated)

    def reset_password(
        self,
        user_id: str,
        password_hash: str,
        *,
        actor_user_id: str,
        request_id: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_admin_actor(connection, actor_user_id)
            row = connection.execute("SELECT 1 FROM auth_users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                raise AccountNotFound("找不到此账号。")
            now = _timestamp(utc_now())
            connection.execute(
                """UPDATE auth_users SET password_hash=?, must_change_password=1, updated_at=?
                   WHERE id=?""",
                (password_hash, now, user_id),
            )
            connection.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (now, user_id),
            )
            self._audit(
                connection,
                actor_user_id=actor_user_id,
                target_user_id=user_id,
                action="user.password_reset",
                summary={},
                request_id=request_id,
            )

    def grant_data_source(
        self,
        user_id: str,
        source_id: str,
        *,
        actor_user_id: str,
        request_id: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_admin_actor(connection, actor_user_id)
            target = connection.execute(
                "SELECT role FROM auth_users WHERE id=?", (user_id,)
            ).fetchone()
            if target is None:
                raise AccountNotFound("找不到此账号。")
            if target["role"] != "member":
                raise DataSourceGrantError("数据源只需分配给普通用户。")
            source = connection.execute(
                """SELECT 1 FROM wren_data_sources
                   WHERE id=? AND enabled=1 AND runtime_status='ready'""",
                (source_id,),
            ).fetchone()
            if source is None:
                raise DataSourceGrantError("只能分配已启用且配置就绪的数据源。")
            connection.execute(
                """INSERT OR IGNORE INTO auth_user_data_sources
                   (user_id, data_source_id, granted_by, granted_at) VALUES (?, ?, ?, ?)""",
                (user_id, source_id, actor_user_id, _timestamp(utc_now())),
            )
            self._audit(
                connection,
                actor_user_id=actor_user_id,
                target_user_id=user_id,
                action="user.data_source_grant",
                summary={"data_source_id": source_id},
                request_id=request_id,
            )

    def revoke_data_source(
        self,
        user_id: str,
        source_id: str,
        *,
        actor_user_id: str,
        request_id: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_admin_actor(connection, actor_user_id)
            if connection.execute("SELECT 1 FROM auth_users WHERE id=?", (user_id,)).fetchone() is None:
                raise AccountNotFound("找不到此账号。")
            deleted = connection.execute(
                "DELETE FROM auth_user_data_sources WHERE user_id=? AND data_source_id=?",
                (user_id, source_id),
            ).rowcount
            if deleted:
                self._audit(
                    connection,
                    actor_user_id=actor_user_id,
                    target_user_id=user_id,
                    action="user.data_source_revoke",
                    summary={"data_source_id": source_id},
                    request_id=request_id,
                )

    def granted_data_source_ids(self, user_id: str) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT data_source_id FROM auth_user_data_sources WHERE user_id=?",
                (user_id,),
            ).fetchall()
        return {row["data_source_id"] for row in rows}

    def can_access_data_source(self, user_id: str, source_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM auth_users AS u
                   JOIN wren_data_sources AS d ON d.id=? AND d.enabled=1
                   WHERE u.id=? AND u.is_active=1 AND (
                     u.role='admin' OR (
                       u.role='member' AND EXISTS (
                         SELECT 1 FROM auth_user_data_sources AS g
                         WHERE g.user_id=u.id AND g.data_source_id=d.id
                       )
                     )
                   )""",
                (source_id, user_id),
            ).fetchone()
        return row is not None

    def is_login_blocked(self, username_key: str, now: datetime | None = None) -> bool:
        now = now or utc_now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT blocked_until FROM auth_login_throttles WHERE username_key=?",
                (username_key,),
            ).fetchone()
        return bool(row and row["blocked_until"] and _parse_timestamp(row["blocked_until"]) > now)

    def record_login_failure(self, username_key: str, now: datetime | None = None) -> None:
        now = now or utc_now()
        now_text = _timestamp(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT failed_count, window_started_at FROM auth_login_throttles WHERE username_key=?",
                (username_key,),
            ).fetchone()
            if row is None or now - _parse_timestamp(row["window_started_at"]) >= LOGIN_WINDOW:
                count = 1
                window_started = now
            else:
                count = int(row["failed_count"]) + 1
                window_started = _parse_timestamp(row["window_started_at"])
            blocked_until = None
            if count >= MAX_LOGIN_FAILURES:
                excess = count - MAX_LOGIN_FAILURES
                seconds = min(
                    int(MAX_LOGIN_BACKOFF.total_seconds()), 30 * (2 ** min(excess, 5))
                )
                blocked_until = _timestamp(now + timedelta(seconds=seconds))
            connection.execute(
                """INSERT INTO auth_login_throttles
                   (username_key, failed_count, window_started_at, blocked_until, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(username_key) DO UPDATE SET
                     failed_count=excluded.failed_count,
                     window_started_at=excluded.window_started_at,
                     blocked_until=excluded.blocked_until,
                     updated_at=excluded.updated_at""",
                (username_key, count, _timestamp(window_started), blocked_until, now_text),
            )
            stale_before = _timestamp(now - timedelta(days=1))
            connection.execute(
                "DELETE FROM auth_login_throttles WHERE updated_at < ?", (stale_before,)
            )
            connection.execute(
                """DELETE FROM auth_login_throttles
                   WHERE username_key NOT IN (
                     SELECT username_key FROM auth_login_throttles
                     ORDER BY updated_at DESC LIMIT 10000
                   )"""
            )

    def clear_login_failures(self, username_key: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM auth_login_throttles WHERE username_key=?", (username_key,))

    def create_session(
        self,
        *,
        token_hash: str,
        user_id: str,
        now: datetime | None = None,
    ) -> None:
        now = now or utc_now()
        now_text = _timestamp(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """DELETE FROM auth_sessions
                   WHERE absolute_expires_at < ? OR
                     (revoked_at IS NOT NULL AND revoked_at < ?)""",
                (now_text, _timestamp(now - timedelta(days=30))),
            )
            user = connection.execute(
                "SELECT is_active FROM auth_users WHERE id=?", (user_id,)
            ).fetchone()
            if user is None or not user["is_active"]:
                raise AccountNotFound("找不到此账号。")
            connection.execute(
                """INSERT INTO auth_sessions
                   (token_hash, user_id, created_at, last_seen_at, idle_expires_at,
                    absolute_expires_at, revoked_at)
                   VALUES (?, ?, ?, ?, ?, ?, NULL)""",
                (
                    token_hash,
                    user_id,
                    now_text,
                    now_text,
                    _timestamp(now + IDLE_TIMEOUT),
                    _timestamp(now + ABSOLUTE_TIMEOUT),
                ),
            )
            connection.execute(
                "UPDATE auth_users SET last_login_at=?, updated_at=? WHERE id=?",
                (now_text, now_text, user_id),
            )

    def authenticate_session(self, token_hash: str, now: datetime | None = None) -> StoredUser | None:
        now = now or utc_now()
        now_text = _timestamp(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            session = connection.execute(
                "SELECT * FROM auth_sessions WHERE token_hash=?", (token_hash,)
            ).fetchone()
            if session is None or session["revoked_at"] is not None:
                connection.commit()
                return None
            expired = (
                _parse_timestamp(session["idle_expires_at"]) <= now
                or _parse_timestamp(session["absolute_expires_at"]) <= now
            )
            user_row = connection.execute(
                "SELECT * FROM auth_users WHERE id=?", (session["user_id"],)
            ).fetchone()
            if expired or user_row is None or not user_row["is_active"]:
                connection.execute(
                    "UPDATE auth_sessions SET revoked_at=? WHERE token_hash=?",
                    (now_text, token_hash),
                )
                connection.commit()
                return None
            absolute_expiry = _parse_timestamp(session["absolute_expires_at"])
            idle_expiry = min(now + IDLE_TIMEOUT, absolute_expiry)
            connection.execute(
                """UPDATE auth_sessions SET last_seen_at=?, idle_expires_at=?
                   WHERE token_hash=? AND revoked_at IS NULL""",
                (now_text, _timestamp(idle_expiry), token_hash),
            )
            return _row_user(user_row)

    def revoke_session(self, token_hash: str, now: datetime | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (_timestamp(now or utc_now()), token_hash),
            )

    def change_password(
        self,
        *,
        user_id: str,
        old_token_hash: str,
        new_token_hash: str,
        password_hash: str,
        now: datetime | None = None,
    ) -> StoredUser:
        now = now or utc_now()
        now_text = _timestamp(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            session = connection.execute(
                """SELECT * FROM auth_sessions WHERE token_hash=? AND user_id=?
                   AND revoked_at IS NULL""",
                (old_token_hash, user_id),
            ).fetchone()
            user = connection.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
            if session is None or user is None or not user["is_active"]:
                raise InvalidSession("登录状态已失效，请重新登录。")
            if (
                _parse_timestamp(session["idle_expires_at"]) <= now
                or _parse_timestamp(session["absolute_expires_at"]) <= now
            ):
                connection.execute(
                    "UPDATE auth_sessions SET revoked_at=? WHERE token_hash=?",
                    (now_text, old_token_hash),
                )
                raise InvalidSession("登录状态已失效，请重新登录。")
            absolute_expiry = _parse_timestamp(session["absolute_expires_at"])
            connection.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE token_hash=?",
                (now_text, old_token_hash),
            )
            connection.execute(
                """UPDATE auth_users SET password_hash=?, must_change_password=0, updated_at=?
                   WHERE id=?""",
                (password_hash, now_text, user_id),
            )
            connection.execute(
                """INSERT INTO auth_sessions
                   (token_hash, user_id, created_at, last_seen_at, idle_expires_at,
                    absolute_expires_at, revoked_at)
                   VALUES (?, ?, ?, ?, ?, ?, NULL)""",
                (
                    new_token_hash,
                    user_id,
                    session["created_at"],
                    now_text,
                    _timestamp(min(now + IDLE_TIMEOUT, absolute_expiry)),
                    session["absolute_expires_at"],
                ),
            )
            self._audit(
                connection,
                actor_user_id=user_id,
                target_user_id=user_id,
                action="user.password_change",
                summary={},
            )
            updated = connection.execute("SELECT * FROM auth_users WHERE id=?", (user_id,)).fetchone()
            if updated is None:
                raise AuthStoreUnavailable("密码更新后无法读取账号。")
            return _row_user(updated)
