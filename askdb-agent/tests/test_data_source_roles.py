from __future__ import annotations

from integrations.source_access import has_source_grant, source_ids_for_user
from application.data_source_roles import DataSourceRolesApplication, normalize_ids, normalize_role_name
from integrations.data_source_role_store import RoleStoreError
import pytest


class _Cursor:
    def __init__(self, rows: list[dict[str, object]]):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, rows: list[dict[str, object]]):
        self.rows = rows
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def execute(self, statement: str, parameters: tuple[str, ...]):
        self.calls.append((statement, parameters))
        return _Cursor(self.rows)


def test_source_grant_checks_direct_and_role_sources_in_one_query() -> None:
    connection = _Connection([{"granted": 1}])

    assert has_source_grant(connection, "member-1", "source-1")

    statement, parameters = connection.calls[0]
    assert "auth_user_data_sources" in statement
    assert "auth_user_role_bindings" in statement
    assert "auth_role_data_sources" in statement
    assert parameters == ("member-1", "source-1", "member-1", "source-1")


def test_source_ids_deduplicate_direct_and_role_sources() -> None:
    connection = _Connection([{"data_source_id": "source-1"}, {"data_source_id": "source-2"}])

    assert source_ids_for_user(connection, "member-1") == {"source-1", "source-2"}

    statement, parameters = connection.calls[0]
    assert "UNION" in statement
    assert "auth_user_role_bindings" in statement
    assert parameters == ("member-1", "member-1")


def test_role_name_is_normalized_and_rejects_controls() -> None:
    assert normalize_role_name("  Ｓａｌｅｓ  ") == ("Sales", "sales")
    with pytest.raises(ValueError):
        normalize_role_name("Ops\nAdmin")


def test_role_ids_are_deduplicated_by_rejection() -> None:
    assert normalize_ids(["role-b", "role-a"]) == ["role-a", "role-b"]
    with pytest.raises(ValueError):
        normalize_ids(["role-a", "role-a"])


class _PreviewStore:
    def __init__(self):
        self.digest = "first"
        self.updated = False

    def impact_role(self, _role_id: str, _source_ids: list[str] | None):
        users = [{"user_id": str(index), "username": f"user-{index}", "lost_data_source_ids": []}
                 for index in range(120)]
        return {"total": len(users), "users": users, "digest": self.digest}

    def change_role(self, **kwargs):
        if kwargs["expected_digest"] != self.digest:
            raise RoleStoreError("DATA_SOURCE_ROLE_PREVIEW_STALE", "权限影响已变化，请重新预览。", 409)
        self.updated = True
        return {"id": kwargs["role_id"]}


def test_preview_pages_are_stable_and_bound_to_actor_and_proposal() -> None:
    store = _PreviewStore()
    app = DataSourceRolesApplication(store)
    first = app.preview_role(role_id="role-1", data_source_ids=[], delete=False,
                             actor_id="admin-1", page=1, page_size=50)
    second = app.preview_page(first["preview_token"], "admin-1", 2, 50)

    assert first["total"] == 120
    assert first["users"][0]["user_id"] == "0"
    assert second["users"][0]["user_id"] == "50"
    with pytest.raises(RoleStoreError):
        app.preview_page(first["preview_token"], "admin-2", 2, 50)
    with pytest.raises(RoleStoreError):
        app.change_role(role_id="role-1", name=None, description=None, data_source_ids=["other"],
                        delete=False, preview_token=first["preview_token"], actor_id="admin-1", request_id=None)
    assert not store.updated


def test_preview_detects_changed_grants_before_write() -> None:
    store = _PreviewStore()
    app = DataSourceRolesApplication(store)
    preview = app.preview_role(role_id="role-1", data_source_ids=[], delete=False,
                               actor_id="admin-1", page=1, page_size=50)
    store.digest = "changed"

    with pytest.raises(RoleStoreError, match="重新预览"):
        app.change_role(role_id="role-1", name=None, description=None, data_source_ids=[],
                        delete=False, preview_token=preview["preview_token"], actor_id="admin-1", request_id=None)
    assert not store.updated
