from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from askdb_agent.application.runtime_manager import RuntimeManager, RuntimeSnapshot


@dataclass
class Source:
    id: str
    enabled: bool = True
    active_revision_id: str | None = "rev-1"
    runtime_status: str = "ready"


@dataclass
class Revision:
    id: str
    source_id: str


@dataclass
class Model:
    id: str
    updated_at: str
    api_key: str = "key"


class SourceStore:
    def __init__(self):
        self.sources = {key: Source(key) for key in ("source-a", "source-b")}
        self.revisions = {("source-a", "rev-1"): Revision("rev-1", "source-a"),
                          ("source-b", "rev-1"): Revision("rev-1", "source-b")}

    def get_data_source(self, source_id):
        return self.sources[source_id]

    def get_revision(self, source_id, revision_id):
        return self.revisions[(source_id, revision_id)]


class ModelStore:
    def __init__(self):
        self.profiles = {"model-1": Model("model-1", "m1"), "model-2": Model("model-2", "m2")}

    def get_default(self):
        return self.profiles["model-1"]

    def get_profile(self, profile_id):
        return self.profiles[profile_id]

    def list_profiles(self, **_kwargs):
        return ("model-1", list(self.profiles.values()))


@pytest.fixture
def manager():
    def builder(source, revision, model):
        return RuntimeSnapshot(
            data_source_id=source.id,
            wren_revision_id=revision.id,
            model_profile_id=model.id,
            model_profile_revision=model.updated_at,
            toolkit=object(),
            graph=object(),
        )

    return RuntimeManager(SourceStore(), ModelStore(), builder)


def test_runtime_key_includes_source_and_both_revisions(manager):
    async def scenario():
        a = await manager.acquire_runtime("source-a", "model-1")
        b = await manager.acquire_runtime("source-b", "model-1")
        second_model = await manager.acquire_runtime("source-a", "model-2")

        assert a.key == ("source-a", "rev-1", "model-1", "m1")
        assert b.key != a.key
        assert second_model.key != a.key
        await manager.release_runtime(a)
        await manager.release_runtime(b)
        await manager.release_runtime(second_model)

    asyncio.run(scenario())


def test_source_swap_preserves_other_sources(manager):
    async def scenario():
        old_b = await manager.current_runtime("source-b", "model-1")
        manager.source_store.revisions[("source-a", "rev-2")] = Revision("rev-2", "source-a")
        candidate_a = await manager.prepare_source_revision("source-a", "rev-2")
        manager.source_store.sources["source-a"].active_revision_id = "rev-2"
        await manager.activate_source_revision("source-a", "rev-2", candidate_a)

        assert await manager.current_runtime("source-b", "model-1") is old_b
        assert (await manager.current_runtime("source-a", "model-1")).wren_revision_id == "rev-2"

    asyncio.run(scenario())


def test_model_profile_invalidation_preserves_other_profiles(manager):
    async def scenario():
        old = await manager.current_runtime("source-a", "model-2")
        await manager.invalidate_model_profile("model-1")
        assert await manager.current_runtime("source-a", "model-2") is old

    asyncio.run(scenario())


def test_failed_candidate_does_not_replace_active_snapshot():
    stores = SourceStore()
    models = ModelStore()

    def builder(source, revision, model):
        if revision.id == "rev-bad":
            raise RuntimeError("candidate failed")
        return RuntimeSnapshot(source.id, revision.id, model.id, model.updated_at, object(), object())

    manager = RuntimeManager(stores, models, builder)

    async def scenario():
        active = await manager.current_runtime("source-a", "model-1")
        stores.revisions[("source-a", "rev-bad")] = Revision("rev-bad", "source-a")
        with pytest.raises(RuntimeError):
            await manager.prepare_source_revision("source-a", "rev-bad")
        assert await manager.current_runtime("source-a", "model-1") is active

    asyncio.run(scenario())
