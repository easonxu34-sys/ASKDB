from __future__ import annotations

import asyncio
import sqlite3
import weakref

import pytest
from cryptography.fernet import Fernet

from application.model_settings import ModelSettingsApplication
from model_settings import (
    ModelConfiguration,
    ModelConfigurationError,
    ModelProfileNotFound,
    ModelSettingsUnavailable,
    ModelSettingsStore,
)


def test_legacy_single_row_migrates_ciphertext_to_default_profile(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    encryption_key = Fernet.generate_key().decode("ascii")
    cipher = Fernet(encryption_key.encode("ascii"))
    encrypted_key = cipher.encrypt(b"test-only-api-key")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """CREATE TABLE model_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key_ciphertext BLOB,
                updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO model_settings VALUES (1, ?, ?, ?, ?, ?)",
            (
                "deepseek",
                "deepseek-v4-flash",
                "https://api.deepseek.com",
                encrypted_key,
                "2026-10-01T12:00:00+00:00",
            ),
        )

    store = ModelSettingsStore(database_path, encryption_key)
    default_id, profiles = store.list_profiles()

    assert len(profiles) == 1
    assert default_id == profiles[0].id
    assert profiles[0].api_key == "test-only-api-key"
    with sqlite3.connect(database_path) as connection:
        migrated_ciphertext = connection.execute(
            "SELECT api_key_ciphertext FROM model_profiles WHERE id = ?",
            (default_id,),
        ).fetchone()[0]
    assert migrated_ciphertext == encrypted_key
    assert "api_key" not in profiles[0].public()
    assert "test-only-api-key" not in repr(profiles[0].public())


def test_failed_default_deletion_keeps_default_and_profile(tmp_path) -> None:
    encryption_key = Fernet.generate_key().decode("ascii")
    store = ModelSettingsStore(tmp_path / "settings.sqlite3", encryption_key)
    profile = store.create(
        ModelConfiguration(
            provider="custom",
            model="test-model",
            base_url="https://models.example.test/v1",
            api_key="test-only-api-key",
        ),
        "Default",
        make_default=True,
    )

    with pytest.raises(ModelConfigurationError):
        store.delete(profile.id, "missing-profile")

    default_id, profiles = store.list_profiles()
    assert default_id == profile.id
    assert [item.id for item in profiles] == [profile.id]


def test_successful_default_deletion_reassigns_default_in_catalog(tmp_path) -> None:
    encryption_key = Fernet.generate_key().decode("ascii")
    store = ModelSettingsStore(tmp_path / "settings.sqlite3", encryption_key)
    first = store.create(
        ModelConfiguration("custom", "first", "https://models.example.test/v1", "key-one"),
        "First",
        make_default=True,
    )
    second = store.create(
        ModelConfiguration("custom", "second", "https://models.example.test/v1", "key-two"),
        "Second",
    )

    default_id = store.delete(first.id, second.id)

    assert default_id == second.id
    assert store.get_default().id == second.id
    with pytest.raises(ModelProfileNotFound):
        store.get_profile(first.id)


def test_failed_default_delete_rolls_back_default_reassignment(tmp_path) -> None:
    encryption_key = Fernet.generate_key().decode("ascii")
    database_path = tmp_path / "settings.sqlite3"
    store = ModelSettingsStore(database_path, encryption_key)
    first = store.create(
        ModelConfiguration("custom", "first", "https://models.example.test/v1", "key-one"),
        "First",
        make_default=True,
    )
    second = store.create(
        ModelConfiguration("custom", "second", "https://models.example.test/v1", "key-two"),
        "Second",
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """CREATE TRIGGER reject_profile_delete
               BEFORE DELETE ON model_profiles
               BEGIN SELECT RAISE(ABORT, 'synthetic delete failure'); END"""
        )

    with pytest.raises(ModelSettingsUnavailable):
        store.delete(first.id, second.id)

    default_id, profiles = store.list_profiles()
    assert default_id == first.id
    assert {profile.id for profile in profiles} == {first.id, second.id}


@pytest.mark.parametrize("replacement_key_kind", ["missing", "malformed", "mismatched"])
def test_saved_settings_fail_closed_without_a_valid_fernet_key(
    tmp_path, monkeypatch, replacement_key_kind
) -> None:
    monkeypatch.setenv("ASKDB_SETTINGS_ENCRYPTION_KEY", "")
    database_path = tmp_path / "settings.sqlite3"
    encryption_key = Fernet.generate_key().decode("ascii")
    original_store = ModelSettingsStore(database_path, encryption_key)
    original_store.create(
        ModelConfiguration(
            "custom", "configured-model", "https://models.example.test/v1", "test-only-api-key"
        ),
        "Configured model",
        make_default=True,
    )

    replacement_key = {
        "missing": None,
        "malformed": "not-a-fernet-key",
        "mismatched": Fernet.generate_key().decode("ascii"),
    }[replacement_key_kind]
    unavailable_store = ModelSettingsStore(database_path, replacement_key)
    with pytest.raises(ModelSettingsUnavailable) as error:
        unavailable_store.list_profiles()

    assert "test-only-api-key" not in str(error.value)
    assert "test-only-api-key" not in repr(error.value.__cause__)


def test_clearing_legacy_default_credential_evicts_cached_runtime(
    tmp_path, monkeypatch
) -> None:
    class Runtime:
        pass

    async def scenario() -> None:
        encryption_key = Fernet.generate_key().decode("ascii")
        store = ModelSettingsStore(tmp_path / "settings.sqlite3", encryption_key)
        application = ModelSettingsApplication(store=store)
        monkeypatch.setattr(
            application,
            "_build_candidate_runtime",
            lambda configuration: (object(), Runtime()),
        )
        profile = await application.create(
            "Default",
            "custom",
            "test-model",
            "https://models.example.test/v1",
            "test-only-api-key",
        )
        runtime = await application.current_runtime(profile["id"])
        runtime_ref = weakref.ref(runtime)

        await application.clear_default_credential()
        del runtime

        assert runtime_ref() is None

    monkeypatch.setattr(
        ModelSettingsApplication,
        "_probe",
        lambda _self, _configuration: asyncio.sleep(0),
    )
    asyncio.run(scenario())


def test_profile_update_replaces_catalog_runtime_without_changing_captured_reference(
    tmp_path, monkeypatch
) -> None:
    class Runtime:
        pass

    async def scenario() -> None:
        encryption_key = Fernet.generate_key().decode("ascii")
        store = ModelSettingsStore(tmp_path / "settings.sqlite3", encryption_key)
        application = ModelSettingsApplication(store=store)
        monkeypatch.setattr(
            application,
            "_build_candidate_runtime",
            lambda configuration: (object(), Runtime()),
        )
        profile = await application.create(
            "Default",
            "custom",
            "test-model",
            "https://models.example.test/v1",
            "test-only-api-key",
        )
        captured_runtime = await application.current_runtime(profile["id"])

        await application.update(
            profile["id"],
            "Default",
            "custom",
            "updated-model",
            "https://models.example.test/v1",
            "replacement-test-only-key",
        )
        next_request_runtime = await application.current_runtime(profile["id"])

        assert next_request_runtime is not captured_runtime
        assert captured_runtime is not None

    monkeypatch.setattr(
        ModelSettingsApplication,
        "_probe",
        lambda _self, _configuration: asyncio.sleep(0),
    )
    asyncio.run(scenario())


def test_deleted_profile_fails_new_requests_but_keeps_captured_runtime_reference(
    tmp_path, monkeypatch
) -> None:
    class Runtime:
        pass

    async def scenario() -> None:
        encryption_key = Fernet.generate_key().decode("ascii")
        store = ModelSettingsStore(tmp_path / "settings.sqlite3", encryption_key)
        application = ModelSettingsApplication(store=store)
        monkeypatch.setattr(
            application,
            "_build_candidate_runtime",
            lambda configuration: (object(), Runtime()),
        )
        default_profile = await application.create(
            "Default",
            "custom",
            "default-model",
            "https://models.example.test/v1",
            "test-only-default-key",
        )
        replacement_profile = await application.create(
            "Replacement",
            "custom",
            "replacement-model",
            "https://models.example.test/v1",
            "test-only-replacement-key",
        )
        captured_runtime = await application.current_runtime(default_profile["id"])

        await application.delete(default_profile["id"], replacement_profile["id"])

        with pytest.raises(ModelProfileNotFound):
            await application.current_runtime(default_profile["id"])
        assert captured_runtime is not None
        assert (await application.current_runtime(replacement_profile["id"])) is not None

    monkeypatch.setattr(
        ModelSettingsApplication,
        "_probe",
        lambda _self, _configuration: asyncio.sleep(0),
    )
    asyncio.run(scenario())
