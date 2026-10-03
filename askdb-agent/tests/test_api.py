from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from api import create_app
from application.model_settings import ModelSettingsApplication
from model_settings import ModelConfiguration, ModelSettingsStore
from wren_settings import WrenSettingsStore
from application.runtime_manager import RuntimeDataSourceUnavailable


class FakeRuntime:
    async def astream_events(self, input, *, config, version):
        assert config["configurable"]["thread_id"] == "thread-1"
        assert version == "v2"
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": {"content": "答复"}},
        }


def test_chat_endpoint_streams_model_tokens_as_sse() -> None:
    client = TestClient(create_app(runtime=FakeRuntime()))

    response = client.post(
        "/v1/chat",
        json={
            "thread_id": "thread-1",
            "messages": [{"role": "user", "content": "订单总数？"}],
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'event: token\ndata: {"text":"答复"}' in response.text
    assert "event: done" in response.text


def test_chat_endpoint_rejects_missing_user_message() -> None:
    client = TestClient(create_app(runtime=FakeRuntime()))

    response = client.post(
        "/v1/chat",
        json={"thread_id": "thread-1", "messages": []},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("forbidden_field", ["api_key", "model", "base_url", "provider"])
def test_chat_rejects_model_overrides_without_echoing_values(forbidden_field) -> None:
    client = TestClient(create_app(runtime=FakeRuntime()))
    payload = {
        "thread_id": "thread-1",
        "messages": [{"role": "user", "content": "订单总数？"}],
        forbidden_field: "DO_NOT_ECHO_TEST_MARKER",
    }

    response = client.post(
        "/v1/chat",
        json=payload,
    )

    assert response.status_code == 422
    assert "DO_NOT_ECHO_TEST_MARKER" not in response.text


def test_chat_unknown_profile_id_is_not_routed_to_default(tmp_path) -> None:
    # Seed a real catalog entry with a test-only key in an isolated database.
    store = ModelSettingsStore(tmp_path / "settings.sqlite3", Fernet.generate_key().decode())
    store.create(
        ModelConfiguration(
            provider="custom",
            model="configured-model",
            base_url="https://models.example.test/v1",
            api_key="test-only-api-key",
        ),
        "Configured model",
        make_default=True,
    )
    app = create_app()
    app.state.model_settings = ModelSettingsApplication(store=store)

    response = TestClient(app).post(
        "/v1/chat",
        json={
            "thread_id": "thread-1",
            "model_profile_id": "profile_deleted_or_unknown",
            "messages": [{"role": "user", "content": "订单总数？"}],
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "MODEL_PROFILE_NOT_FOUND"


def test_injected_runtime_does_not_bypass_unknown_profile_validation(tmp_path) -> None:
    store = ModelSettingsStore(tmp_path / "settings.sqlite3", Fernet.generate_key().decode())
    store.create(
        ModelConfiguration(
            "custom", "configured-model", "https://models.example.test/v1", "test-only-api-key"
        ),
        "Configured model",
        make_default=True,
    )
    app = create_app(runtime=FakeRuntime())
    app.state.model_settings = ModelSettingsApplication(
        runtime=FakeRuntime(),
        store=store,
    )

    response = TestClient(app).post(
        "/v1/chat",
        json={
            "thread_id": "thread-1",
            "model_profile_id": "profile_deleted_or_unknown",
            "messages": [{"role": "user", "content": "订单总数？"}],
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "MODEL_PROFILE_NOT_FOUND"


def test_singular_model_settings_route_remains_default_profile_alias(tmp_path) -> None:
    store = ModelSettingsStore(tmp_path / "settings.sqlite3", Fernet.generate_key().decode())
    profile = store.create(
        ModelConfiguration(
            provider="custom",
            model="configured-model",
            base_url="https://models.example.test/v1",
            api_key="test-only-api-key",
        ),
        "Configured model",
        make_default=True,
    )
    app = create_app()
    app.state.model_settings = ModelSettingsApplication(store=store)

    response = TestClient(app).get("/v1/settings/model")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["id"] == profile.id
    assert response.json()["api_key_configured"] is True
    assert "test-only-api-key" not in response.text


def test_settings_api_fails_closed_without_fernet_key_and_does_not_echo_secret(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ASKDB_SETTINGS_ENCRYPTION_KEY", "")
    database_path = tmp_path / "settings.sqlite3"
    store_with_key = ModelSettingsStore(database_path, Fernet.generate_key().decode())
    store_with_key.create(
        ModelConfiguration(
            "custom", "configured-model", "https://models.example.test/v1", "test-only-api-key"
        ),
        "Configured model",
        make_default=True,
    )
    app = create_app()
    app.state.model_settings = ModelSettingsApplication(
        store=ModelSettingsStore(database_path, None)
    )

    response = TestClient(app).get("/v1/settings/models")

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["detail"]["code"] == "MODEL_SETTINGS_UNAVAILABLE"
    assert "test-only-api-key" not in response.text


def test_data_source_catalog_exposes_only_safe_fields(tmp_path):
    store = WrenSettingsStore(tmp_path / "settings.sqlite3", Fernet.generate_key().decode())
    source = store.create_data_source(
        "分析库", "mysql", {"host": "db.internal", "database": "analytics", "user": "reader"},
        {"password": "test-only-password"},
    )
    store.activate_revision(source.id, source.draft_revision_id)
    store.set_default(source.id)

    response = TestClient(create_app(wren_store=store)).get("/v1/data-sources")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["default_data_source_id"] == source.id
    assert response.json()["data_sources"][0]["id"] == source.id
    assert "db.internal" not in response.text
    assert "analytics" not in response.text
    assert "reader" not in response.text
    assert "password" not in response.text


def test_wren_settings_rejects_paths_and_unknown_fields(tmp_path):
    store = WrenSettingsStore(tmp_path / "settings.sqlite3", Fernet.generate_key().decode())
    client = TestClient(create_app(wren_store=store))

    response = client.post(
        "/v1/settings/wren/data-sources",
        json={
            "display_name": "分析库",
            "connection": {
                "host": "db.internal", "port": 3306, "database": "analytics",
                "user": "reader", "password": "secret", "project_dir": "/tmp/unsafe",
            },
        },
    )

    assert response.status_code == 422
    assert "unsafe" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_source_create_and_detail_never_return_password(tmp_path):
    store = WrenSettingsStore(tmp_path / "settings.sqlite3", Fernet.generate_key().decode())
    client = TestClient(create_app(wren_store=store))

    created = client.post(
        "/v1/settings/wren/data-sources",
        json={
            "display_name": "分析库",
            "connection": {
                "host": "db.internal", "port": 3306, "database": "analytics",
                "user": "reader", "password": "test-only-password",
            },
            "semantic": {"tables": ["orders"]},
        },
    )

    assert created.status_code == 201
    source_id = created.json()["data_source"]["id"]
    assert created.headers["cache-control"] == "no-store"
    assert created.json()["connection"]["credential_configured"] is True
    assert "test-only-password" not in created.text
    assert "password" not in created.text

    detail = client.get(f"/v1/settings/wren/data-sources/{source_id}")
    assert detail.status_code == 200
    assert detail.json()["connection"]["host"] == "db.internal"
    assert "test-only-password" not in detail.text


def test_source_can_be_reenabled_after_deactivation(tmp_path):
    store = WrenSettingsStore(tmp_path / "settings.sqlite3", Fernet.generate_key().decode())
    source = store.create_data_source("分析库", "mysql", {"host": "db"}, {})
    store.activate_revision(source.id, source.draft_revision_id)
    client = TestClient(create_app(wren_store=store))

    assert client.post(f"/v1/settings/wren/data-sources/{source.id}/deactivate").status_code == 200
    response = client.post(f"/v1/settings/wren/data-sources/{source.id}/enable")

    assert response.status_code == 200
    assert response.json()["data_source"]["enabled"] is True
    assert response.json()["data_source"]["runtime_status"] == "ready"


class FakeSourceRuntimeManager:
    def __init__(self, *, unavailable=()):
        self.unavailable = set(unavailable)
        self.acquired = []
        self.released = []

    async def acquire_runtime(self, source_id, model_profile_id=None):
        if source_id in self.unavailable:
            raise RuntimeDataSourceUnavailable("DATA_SOURCE_UNAVAILABLE")
        self.acquired.append((source_id, model_profile_id))
        return SimpleNamespace(snapshot=SimpleNamespace(graph=FakeRuntime()))

    async def release_runtime(self, lease):
        self.released.append(lease)


def _active_source(store, name):
    source = store.create_data_source(name, "mysql", {"host": "db", "database": "db", "user": "reader"}, {})
    store.activate_revision(source.id, source.draft_revision_id)
    return source


def test_chat_uses_requested_source_runtime(tmp_path):
    store = WrenSettingsStore(tmp_path / "settings.sqlite3", Fernet.generate_key().decode())
    source = _active_source(store, "分析库")
    manager = FakeSourceRuntimeManager()
    client = TestClient(create_app(runtime=FakeRuntime(), wren_store=store, runtime_manager=manager))

    response = client.post(
        "/v1/chat",
        json={
            "thread_id": "thread-1",
            "data_source_id": source.id,
            "messages": [{"role": "user", "content": "订单总数？"}],
        },
    )

    assert response.status_code == 200
    assert manager.acquired == [(source.id, None)]
    assert store.get_thread_source("thread-1") == source.id


def test_existing_thread_rejects_source_change_before_sse(tmp_path):
    store = WrenSettingsStore(tmp_path / "settings.sqlite3", Fernet.generate_key().decode())
    source_a = _active_source(store, "分析库")
    source_b = _active_source(store, "运营库")
    store.bind_thread_source("thread-1", source_a.id)
    manager = FakeSourceRuntimeManager()
    client = TestClient(create_app(runtime=FakeRuntime(), wren_store=store, runtime_manager=manager))

    response = client.post(
        "/v1/chat",
        json={
            "thread_id": "thread-1",
            "data_source_id": source_b.id,
            "messages": [{"role": "user", "content": "订单总数？"}],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CHAT_DATA_SOURCE_MISMATCH"
    assert manager.acquired == []


def test_legacy_chat_uses_only_configured_default_source(tmp_path):
    store = WrenSettingsStore(tmp_path / "settings.sqlite3", Fernet.generate_key().decode())
    source = _active_source(store, "默认库")
    store.set_default(source.id)
    manager = FakeSourceRuntimeManager()
    client = TestClient(create_app(runtime=FakeRuntime(), wren_store=store, runtime_manager=manager))

    response = client.post(
        "/v1/chat",
        json={"thread_id": "thread-1", "messages": [{"role": "user", "content": "订单总数？"}]},
    )

    assert response.status_code == 200
    assert manager.acquired == [(source.id, None)]


def test_missing_default_and_unavailable_source_do_not_fall_back(tmp_path):
    store = WrenSettingsStore(tmp_path / "settings.sqlite3", Fernet.generate_key().decode())
    source = _active_source(store, "暂不可用")
    manager = FakeSourceRuntimeManager(unavailable={source.id})
    client = TestClient(create_app(runtime=FakeRuntime(), wren_store=store, runtime_manager=manager))

    missing_default = client.post(
        "/v1/chat",
        json={"thread_id": "thread-1", "messages": [{"role": "user", "content": "订单总数？"}]},
    )
    assert missing_default.status_code == 422
    assert missing_default.json()["detail"]["code"] == "DATA_SOURCE_REQUIRED"

    store.set_default(source.id)
    unavailable = client.post(
        "/v1/chat",
        json={"thread_id": "thread-2", "messages": [{"role": "user", "content": "订单总数？"}]},
    )
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"]["code"] == "DATA_SOURCE_UNAVAILABLE"
    assert store.get_thread_source("thread-2") is None
