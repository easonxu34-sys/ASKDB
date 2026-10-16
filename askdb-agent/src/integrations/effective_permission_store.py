from __future__ import annotations

import base64
import binascii
import json
import re
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator, Literal

import psycopg

from auth_store import AuthStore
from domain.auth import AdminPrivilegeRequired
from integrations.database import PostgresConnection, PostgresDatabase


View = Literal["user", "source", "all"]
Scope = Literal["granted", "all"]
Status = Literal["all", "usable", "granted_blocked", "ungranted", "multiple_sources"]
MAX_GRANT_SOURCES = 256
MAX_EXPORT_ROWS = 50_000
MAX_EXPORT_GRANT_SOURCE_ROWS = 100_000
MAX_EXPORT_BYTES = 25 * 1024 * 1024
_CURSOR_FIELDS = {"view", "target_id", "scope", "status", "limit", "sort_key", "sort_id"}


class EffectivePermissionStoreError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def _cursor_payload(
    cursor: str,
    *,
    view: str,
    target_id: str,
    scope: str,
    status: str,
    limit: int,
) -> tuple[str, str]:
    if not cursor or len(cursor) > 2048 or re.fullmatch(r"[A-Za-z0-9_-]+", cursor) is None:
        raise EffectivePermissionStoreError("CURSOR_INVALID", "分页游标无效。", 400)
    try:
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        payload = json.loads(raw)
    except (ValueError, binascii.Error, json.JSONDecodeError):
        raise EffectivePermissionStoreError("CURSOR_INVALID", "分页游标无效。", 400) from None
    if not isinstance(payload, dict) or set(payload) != _CURSOR_FIELDS:
        raise EffectivePermissionStoreError("CURSOR_INVALID", "分页游标无效。", 400)
    expected = {
        "view": view,
        "target_id": target_id,
        "scope": scope,
        "status": status,
        "limit": limit,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise EffectivePermissionStoreError("CURSOR_INVALID", "分页游标与当前筛选条件不匹配。", 400)
    if not isinstance(payload.get("sort_key"), str) or not isinstance(payload.get("sort_id"), str):
        raise EffectivePermissionStoreError("CURSOR_INVALID", "分页游标无效。", 400)
    return payload["sort_key"], payload["sort_id"]


def _encode_cursor(
    *,
    view: str,
    target_id: str,
    scope: str,
    status: str,
    limit: int,
    sort_key: str,
    sort_id: str,
) -> str:
    payload = {
        "view": view,
        "target_id": target_id,
        "scope": scope,
        "status": status,
        "limit": limit,
        "sort_key": sort_key,
        "sort_id": sort_id,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _status_predicate(status: str) -> str:
    return {
        "all": "TRUE",
        "usable": "usable_now",
        "granted_blocked": "has_grant AND NOT usable_now",
        "ungranted": "NOT has_grant",
        "multiple_sources": "path_count > 1",
    }[status]


class EffectivePermissionStore:
    """Read one permission matrix from a repeatable, read-only PostgreSQL snapshot."""

    def __init__(self, database: PostgresDatabase):
        self.database = database

    @contextmanager
    def _connect(self, *, read_only: bool) -> Iterator[PostgresConnection]:
        try:
            connection = self.database.connect()
        except (psycopg.Error, RuntimeError) as exc:
            raise EffectivePermissionStoreError(
                "EFFECTIVE_PERMISSION_STORE_UNAVAILABLE", "权限视图服务当前不可用。", 503
            ) from exc
        try:
            with connection:
                if read_only:
                    connection.execute(
                        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                    )
                yield connection
        except EffectivePermissionStoreError:
            raise
        except psycopg.Error as exc:
            raise EffectivePermissionStoreError(
                "EFFECTIVE_PERMISSION_STORE_UNAVAILABLE", "权限视图服务当前不可用。", 503
            ) from exc

    @staticmethod
    def _candidate_sql(view: View, target_id: str | None, scope: Scope) -> tuple[str, tuple[object, ...]]:
        if view == "user":
            assert target_id is not None
            if scope == "granted":
                pairs = """SELECT %s::text AS user_id, grants.source_id
                           FROM auth_users AS grant_target
                           CROSS JOIN LATERAL (
                             SELECT data_source_id AS source_id
                             FROM auth_user_data_sources WHERE user_id=%s
                             UNION
                             SELECT grant_row.data_source_id AS source_id
                             FROM auth_user_role_bindings AS binding
                             JOIN auth_role_data_sources AS grant_row ON grant_row.role_id=binding.role_id
                             WHERE binding.user_id=%s
                           ) AS grants
                           WHERE grant_target.id=%s AND grant_target.role='member'
                           UNION
                           SELECT %s::text AS user_id, source.id AS source_id
                           FROM auth_users AS target CROSS JOIN wren_data_sources AS source
                           WHERE target.id=%s AND target.role='admin'"""
                pair_params: tuple[object, ...] = (
                    target_id, target_id, target_id, target_id, target_id, target_id
                )
            else:
                pairs = """SELECT %s::text AS user_id, source.id AS source_id
                           FROM wren_data_sources AS source
                           UNION
                           SELECT %s::text AS user_id, grants.source_id
                           FROM (
                             SELECT data_source_id AS source_id
                             FROM auth_user_data_sources WHERE user_id=%s
                             UNION
                             SELECT grant_row.data_source_id AS source_id
                             FROM auth_user_role_bindings AS binding
                             JOIN auth_role_data_sources AS grant_row ON grant_row.role_id=binding.role_id
                             WHERE binding.user_id=%s
                           ) AS grants
                           WHERE NOT EXISTS (
                             SELECT 1 FROM wren_data_sources AS existing_source
                             WHERE existing_source.id=grants.source_id
                           )
                             AND EXISTS (
                               SELECT 1 FROM auth_users AS target
                               WHERE target.id=%s AND target.role='member'
                             )"""
                pair_params = (target_id, target_id, target_id, target_id, target_id)
        elif view == "source":
            assert target_id is not None
            if scope == "all":
                pairs = "SELECT account.id AS user_id, %s::text AS source_id FROM auth_users AS account"
                pair_params = (target_id,)
            else:
                pairs = """SELECT account.id AS user_id, %s::text AS source_id
                           FROM auth_users AS account
                           WHERE account.role='admin'
                              OR EXISTS (
                                SELECT 1 FROM auth_user_data_sources AS direct
                                WHERE direct.user_id=account.id AND direct.data_source_id=%s
                              )
                              OR EXISTS (
                                SELECT 1 FROM auth_user_role_bindings AS binding
                                JOIN auth_role_data_sources AS grant_row ON grant_row.role_id=binding.role_id
                                WHERE binding.user_id=account.id AND grant_row.data_source_id=%s
                              )"""
                pair_params = (target_id, target_id, target_id)
        elif view == "all":
            if scope == "granted":
                pairs = """SELECT account.id AS user_id, source.id AS source_id
                           FROM auth_users AS account CROSS JOIN wren_data_sources AS source
                           WHERE account.role='admin'
                              OR EXISTS (
                                SELECT 1 FROM auth_user_data_sources AS direct
                                WHERE direct.user_id=account.id AND direct.data_source_id=source.id
                              )
                              OR EXISTS (
                                SELECT 1 FROM auth_user_role_bindings AS binding
                                JOIN auth_role_data_sources AS grant_row ON grant_row.role_id=binding.role_id
                                WHERE binding.user_id=account.id AND grant_row.data_source_id=source.id
                              )
                           UNION
                           SELECT orphan.user_id, orphan.source_id
                           FROM (
                             SELECT user_id, data_source_id AS source_id FROM auth_user_data_sources
                             UNION
                             SELECT binding.user_id, grant_row.data_source_id
                             FROM auth_user_role_bindings AS binding
                             JOIN auth_role_data_sources AS grant_row ON grant_row.role_id=binding.role_id
                           ) AS orphan
                           JOIN auth_users AS account ON account.id=orphan.user_id AND account.role='member'
                           WHERE NOT EXISTS (
                             SELECT 1 FROM wren_data_sources AS source
                             WHERE source.id=orphan.source_id
                           )"""
                pair_params = ()
            else:
                pairs = """SELECT account.id AS user_id, source.id AS source_id
                           FROM auth_users AS account CROSS JOIN wren_data_sources AS source
                           UNION
                           SELECT orphan.user_id, orphan.source_id
                           FROM (
                             SELECT user_id, data_source_id AS source_id FROM auth_user_data_sources
                             UNION
                             SELECT binding.user_id, grant_row.data_source_id
                             FROM auth_user_role_bindings AS binding
                             JOIN auth_role_data_sources AS grant_row ON grant_row.role_id=binding.role_id
                           ) AS orphan
                           JOIN auth_users AS account ON account.id=orphan.user_id AND account.role='member'
                           WHERE NOT EXISTS (
                             SELECT 1 FROM wren_data_sources AS source
                             WHERE source.id=orphan.source_id
                           )"""
                pair_params = ()
        else:
            raise ValueError("view is invalid")

        sort_name = "COALESCE(source.display_name, pair.source_id)" if view == "user" else "account.username"
        candidate = f"""WITH pairs AS ({pairs})
            SELECT account.id AS user_id,
                   account.username,
                   account.role AS user_role,
                   (account.is_active=1) AS user_is_active,
                   pair.source_id,
                   COALESCE(source.display_name, pair.source_id) AS source_name,
                   (source.id IS NOT NULL) AS source_exists,
                   COALESCE(source.enabled=1, FALSE) AS source_enabled,
                   source.runtime_status,
                   CASE WHEN account.role='admin' THEN 1
                        ELSE (CASE WHEN direct.user_id IS NOT NULL THEN 1 ELSE 0 END)
                             + COALESCE(role_paths.path_count, 0)
                   END AS path_count,
                   (account.role='admin' OR direct.user_id IS NOT NULL
                     OR COALESCE(role_paths.path_count, 0) > 0) AS has_grant,
                   ((account.is_active=1) AND source.id IS NOT NULL AND source.enabled=1
                     AND (account.role='admin' OR direct.user_id IS NOT NULL
                          OR COALESCE(role_paths.path_count, 0) > 0)) AS access_allowed,
                   ((account.is_active=1) AND source.id IS NOT NULL AND source.enabled=1
                     AND source.runtime_status='ready'
                     AND (account.role='admin' OR direct.user_id IS NOT NULL
                          OR COALESCE(role_paths.path_count, 0) > 0)) AS usable_now,
                   LOWER({sort_name}) AS sort_key,
                   {"pair.source_id" if view == "user" else "account.id"} AS sort_id
            FROM pairs AS pair
            JOIN auth_users AS account ON account.id=pair.user_id
            LEFT JOIN wren_data_sources AS source ON source.id=pair.source_id
            LEFT JOIN auth_user_data_sources AS direct
              ON direct.user_id=account.id AND direct.data_source_id=pair.source_id
             AND account.role='member'
            LEFT JOIN LATERAL (
              SELECT COUNT(*) AS path_count
              FROM auth_user_role_bindings AS binding
              JOIN auth_role_data_sources AS grant_row ON grant_row.role_id=binding.role_id
              WHERE binding.user_id=account.id AND grant_row.data_source_id=pair.source_id
                AND account.role='member'
            ) AS role_paths ON TRUE"""
        return candidate, pair_params

    @staticmethod
    def _count_query(candidate: str, status: str) -> str:
        predicate = _status_predicate(status)
        return f"""WITH permissions AS ({candidate})
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE usable_now) AS usable,
                   COUNT(*) FILTER (WHERE has_grant AND NOT usable_now) AS granted_blocked,
                   COUNT(*) FILTER (WHERE NOT has_grant) AS ungranted,
                   COUNT(*) FILTER (WHERE path_count > 1) AS multiple_sources,
                   COUNT(*) FILTER (WHERE {predicate}) AS matching
            FROM permissions"""

    @staticmethod
    def _read_target(
        connection: PostgresConnection, view: View, target_id: str | None
    ) -> dict[str, object]:
        if view == "all":
            return {"type": "all", "id": "all"}
        if view == "user":
            row = connection.execute(
                "SELECT id, username, role, is_active FROM auth_users WHERE id=%s", (target_id,)
            ).fetchone()
            if row is None:
                raise EffectivePermissionStoreError("TARGET_NOT_FOUND", "账号不存在。", 404)
            return {
                "type": "user",
                "id": row["id"],
                "username": row["username"],
                "role": row["role"],
                "is_active": bool(row["is_active"]),
            }
        row = connection.execute(
            "SELECT id, display_name, enabled, runtime_status FROM wren_data_sources WHERE id=%s",
            (target_id,),
        ).fetchone()
        if row is None:
            raise EffectivePermissionStoreError("TARGET_NOT_FOUND", "数据源不存在。", 404)
        return {
            "type": "source",
            "id": row["id"],
            "display_name": row["display_name"],
            "enabled": bool(row["enabled"]),
            "runtime_status": row["runtime_status"],
        }

    @staticmethod
    def _read_at(connection: PostgresConnection) -> str:
        row = connection.execute("SELECT transaction_timestamp() AS read_at").fetchone()
        value = row["read_at"]
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()

    @staticmethod
    def _read_grant_sources(
        connection: PostgresConnection,
        rows: list[dict[str, Any]],
        *,
        max_rows: int | None = None,
    ) -> dict[tuple[str, str], list[dict[str, object]]]:
        if not rows:
            return {}
        user_ids = sorted({row["user_id"] for row in rows})
        source_ids = sorted({row["source_id"] for row in rows})
        result: dict[tuple[str, str], list[dict[str, object]]] = {}
        query = """SELECT direct.user_id, direct.data_source_id AS source_id,
                          'direct'::text AS type,
                          NULL::text AS role_id, NULL::text AS role_name,
                          NULL::text AS role_name_key,
                          direct.granted_by, direct.granted_at,
                          NULL::text AS bound_by, NULL::text AS bound_at
                   FROM auth_user_data_sources AS direct
                   JOIN auth_users AS account ON account.id=direct.user_id AND account.role='member'
                   WHERE direct.user_id=ANY(%s) AND direct.data_source_id=ANY(%s)
                   UNION ALL
                   SELECT binding.user_id, grant_row.data_source_id AS source_id,
                          'role'::text AS type,
                          role.id AS role_id, role.name AS role_name,
                          role.name_key AS role_name_key,
                          grant_row.granted_by, grant_row.granted_at,
                          binding.bound_by, binding.bound_at
                   FROM auth_user_role_bindings AS binding
                   JOIN auth_users AS account ON account.id=binding.user_id AND account.role='member'
                   JOIN auth_data_source_roles AS role ON role.id=binding.role_id
                   JOIN auth_role_data_sources AS grant_row ON grant_row.role_id=role.id
                   WHERE binding.user_id=ANY(%s) AND grant_row.data_source_id=ANY(%s)
                   ORDER BY user_id, source_id, type, role_name_key, role_id"""
        parameters: tuple[object, ...] = (user_ids, source_ids, user_ids, source_ids)
        if max_rows is not None:
            query += " LIMIT %s"
            parameters += (max_rows + 1,)
        grant_rows = connection.execute(query, parameters).fetchall()
        if max_rows is not None and len(grant_rows) > max_rows:
            raise EffectivePermissionStoreError(
                "GRANT_SOURCE_LIMIT", "导出范围包含过多授权来源，请缩小筛选范围。", 413
            )
        for grant in grant_rows:
            key = (grant["user_id"], grant["source_id"])
            details: dict[str, object] = {
                "type": grant["type"],
                "granted_by": grant["granted_by"],
                "granted_at": grant["granted_at"],
            }
            if grant["type"] == "role":
                details.update(
                    {
                        "role_id": grant["role_id"],
                        "role_name": grant["role_name"],
                        "role_name_key": grant["role_name_key"],
                        "bound_by": grant["bound_by"],
                        "bound_at": grant["bound_at"],
                    }
                )
            result.setdefault(key, []).append(details)
        return result

    @staticmethod
    def _item(row: dict[str, Any], grant_sources: list[dict[str, object]]) -> dict[str, object]:
        return {
            "user": {
                "id": row["user_id"],
                "username": row["username"],
                "role": row["user_role"],
                "is_active": bool(row["user_is_active"]),
            },
            "data_source": {
                "id": row["source_id"],
                "display_name": row["source_name"],
                "exists": bool(row["source_exists"]),
                "enabled": bool(row["source_enabled"]),
                "runtime_status": row["runtime_status"],
            },
            "grant_sources": grant_sources,
            "_sort_key": row["sort_key"],
            "_sort_id": row["sort_id"],
            "_path_count": row["path_count"],
        }

    def query(
        self,
        *,
        view: Literal["user", "source"],
        user_id: str | None,
        data_source_id: str | None,
        scope: Scope,
        status: Status,
        limit: int,
        cursor: str | None,
    ) -> dict[str, object]:
        target_id = user_id if view == "user" else data_source_id
        if target_id is None:
            raise ValueError("target_id is required")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        cursor_key = _cursor_payload(
            cursor,
            view=view,
            target_id=target_id,
            scope=scope,
            status=status,
            limit=limit,
        ) if cursor is not None else None
        with self._connect(read_only=True) as connection:
            read_at = self._read_at(connection)
            target = self._read_target(connection, view, target_id)
            candidate, params = self._candidate_sql(view, target_id, scope)
            count_row = connection.execute(
                self._count_query(candidate, status), params
            ).fetchone()
            predicate = _status_predicate(status)
            cursor_clause = ""
            page_params: tuple[object, ...] = params
            if cursor_key is not None:
                cursor_clause = "AND (sort_key, sort_id) > (%s, %s)"
                page_params += cursor_key
            page_params += (limit + 1,)
            rows = connection.execute(
                f"""WITH permissions AS ({candidate})
                    SELECT * FROM permissions
                    WHERE {predicate} {cursor_clause}
                    ORDER BY sort_key, sort_id
                    LIMIT %s""",
                page_params,
            ).fetchall()
            has_more = len(rows) > limit
            rows = [dict(row) for row in rows[:limit]]
            if any(int(row["path_count"]) > MAX_GRANT_SOURCES for row in rows):
                raise EffectivePermissionStoreError(
                    "GRANT_SOURCE_LIMIT", "此结果的授权来源过多，无法安全显示。", 413
                )
            grant_sources = self._read_grant_sources(connection, rows)
            items = [
                self._item(row, grant_sources.get((row["user_id"], row["source_id"]), []))
                for row in rows
            ]
            next_cursor = None
            if has_more and items:
                last = items[-1]
                next_cursor = _encode_cursor(
                    view=view,
                    target_id=target_id,
                    scope=scope,
                    status=status,
                    limit=limit,
                    sort_key=str(last["_sort_key"]),
                    sort_id=str(last["_sort_id"]),
                )

        for item in items:
            item.pop("_sort_key", None)
            item.pop("_sort_id", None)
            item.pop("_path_count", None)
        counts = {key: int(count_row[key]) for key in (
            "total", "usable", "granted_blocked", "ungranted", "multiple_sources", "matching"
        )}
        return {
            "read_at": read_at,
            "target": target,
            "counts": counts,
            "items": items,
            "next_cursor": next_cursor,
        }

    def read_export(
        self,
        *,
        view: View,
        user_id: str | None,
        data_source_id: str | None,
        scope: Scope,
        status: Status,
    ) -> dict[str, object]:
        target_id = user_id if view == "user" else data_source_id if view == "source" else None
        if (view == "user" and (not user_id or data_source_id is not None)) or (
            view == "source" and (not data_source_id or user_id is not None)
        ) or (view == "all" and (user_id is not None or data_source_id is not None)):
            raise ValueError("export target is invalid")
        with self._connect(read_only=True) as connection:
            read_at = self._read_at(connection)
            target = self._read_target(connection, view, target_id)
            candidate, params = self._candidate_sql(view, target_id, scope)
            count_row = connection.execute(
                self._count_query(candidate, status), params
            ).fetchone()
            row_count = int(count_row["matching"])
            if row_count > MAX_EXPORT_ROWS:
                raise EffectivePermissionStoreError(
                    "EXPORT_ROW_LIMIT", "导出范围超过行数上限，请缩小筛选范围。", 413
                )
            rows = [
                dict(row)
                for row in connection.execute(
                    f"""WITH permissions AS ({candidate})
                        SELECT * FROM permissions
                        WHERE {_status_predicate(status)}
                        ORDER BY sort_key, sort_id
                        LIMIT %s""",
                    (*params, MAX_EXPORT_ROWS + 1),
                ).fetchall()
            ]
            if len(rows) != row_count:
                raise EffectivePermissionStoreError(
                    "EXPORT_SNAPSHOT_INCONSISTENT", "导出快照读取不完整，请重试。", 503
                )
            if any(int(row["path_count"]) > MAX_GRANT_SOURCES for row in rows):
                raise EffectivePermissionStoreError(
                    "GRANT_SOURCE_LIMIT", "导出范围包含来源过多的记录，请缩小筛选范围。", 413
                )
            grant_sources = self._read_grant_sources(
                connection, rows, max_rows=MAX_EXPORT_GRANT_SOURCE_ROWS
            )
            items = [
                self._item(row, grant_sources.get((row["user_id"], row["source_id"]), []))
                for row in rows
            ]
            for item in items:
                item.pop("_sort_key", None)
                item.pop("_sort_id", None)
                item.pop("_path_count", None)
            counts = {key: int(count_row[key]) for key in (
                "total", "usable", "granted_blocked", "ungranted", "multiple_sources", "matching"
            )}
        return {"read_at": read_at, "target": target, "counts": counts, "items": items}

    def audit_export(
        self,
        *,
        actor_id: str,
        request_id: str | None,
        summary: dict[str, object],
    ) -> None:
        try:
            with self._connect(read_only=False) as connection:
                connection.acquire_write_lock()
                AuthStore._require_admin_actor(connection, actor_id)
                AuthStore._audit(
                    connection,
                    actor_user_id=actor_id,
                    target_user_id=None,
                    action="effective_permissions.export",
                    summary=summary,
                    request_id=request_id,
                )
        except AdminPrivilegeRequired:
            raise EffectivePermissionStoreError(
                "ADMIN_REQUIRED", "此操作仅限管理员。", 403
            ) from None
