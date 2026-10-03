from __future__ import annotations

import sqlite3

import pytest
from cryptography.fernet import Fernet

from askdb_agent.wren_settings import ChatDataSourceMismatch, WrenSettingsStore


@pytest.fixture
def store(tmp_path):
    return WrenSettingsStore(
        tmp_path / "settings.sqlite3",
        Fernet.generate_key().decode("ascii"),
    )


def test_creates_two_independent_sources(store):
    first = store.create_data_source("分析库", "mysql", {"host": "db-a"}, {"password": "secret-a"})
    second = store.create_data_source("运营库", "mysql", {"host": "db-b"}, {"password": "secret-b"})

    assert first.id != second.id
    assert first.draft_revision_id != second.draft_revision_id
    assert store.get_data_source(first.id).display_name == "分析库"
    assert store.get_data_source(second.id).display_name == "运营库"


def test_secret_is_encrypted_at_rest(store, tmp_path):
    source = store.create_data_source(
        "分析库", "mysql", {"host": "db.internal"}, {"password": "secret-marker"}
    )

    with sqlite3.connect(tmp_path / "settings.sqlite3") as connection:
        stored = connection.execute("SELECT ciphertext FROM wren_secrets").fetchone()[0]

    assert b"secret-marker" not in stored
    assert store.get_secret(source.id, source.draft_revision_id, "password") == "secret-marker"


def test_default_must_be_enabled_and_active(store):
    source = store.create_data_source("分析库", "mysql", {}, {})

    with pytest.raises(ValueError, match="活动 runtime"):
        store.set_default(source.id)

    store.activate_revision(source.id, source.draft_revision_id)
    store.set_default(source.id)
    store.set_enabled(source.id, False)

    with pytest.raises(ValueError, match="启用状态"):
        store.set_default(source.id)


def test_thread_source_binding_is_immutable(store):
    source_a = store.create_data_source("A", "mysql", {}, {})
    source_b = store.create_data_source("B", "mysql", {}, {})

    assert store.bind_thread_source("thread-1", source_a.id) == source_a.id
    assert store.bind_thread_source("thread-1", source_a.id) == source_a.id
    with pytest.raises(ChatDataSourceMismatch):
        store.bind_thread_source("thread-1", source_b.id)


def test_source_detail_exposes_safe_revision_history(store):
    source = store.create_data_source("分析库", "mysql", {"host": "db.internal"}, {})
    first_revision = source.draft_revision_id
    second = store.save_draft(source.id, {"host": "db.internal", "database": "analytics"})

    detail = store.source_detail(source.id)

    assert [revision["id"] for revision in detail["revisions"]] == [second.id, first_revision]
    assert detail["revisions"][0]["status"] == "draft"
    assert "project_dir" not in detail["revisions"][0]


def test_source_detail_includes_active_config_separately_from_draft(store):
    source = store.create_data_source("分析库", "mysql", {"host": "active-db"}, {"password": "active-secret"})
    store.activate_revision(source.id, source.draft_revision_id)
    store.save_draft(source.id, {"host": "draft-db"})

    detail = store.source_detail(source.id)

    assert detail["active_config"]["host"] == "active-db"
    assert detail["config"]["host"] == "draft-db"
    assert "active-secret" not in str(detail["active_config"])


def test_binding_unknown_source_is_rejected(store):
    with pytest.raises(LookupError):
        store.bind_thread_source("thread-1", "missing")
