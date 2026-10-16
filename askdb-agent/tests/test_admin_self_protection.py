from __future__ import annotations

from contextlib import contextmanager

import pytest

from api.dependencies import auth_http_exception
from auth_store import AuthStore
from domain.auth import SelfAdminChangeForbidden


class _Result:
    def __init__(self, row: dict[str, object]):
        self.row = row

    def fetchone(self) -> dict[str, object]:
        return self.row


class _Connection:
    def __init__(self):
        self.statements: list[str] = []

    def acquire_write_lock(self) -> None:
        pass

    def execute(self, statement: str, _parameters: object = None) -> _Result:
        self.statements.append(statement)
        if statement == "SELECT role, is_active FROM auth_users WHERE id=%s":
            return _Result({"role": "admin", "is_active": 1})
        if statement == "SELECT * FROM auth_users WHERE id=%s":
            return _Result({
                "id": "current-admin",
                "username": "admin",
                "username_key": "admin",
                "role": "admin",
                "password_hash": "unused",
                "is_active": 1,
                "must_change_password": 0,
                "created_at": "2026-10-10T00:00:00+00:00",
                "updated_at": "2026-10-10T00:00:00+00:00",
                "last_login_at": None,
            })
        pytest.fail(f"unexpected database statement: {statement}")


@pytest.mark.parametrize(
    ("role", "is_active"),
    [("member", True), ("admin", False)],
)
def test_admin_cannot_remove_own_access(monkeypatch: pytest.MonkeyPatch, role: str, is_active: bool) -> None:
    connection = _Connection()

    @contextmanager
    def connect():
        yield connection

    store = AuthStore(database=object())
    monkeypatch.setattr(store, "_connect", connect)

    with pytest.raises(SelfAdminChangeForbidden):
        store.update_user(
            "current-admin",
            username=None,
            username_key=None,
            role=role,
            is_active=is_active,
            actor_user_id="current-admin",
        )

    assert all(not statement.startswith("UPDATE auth_users") for statement in connection.statements)


def test_self_admin_change_has_explicit_conflict_response() -> None:
    response = auth_http_exception(SelfAdminChangeForbidden("不能取消当前账号的管理员权限。"))

    assert response.status_code == 409
    assert response.detail["code"] == "SELF_ADMIN_CHANGE_FORBIDDEN"


def test_other_admin_demotion_saves_role_bindings_in_one_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    class Connection(_Connection):
        def execute(self, statement: str, parameters: object = None) -> _Result:
            self.statements.append(statement)
            if statement == "SELECT role, is_active FROM auth_users WHERE id=%s":
                return _Result({"role": "admin", "is_active": 1})
            if statement == "SELECT * FROM auth_users WHERE id=%s":
                row = super().execute(statement, parameters).fetchone().copy()
                row["id"] = "other-admin"
                row["role"] = "member" if any(s.startswith("UPDATE auth_users SET") for s in self.statements) else "admin"
                return _Result(row)
            if statement == "SELECT 1 FROM auth_data_source_roles WHERE id=%s":
                return _Result({"exists": 1})
            if statement.startswith("SELECT COUNT(*) FROM auth_users"):
                return _Result((2,))
            if statement.startswith("SELECT 1 FROM auth_users WHERE username_key"):
                return _Result(None)
            return _Result(None)

    connection = Connection()

    @contextmanager
    def connect():
        yield connection

    store = AuthStore(database=object())
    monkeypatch.setattr(store, "_connect", connect)
    monkeypatch.setattr(store, "_audit", lambda *args, **kwargs: None)

    updated = store.update_user(
        "other-admin", username=None, username_key=None, role="member",
        is_active=True, role_ids=["sales"], actor_user_id="current-admin",
    )

    assert updated.role == "member"
    assert any(s.startswith("UPDATE auth_users SET") for s in connection.statements)
    assert any(s.startswith("INSERT INTO auth_user_role_bindings") for s in connection.statements)
