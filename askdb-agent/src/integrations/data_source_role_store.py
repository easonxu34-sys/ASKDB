from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator

import psycopg

from auth_store import AuthStore
from integrations.database import PostgresConnection, PostgresDatabase
from integrations.source_access import source_ids_for_user


class RoleStoreError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        self.code = code
        self.status_code = status_code
        super().__init__(message)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _missing() -> RoleStoreError:
    return RoleStoreError("DATA_SOURCE_ROLE_NOT_FOUND", "找不到数据源角色。", 404)


def _role_row(connection: PostgresConnection, role_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM auth_data_source_roles WHERE id=%s", (role_id,)
    ).fetchone()
    if row is None:
        raise _missing()
    source_ids = [
        item["data_source_id"]
        for item in connection.execute(
            "SELECT data_source_id FROM auth_role_data_sources WHERE role_id=%s ORDER BY data_source_id",
            (role_id,),
        ).fetchall()
    ]
    user_ids = [
        item["user_id"]
        for item in connection.execute(
            "SELECT user_id FROM auth_user_role_bindings WHERE role_id=%s ORDER BY user_id",
            (role_id,),
        ).fetchall()
    ]
    return {**dict(row), "data_source_ids": source_ids, "user_ids": user_ids}


def _require_sources(connection: PostgresConnection, source_ids: list[str]) -> None:
    for source_id in source_ids:
        row = connection.execute(
            "SELECT 1 FROM wren_data_sources WHERE id=%s AND enabled=1 AND runtime_status='ready'",
            (source_id,),
        ).fetchone()
        if row is None:
            raise RoleStoreError(
                "DATA_SOURCE_NOT_ASSIGNABLE", "只能分配已启用且配置就绪的数据源。", 422
            )


def _require_roles(connection: PostgresConnection, role_ids: list[str]) -> None:
    for role_id in role_ids:
        if connection.execute(
            "SELECT 1 FROM auth_data_source_roles WHERE id=%s", (role_id,)
        ).fetchone() is None:
            raise RoleStoreError("DATA_SOURCE_ROLE_NOT_FOUND", "找不到数据源角色。", 404)


def _grant_snapshot(connection: PostgresConnection, user_id: str) -> dict[str, Any]:
    direct = [
        row["data_source_id"]
        for row in connection.execute(
            "SELECT data_source_id FROM auth_user_data_sources WHERE user_id=%s ORDER BY data_source_id",
            (user_id,),
        ).fetchall()
    ]
    roles = [
        {"role_id": row["role_id"], "data_source_id": row["data_source_id"]}
        for row in connection.execute(
            """SELECT b.role_id, g.data_source_id FROM auth_user_role_bindings AS b
               LEFT JOIN auth_role_data_sources AS g ON g.role_id=b.role_id
               WHERE b.user_id=%s ORDER BY b.role_id, g.data_source_id""",
            (user_id,),
        ).fetchall()
    ]
    return {"direct": direct, "roles": roles}


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class DataSourceRoleStore:
    def __init__(self, database: PostgresDatabase):
        self.database = database

    @contextmanager
    def _connect(self) -> Iterator[PostgresConnection]:
        try:
            connection = self.database.connect()
        except (psycopg.Error, RuntimeError) as exc:
            raise RoleStoreError("DATA_SOURCE_ROLE_STORE_UNAVAILABLE", "数据源角色服务当前不可用。", 503) from exc
        try:
            with connection:
                yield connection
        except psycopg.Error as exc:
            raise RoleStoreError("DATA_SOURCE_ROLE_STORE_UNAVAILABLE", "数据源角色服务当前不可用。", 503) from exc

    def list_roles(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            ids = [row["id"] for row in connection.execute(
                "SELECT id FROM auth_data_source_roles ORDER BY name_key, id"
            ).fetchall()]
            return [_role_row(connection, role_id) for role_id in ids]

    def get_role(self, role_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            return _role_row(connection, role_id)

    def create_role(
        self, *, name: str, name_key: str, description: str, source_ids: list[str],
        actor_id: str, request_id: str | None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.acquire_write_lock()
            AuthStore._require_admin_actor(connection, actor_id)
            if connection.execute(
                "SELECT 1 FROM auth_data_source_roles WHERE name_key=%s", (name_key,)
            ).fetchone():
                raise RoleStoreError("DATA_SOURCE_ROLE_CONFLICT", "角色名称已存在。", 409)
            _require_sources(connection, source_ids)
            role_id = uuid.uuid4().hex
            now = _now()
            connection.execute(
                """INSERT INTO auth_data_source_roles
                   (id, name, name_key, description, version, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, 1, %s, %s)""",
                (role_id, name, name_key, description, now, now),
            )
            for source_id in source_ids:
                connection.execute(
                    """INSERT INTO auth_role_data_sources
                       (role_id, data_source_id, granted_by, granted_at)
                       VALUES (%s, %s, %s, %s)""",
                    (role_id, source_id, actor_id, now),
                )
            AuthStore._audit(connection, actor_user_id=actor_id, target_user_id=None,
                             action="data_source_role.create",
                             summary={"role_id": role_id, "data_source_ids": source_ids},
                             request_id=request_id)
            return _role_row(connection, role_id)

    def get_bindings(self, user_id: str) -> list[str]:
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM auth_users WHERE id=%s", (user_id,)).fetchone() is None:
                raise RoleStoreError("ACCOUNT_NOT_FOUND", "找不到此账号。", 404)
            return [row["role_id"] for row in connection.execute(
                "SELECT role_id FROM auth_user_role_bindings WHERE user_id=%s ORDER BY role_id", (user_id,)
            ).fetchall()]

    def impact_role(self, role_id: str, source_ids: list[str] | None) -> dict[str, Any]:
        with self._connect() as connection:
            connection.acquire_write_lock()
            return self._impact_role(connection, role_id, source_ids)

    def _impact_role(self, connection: PostgresConnection, role_id: str, source_ids: list[str] | None) -> dict[str, Any]:
        role = _role_row(connection, role_id)
        old = set(role["data_source_ids"])
        retained = set(source_ids or [])
        affected = []
        snapshots = []
        for user_id in role["user_ids"]:
            account = connection.execute("SELECT username FROM auth_users WHERE id=%s", (user_id,)).fetchone()
            before = source_ids_for_user(connection, user_id)
            snapshot = _grant_snapshot(connection, user_id)
            other = set(snapshot["direct"]) | {
                item["data_source_id"] for item in snapshot["roles"]
                if item["role_id"] != role_id and item["data_source_id"] is not None
            }
            lost = sorted(before - (other | retained))
            affected.append({"user_id": user_id, "username": account["username"], "lost_data_source_ids": lost})
            snapshots.append({"user_id": user_id, "grants": snapshot})
        digest = _digest({"role": role, "affected": snapshots, "proposal": source_ids})
        return {"total": len(affected), "users": affected, "digest": digest}

    def change_role(self, *, role_id: str, name: str | None, name_key: str | None,
                    description: str | None, source_ids: list[str] | None, delete: bool,
                    expected_digest: str | None, actor_id: str, request_id: str | None) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.acquire_write_lock()
            AuthStore._require_admin_actor(connection, actor_id)
            role = _role_row(connection, role_id)
            if name_key is not None and connection.execute(
                "SELECT 1 FROM auth_data_source_roles WHERE name_key=%s AND id<>%s", (name_key, role_id)
            ).fetchone():
                raise RoleStoreError("DATA_SOURCE_ROLE_CONFLICT", "角色名称已存在。", 409)
            if source_ids is not None and not delete:
                _require_sources(connection, [item for item in source_ids if item not in role["data_source_ids"]])
            removing = delete or (source_ids is not None and bool(set(role["data_source_ids"]) - set(source_ids)))
            if removing:
                impact = self._impact_role(connection, role_id, None if delete else source_ids)
                if expected_digest != impact["digest"]:
                    raise RoleStoreError("DATA_SOURCE_ROLE_PREVIEW_STALE", "权限影响已变化，请重新预览。", 409)
            elif expected_digest is not None:
                impact = self._impact_role(connection, role_id, source_ids or role["data_source_ids"])
                if expected_digest != impact["digest"]:
                    raise RoleStoreError("DATA_SOURCE_ROLE_PREVIEW_STALE", "权限影响已变化，请重新预览。", 409)
            if delete:
                connection.execute("DELETE FROM auth_user_role_bindings WHERE role_id=%s", (role_id,))
                connection.execute("DELETE FROM auth_role_data_sources WHERE role_id=%s", (role_id,))
                connection.execute("DELETE FROM auth_data_source_roles WHERE id=%s", (role_id,))
                action = "data_source_role.delete"
                result = None
            else:
                next_ids = role["data_source_ids"] if source_ids is None else source_ids
                now = _now()
                for source_id in set(role["data_source_ids"]) - set(next_ids):
                    connection.execute(
                        "DELETE FROM auth_role_data_sources WHERE role_id=%s AND data_source_id=%s",
                        (role_id, source_id),
                    )
                for source_id in set(next_ids) - set(role["data_source_ids"]):
                    connection.execute(
                        """INSERT INTO auth_role_data_sources
                           (role_id, data_source_id, granted_by, granted_at) VALUES (%s, %s, %s, %s)""",
                        (role_id, source_id, actor_id, now),
                    )
                connection.execute(
                    """UPDATE auth_data_source_roles SET name=%s, name_key=%s, description=%s,
                       version=version+1, updated_at=%s WHERE id=%s""",
                    (name or role["name"], name_key or role["name_key"],
                     description if description is not None else role["description"], now, role_id),
                )
                action = "data_source_role.update"
                result = _role_row(connection, role_id)
            audited_source_ids = [] if delete else (source_ids if source_ids is not None else role["data_source_ids"])
            AuthStore._audit(connection, actor_user_id=actor_id, target_user_id=None,
                             action=action,
                             summary={
                                 "role_id": role_id,
                                 "deleted": delete,
                                 "name_changed": name is not None and name != role["name"],
                                 "description_changed": description is not None and description != role["description"],
                                 "added_data_source_ids": sorted(set(audited_source_ids) - set(role["data_source_ids"])),
                                 "removed_data_source_ids": sorted(set(role["data_source_ids"]) - set(audited_source_ids)),
                                 "affected_user_count": len(role["user_ids"]),
                             },
                             request_id=request_id)
            return result

    def impact_bindings(self, user_id: str, role_ids: list[str]) -> dict[str, Any]:
        with self._connect() as connection:
            connection.acquire_write_lock()
            return self._impact_bindings(connection, user_id, role_ids)

    def _impact_bindings(self, connection: PostgresConnection, user_id: str, role_ids: list[str]) -> dict[str, Any]:
        account = connection.execute("SELECT username, role FROM auth_users WHERE id=%s", (user_id,)).fetchone()
        if account is None:
            raise RoleStoreError("ACCOUNT_NOT_FOUND", "找不到此账号。", 404)
        if account["role"] != "member":
            raise RoleStoreError("DATA_SOURCE_ROLE_MEMBER_REQUIRED", "只能给普通账号绑定数据源角色。", 422)
        _require_roles(connection, role_ids)
        before = source_ids_for_user(connection, user_id)
        snapshot = _grant_snapshot(connection, user_id)
        after = set(snapshot["direct"])
        for role_id in role_ids:
            after.update(row["data_source_id"] for row in connection.execute(
                "SELECT data_source_id FROM auth_role_data_sources WHERE role_id=%s", (role_id,)
            ).fetchall())
        return {"total": 1, "users": [{"user_id": user_id, "username": account["username"],
                 "lost_data_source_ids": sorted(before - after)}],
                "digest": _digest({"user_id": user_id, "grants": snapshot, "proposal": role_ids})}

    def replace_bindings(self, *, user_id: str, role_ids: list[str], expected_digest: str | None,
                         actor_id: str, request_id: str | None) -> list[str]:
        with self._connect() as connection:
            connection.acquire_write_lock()
            AuthStore._require_admin_actor(connection, actor_id)
            impact = self._impact_bindings(connection, user_id, role_ids)
            old_ids = {row["role_id"] for row in connection.execute(
                "SELECT role_id FROM auth_user_role_bindings WHERE user_id=%s", (user_id,)
            ).fetchall()}
            if (old_ids - set(role_ids) or expected_digest is not None) and expected_digest != impact["digest"]:
                raise RoleStoreError("DATA_SOURCE_ROLE_PREVIEW_STALE", "权限影响已变化，请重新预览。", 409)
            now = _now()
            connection.execute("DELETE FROM auth_user_role_bindings WHERE user_id=%s", (user_id,))
            for role_id in role_ids:
                connection.execute(
                    """INSERT INTO auth_user_role_bindings(user_id, role_id, bound_by, bound_at)
                       VALUES (%s, %s, %s, %s)""",
                    (user_id, role_id, actor_id, now),
                )
            AuthStore._audit(connection, actor_user_id=actor_id, target_user_id=user_id,
                             action="user.data_source_roles_replace",
                             summary={"role_ids": role_ids, "lost_data_source_ids": impact["users"][0]["lost_data_source_ids"]},
                             request_id=request_id)
            return role_ids
