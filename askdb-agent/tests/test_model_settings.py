from __future__ import annotations

import asyncio
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


def test_failed_default_deletion_keeps_default_and_profile(postgres_database) -> None:
    encryption_key = Fernet.generate_key().decode("ascii")
    store = ModelSettingsStore(postgres_database, encryption_key)
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


def test_successful_default_deletion_reassigns_default_in_catalog(postgres_database) -> None:
    encryption_key = Fernet.generate_key().decode("ascii")
    store = ModelSettingsStore(postgres_database, encryption_key)
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


def test_failed_default_delete_rolls_back_default_reassignment(postgres_database) -> None:
    encryption_key = Fernet.generate_key().decode("ascii")
    store = ModelSettingsStore(postgres_database, encryption_key)
    first = store.create(
        ModelConfiguration("custom", "first", "https://models.example.test/v1", "key-one"),
        "First",
        make_default=True,
    )
    second = store.create(
        ModelConfiguration("custom", "second", "https://models.example.test/v1", "key-two"),
        "Second",
    )
    with postgres_database.connect() as connection:
        connection.execute(
            """CREATE FUNCTION reject_profile_delete() RETURNS trigger
               LANGUAGE plpgsql AS $$
               BEGIN RAISE EXCEPTION 'synthetic delete failure'; END;
               $$"""
        )
        connection.execute(
            """CREATE TRIGGER reject_profile_delete BEFORE DELETE ON model_profiles
               FOR EACH ROW EXECUTE FUNCTION reject_profile_delete()"""
        )

    with pytest.raises(ModelSettingsUnavailable):
        store.delete(first.id, second.id)

    default_id, profiles = store.list_profiles()
    assert default_id == first.id
    assert {profile.id for profile in profiles} == {first.id, second.id}


@pytest.mark.parametrize("replacement_key_kind", ["missing", "malformed", "mismatched"])
def test_saved_settings_fail_closed_without_a_valid_fernet_key(
    postgres_database, monkeypatch, replacement_key_kind
) -> None:
    monkeypatch.setenv("ASKDB_SETTINGS_ENCRYPTION_KEY", "")
    encryption_key = Fernet.generate_key().decode("ascii")
    original_store = ModelSettingsStore(postgres_database, encryption_key)
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
    unavailable_store = ModelSettingsStore(postgres_database, replacement_key)
    with pytest.raises(ModelSettingsUnavailable) as error:
        unavailable_store.list_profiles()

    assert "test-only-api-key" not in str(error.value)
    assert "test-only-api-key" not in repr(error.value.__cause__)


def test_clearing_legacy_default_credential_evicts_cached_runtime(
    postgres_database, monkeypatch
) -> None:
    class Runtime:
        pass

    async def scenario() -> None:
        encryption_key = Fernet.generate_key().decode("ascii")
        store = ModelSettingsStore(postgres_database, encryption_key)
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
    postgres_database, monkeypatch
) -> None:
    class Runtime:
        pass

    async def scenario() -> None:
        encryption_key = Fernet.generate_key().decode("ascii")
        store = ModelSettingsStore(postgres_database, encryption_key)
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
    postgres_database, monkeypatch
) -> None:
    class Runtime:
        pass

    async def scenario() -> None:
        encryption_key = Fernet.generate_key().decode("ascii")
        store = ModelSettingsStore(postgres_database, encryption_key)
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
