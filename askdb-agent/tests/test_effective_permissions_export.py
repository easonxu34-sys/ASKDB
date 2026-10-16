from __future__ import annotations

import csv
import hashlib
import io
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

import application.effective_permissions as effective_permissions
from application.effective_permissions import EffectivePermissionsApplication
from auth_store import AuthStore
from integrations.effective_permission_store import (
    EffectivePermissionStore,
    EffectivePermissionStoreError,
)


class _ExportStore:
    def __init__(self) -> None:
        self.audit: dict[str, object] | None = None
        self.fail_audit = False

    def read_export(self, **_kwargs):
        return {
            "read_at": "2026-10-10T08:00:00+00:00",
            "target": {"type": "user", "id": "user-1"},
            "counts": {},
            "items": [
                {
                    "user": {
                        "id": "user-1",
                        "username": "=1+1",
                        "role": "member",
                        "is_active": True,
                    },
                    "data_source": {
                        "id": "source-1",
                        "display_name": "Finance",
                        "exists": True,
                        "enabled": True,
                        "runtime_status": "ready",
                    },
                    "grant_sources": [
                        {
                            "type": "role",
                            "role_id": "role-1",
                            "role_name": "@SUM(A1:A2)",
                            "role_name_key": "@sum(a1:a2)",
                            "granted_by": "admin-1",
                            "granted_at": "2026-10-01T00:00:00+00:00",
                            "bound_by": "admin-1",
                            "bound_at": "2026-10-02T00:00:00+00:00",
                        }
                    ],
                }
            ],
        }

    def audit_export(self, *, actor_id: str, request_id: str | None, summary: dict[str, object]):
        if self.fail_audit:
            raise RuntimeError("audit failure")
        self.audit = {"actor_id": actor_id, "request_id": request_id, **summary}


def test_export_csv_has_fixed_columns_safe_cells_and_matching_digest() -> None:
    store = _ExportStore()
    application = EffectivePermissionsApplication(store)  # type: ignore[arg-type]

    artifact = application.prepare_export(
        view="user",
        user_id="user-1",
        data_source_id=None,
        scope="granted",
        status="all",
        actor_id="admin-1",
        request_id="request-1",
    )
    try:
        content = artifact.path.read_bytes()
        text = content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text, newline="")))

        assert content.startswith(b"\xef\xbb\xbf")
        assert len(rows) == 2
        assert len(rows[0]) == len(effective_permissions.CSV_HEADERS)
        assert tuple(rows[0]) == effective_permissions.CSV_HEADERS
        assert rows[1][1] == "'=1+1"
        assert json.loads(rows[1][8])[0]["role_id"] == "role-1"
        assert rows[1][14:16] == ["granted", "all"]
        assert artifact.row_count == 1
        assert artifact.byte_count == len(content)
        assert artifact.sha256 == hashlib.sha256(content).hexdigest()
        assert artifact.filename == "askdb-effective-permissions-20261010T080000Z.csv"
        assert store.audit is not None
        assert store.audit["actor_id"] == "admin-1"
        assert store.audit["request_id"] == "request-1"
        assert store.audit["sha256"] == artifact.sha256
        assert "=1+1" not in json.dumps(store.audit)
    finally:
        artifact.path.unlink(missing_ok=True)


def test_export_byte_limit_and_audit_failure_remove_temporary_file(monkeypatch) -> None:
    store = _ExportStore()
    application = EffectivePermissionsApplication(store)  # type: ignore[arg-type]
    created_paths: list[Path] = []
    original_mkstemp = effective_permissions.tempfile.mkstemp

    def capture_mkstemp(*args, **kwargs):
        descriptor, path = original_mkstemp(*args, **kwargs)
        created_paths.append(Path(path))
        return descriptor, path

    monkeypatch.setattr(effective_permissions.tempfile, "mkstemp", capture_mkstemp)
    monkeypatch.setattr(effective_permissions, "MAX_EXPORT_BYTES", 1)

    with pytest.raises(EffectivePermissionStoreError) as too_large:
        application.prepare_export(
            view="user",
            user_id="user-1",
            data_source_id=None,
            scope="granted",
            status="all",
            actor_id="admin-1",
            request_id=None,
        )

    assert too_large.value.code == "EXPORT_BYTE_LIMIT"
    assert store.audit is None
    assert len(created_paths) == 1 and not created_paths[-1].exists()

    monkeypatch.setattr(effective_permissions, "MAX_EXPORT_BYTES", 25 * 1024 * 1024)
    store.fail_audit = True
    with pytest.raises(RuntimeError, match="audit failure"):
        application.prepare_export(
            view="user",
            user_id="user-1",
            data_source_id=None,
            scope="granted",
            status="all",
            actor_id="admin-1",
            request_id=None,
        )
    assert len(created_paths) == 2 and not created_paths[-1].exists()


def test_export_byte_limit_stops_before_serializing_rows(monkeypatch) -> None:
    store = _ExportStore()
    application = EffectivePermissionsApplication(store)  # type: ignore[arg-type]
    monkeypatch.setattr(effective_permissions, "MAX_EXPORT_BYTES", 1)

    def fail_if_row_is_serialized(*_args, **_kwargs):
        pytest.fail("row serialization must stop as soon as the header exceeds the limit")

    monkeypatch.setattr(effective_permissions, "_csv_row", fail_if_row_is_serialized)

    with pytest.raises(EffectivePermissionStoreError) as too_large:
        application.prepare_export(
            view="user",
            user_id="user-1",
            data_source_id=None,
            scope="granted",
            status="all",
            actor_id="admin-1",
            request_id=None,
        )

    assert too_large.value.code == "EXPORT_BYTE_LIMIT"
    assert store.audit is None


def test_export_grant_source_limit_bounds_rows_read_from_database() -> None:
    class Cursor:
        def fetchall(self):
            return [{"user_id": "user-1", "source_id": "source-1"}] * 3

    class Connection:
        statement = ""
        parameters: tuple[object, ...] = ()

        def execute(self, statement, parameters):
            self.statement = statement
            self.parameters = tuple(parameters)
            return Cursor()

    connection = Connection()

    with pytest.raises(EffectivePermissionStoreError) as too_many:
        EffectivePermissionStore._read_grant_sources(
            connection, [{"user_id": "user-1", "source_id": "source-1"}], max_rows=2
        )

    assert too_many.value.code == "GRANT_SOURCE_LIMIT"
    assert "LIMIT %s" in connection.statement
    assert connection.parameters[-1] == 3


def test_export_audit_uses_the_shared_write_lock(monkeypatch) -> None:
    events: list[str] = []

    class Connection:
        def acquire_write_lock(self) -> None:
            events.append("write_lock")

    @contextmanager
    def connect(*, read_only: bool):
        assert read_only is False
        yield Connection()

    monkeypatch.setattr(AuthStore, "_require_admin_actor", lambda *_args: events.append("admin"))
    monkeypatch.setattr(AuthStore, "_audit", lambda *_args, **_kwargs: events.append("audit"))
    store = EffectivePermissionStore(object())  # type: ignore[arg-type]
    monkeypatch.setattr(store, "_connect", connect)

    store.audit_export(actor_id="admin-1", request_id="request-1", summary={})

    assert events == ["write_lock", "admin", "audit"]
