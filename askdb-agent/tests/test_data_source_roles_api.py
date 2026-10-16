from __future__ import annotations

from fastapi.testclient import TestClient

from api.app import create_app
from api.dependencies import get_auth_application
from domain.auth import InvalidSession, Principal
from integrations.data_source_role_store import RoleStoreError


class _Auth:
    def authenticate(self, token: str | None) -> Principal:
        if token not in {"admin", "member"}:
            raise InvalidSession("请先登录。")
        return Principal(user_id=token, username=token, role=token, must_change_password=False)


class _Roles:
    def list_roles(self):
        return []

    def create_role(self, **_kwargs):
        raise RoleStoreError("DATA_SOURCE_ROLE_CONFLICT", "角色名称已存在。", 409)

    def get_role(self, role_id: str):
        raise RoleStoreError("DATA_SOURCE_ROLE_NOT_FOUND", "找不到数据源角色。", 404)


def _client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_auth_application] = lambda: _Auth()
    app.state.data_source_roles = _Roles()
    return TestClient(app)


def test_role_management_requires_admin_and_disables_cache() -> None:
    client = _client()
    unauthenticated = client.get("/v1/admin/data-source-roles")
    member = client.get("/v1/admin/data-source-roles", headers={"Authorization": "Bearer member"})
    admin = client.get("/v1/admin/data-source-roles", headers={"Authorization": "Bearer admin"})

    assert unauthenticated.status_code == 401
    assert member.status_code == 403
    assert admin.status_code == 200
    assert admin.json() == {"roles": []}
    assert all("no-store" in response.headers["cache-control"] for response in (unauthenticated, member, admin))


def test_role_management_rejects_invalid_body_and_reports_conflicts() -> None:
    client = _client()
    headers = {"Authorization": "Bearer admin"}
    invalid = client.post("/v1/admin/data-source-roles", headers=headers, json={"name": ""})
    conflict = client.post("/v1/admin/data-source-roles", headers=headers, json={"name": "Sales"})

    assert invalid.status_code == 422
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "DATA_SOURCE_ROLE_CONFLICT"
    assert "no-store" in invalid.headers["cache-control"]
    assert "no-store" in conflict.headers["cache-control"]


def test_missing_role_and_invalid_page_size_are_safe() -> None:
    client = _client()
    headers = {"Authorization": "Bearer admin"}
    missing = client.get("/v1/admin/data-source-roles/missing", headers=headers)
    invalid_page = client.get("/v1/admin/data-source-roles/previews/token?page_size=101", headers=headers)

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "DATA_SOURCE_ROLE_NOT_FOUND"
    assert invalid_page.status_code == 422
    assert "no-store" in missing.headers["cache-control"]
