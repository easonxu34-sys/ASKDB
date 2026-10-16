from __future__ import annotations

from pathlib import Path

import anyio
from fastapi.testclient import TestClient

from api.routes.admin_effective_permissions import _file_chunks
from api.app import create_app
from api.dependencies import get_auth_application
from domain.auth import InvalidSession, Principal
from application.effective_permissions import EffectivePermissionExport
from integrations.effective_permission_store import EffectivePermissionStoreError


class _Auth:
    def authenticate(self, token: str | None) -> Principal:
        if token not in {"admin", "member"}:
            raise InvalidSession("请先登录。")
        return Principal(user_id=token, username=token, role=token, must_change_password=False)


class _EffectivePermissions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.export: EffectivePermissionExport | None = None

    def query(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("user_id") == "missing":
            raise EffectivePermissionStoreError("TARGET_NOT_FOUND", "目标不存在。", 404)
        if kwargs.get("cursor") == "invalid":
            raise EffectivePermissionStoreError("CURSOR_INVALID", "分页游标无效。", 400)
        return {
            "read_at": "2026-10-10T00:00:00+00:00",
            "target": {
                "type": "user",
                "id": "user-1",
                "username": "alice",
                "role": "member",
                "is_active": True,
            },
            "counts": {
                "total": 1,
                "usable": 1,
                "granted_blocked": 0,
                "ungranted": 0,
                "multiple_sources": 0,
                "matching": 1,
            },
            "items": [
                {
                    "user": {"id": "user-1", "username": "alice", "role": "member", "is_active": True},
                    "data_source": {
                        "id": "source-1",
                        "display_name": "Finance",
                        "exists": True,
                        "enabled": True,
                        "runtime_status": "ready",
                    },
                    "grant_sources": [
                        {"type": "direct", "key": "direct", "granted_by": "admin-1", "granted_at": "2026-10-01T00:00:00+00:00"}
                    ],
                    "has_grant": True,
                    "access_allowed": True,
                    "runtime_ready": True,
                    "usable_now": True,
                    "block_reasons": [],
                    "removal_impacts": [
                        {
                            "source_key": "direct",
                            "remaining_sources": [],
                            "access_allowed_after": False,
                            "would_lose_access": True,
                        }
                    ],
                }
            ],
            "next_cursor": None,
        }

    def prepare_export(self, **_kwargs):
        if self.export is None:
            raise AssertionError("test export artifact not configured")
        return self.export


def _client() -> tuple[TestClient, _EffectivePermissions]:
    app = create_app()
    app.dependency_overrides[get_auth_application] = lambda: _Auth()
    service = _EffectivePermissions()
    app.state.effective_permissions = service
    return TestClient(app), service


def test_effective_permission_query_requires_admin_and_disables_cache() -> None:
    client, _ = _client()
    path = "/v1/admin/effective-permissions?view=user&user_id=user-1"

    unauthenticated = client.get(path)
    member = client.get(path, headers={"Authorization": "Bearer member"})
    admin = client.get(path, headers={"Authorization": "Bearer admin"})

    assert unauthenticated.status_code == 401
    assert member.status_code == 403
    assert admin.status_code == 200
    assert admin.json()["items"][0]["grant_sources"][0]["type"] == "direct"
    assert all("no-store" in response.headers["cache-control"] for response in (unauthenticated, member, admin))


def test_effective_permission_query_validates_target_and_paging_contract() -> None:
    client, service = _client()
    headers = {"Authorization": "Bearer admin"}

    missing_target = client.get("/v1/admin/effective-permissions?view=user", headers=headers)
    both_targets = client.get(
        "/v1/admin/effective-permissions?view=user&user_id=user-1&data_source_id=source-1",
        headers=headers,
    )
    unknown_view = client.get("/v1/admin/effective-permissions?view=all", headers=headers)
    oversized_page = client.get(
        "/v1/admin/effective-permissions?view=user&user_id=user-1&limit=101",
        headers=headers,
    )
    valid = client.get(
        "/v1/admin/effective-permissions?view=source&data_source_id=source-1&scope=all&status=multiple_sources&limit=1",
        headers=headers,
    )

    assert [response.status_code for response in (missing_target, both_targets, unknown_view, oversized_page)] == [422, 422, 422, 422]
    assert valid.status_code == 200
    assert service.calls[-1] == {
        "view": "source",
        "user_id": None,
        "data_source_id": "source-1",
        "scope": "all",
        "status": "multiple_sources",
        "limit": 1,
        "cursor": None,
    }
    assert all("no-store" in response.headers["cache-control"] for response in (missing_target, both_targets, unknown_view, oversized_page, valid))


def test_effective_permission_query_maps_missing_target_and_invalid_cursor() -> None:
    client, _ = _client()
    headers = {"Authorization": "Bearer admin"}

    missing = client.get(
        "/v1/admin/effective-permissions?view=user&user_id=missing", headers=headers
    )
    invalid_cursor = client.get(
        "/v1/admin/effective-permissions?view=user&user_id=user-1&cursor=invalid",
        headers=headers,
    )

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "TARGET_NOT_FOUND"
    assert invalid_cursor.status_code == 400
    assert invalid_cursor.json()["detail"]["code"] == "CURSOR_INVALID"
    assert "no-store" in missing.headers["cache-control"]
    assert "no-store" in invalid_cursor.headers["cache-control"]


def test_effective_permission_export_requires_admin_and_cleans_file(tmp_path: Path) -> None:
    client, service = _client()
    path = tmp_path / "permission.csv"
    content = b"\xef\xbb\xbfuser_id\r\nuser-1\r\n"
    path.write_bytes(content)
    service.export = EffectivePermissionExport(
        path=path,
        filename="askdb-effective-permissions-20261010T080000Z.csv",
        read_at="2026-10-10T08:00:00+00:00",
        row_count=1,
        sha256="a" * 64,
        byte_count=len(content),
    )
    path_query = "/v1/admin/effective-permissions/export?view=user&user_id=user-1"

    unauthenticated = client.get(path_query)
    member = client.get(path_query, headers={"Authorization": "Bearer member"})
    admin = client.get(path_query, headers={"Authorization": "Bearer admin"})

    assert unauthenticated.status_code == 401
    assert member.status_code == 403
    assert admin.status_code == 200
    assert admin.content == content
    assert admin.headers["x-export-row-count"] == "1"
    assert admin.headers["x-export-generated-at"] == "2026-10-10T08:00:00+00:00"
    assert admin.headers["x-export-sha256"] == "a" * 64
    assert "no-store" in admin.headers["cache-control"]
    assert not path.exists()


def test_effective_permission_export_rejects_invalid_global_target() -> None:
    client, _ = _client()

    response = client.get(
        "/v1/admin/effective-permissions/export?view=all&user_id=user-1",
        headers={"Authorization": "Bearer admin"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "EFFECTIVE_PERMISSION_QUERY_INVALID"
    assert "no-store" in response.headers["cache-control"]


def test_export_stream_cancellation_closes_and_removes_file(tmp_path: Path) -> None:
    path = tmp_path / "cancelled.csv"
    path.write_bytes(b"x" * 100_000)

    async def consume_first_chunk() -> None:
        stream = _file_chunks(path)
        chunk = await anext(stream)
        assert chunk == b"x" * 64 * 1024
        await stream.aclose()

    anyio.run(consume_first_chunk)

    assert not path.exists()
