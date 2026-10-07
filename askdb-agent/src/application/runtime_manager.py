from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from domain.memory_recall import RecallDocument
from model_settings import ModelProfileNotFound


RuntimeIdentity = tuple[str, str]
RuntimeKey = tuple[str, str, str, str, str, str]


class RuntimeDataSourceNotFound(LookupError):
    pass


class RuntimeDataSourceUnavailable(RuntimeError):
    pass


class RuntimeModelNotConfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeSnapshot:
    data_source_id: str
    wren_revision_id: str
    model_profile_id: str
    model_profile_revision: str
    toolkit: Any
    graph: Any
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    tokenizer_id: str | None = None
    connector_type: str | None = None
    mdl_digest: str | None = None
    memory_documents: tuple[RecallDocument, ...] = ()
    memory_revision: str = "none"

    @property
    def key(self) -> RuntimeKey:
        return (
            self.data_source_id,
            self.wren_revision_id,
            self.model_profile_id,
            self.model_profile_revision,
            self.mdl_digest or "",
            self.memory_revision,
        )


@dataclass
class RuntimeLease:
    snapshot: RuntimeSnapshot
    released: bool = False

    @property
    def key(self) -> RuntimeKey:
        return self.snapshot.key


class RuntimeManager:
    """Caches immutable source/model snapshots and leases them for active requests."""

    def __init__(
        self,
        source_store: Any,
        model_store: Any,
        snapshot_builder: Callable[[Any, Any, Any], RuntimeSnapshot],
        runtime_identity_resolver: Callable[[Any, Any], RuntimeIdentity] | None = None,
    ):
        self.source_store = source_store
        self.model_store = model_store
        self.snapshot_builder = snapshot_builder
        self.runtime_identity_resolver = runtime_identity_resolver
        self._registry: dict[RuntimeKey, RuntimeSnapshot] = {}
        self._in_flight: dict[RuntimeKey, int] = {}
        self._retired: dict[RuntimeKey, RuntimeSnapshot] = {}
        self._lock = asyncio.Lock()

    def _resolve_model(self, model_profile_id: str | None) -> Any:
        if model_profile_id:
            model = self.model_store.get_profile(model_profile_id)
        else:
            model = self.model_store.get_default()
        if getattr(model, "model_kind", "chat") != "chat":
            raise RuntimeModelNotConfigured("MODEL_NOT_CONFIGURED")
        if not getattr(model, "api_key", ""):
            raise RuntimeModelNotConfigured("MODEL_NOT_CONFIGURED")
        return model

    @staticmethod
    def _resolve_source(store: Any, source_id: str) -> Any:
        try:
            source = store.get_data_source(source_id)
        except (KeyError, LookupError) as exc:
            raise RuntimeDataSourceNotFound(source_id) from exc
        if not getattr(source, "enabled", False):
            raise RuntimeDataSourceUnavailable("DATA_SOURCE_UNAVAILABLE")
        if not getattr(source, "active_revision_id", None):
            raise RuntimeDataSourceUnavailable("DATA_SOURCE_UNAVAILABLE")
        if getattr(source, "runtime_status", "ready") != "ready":
            raise RuntimeDataSourceUnavailable("DATA_SOURCE_UNAVAILABLE")
        return source

    async def _snapshot(self, source: Any, revision_id: str, model: Any) -> RuntimeSnapshot:
        revision = self.source_store.get_revision(source.id, revision_id)
        built = self.snapshot_builder(source, revision, model)
        if inspect.isawaitable(built):
            built = await built
        if not isinstance(built, RuntimeSnapshot):
            raise TypeError("snapshot_builder must return RuntimeSnapshot")
        return built

    def _runtime_key(
        self, source: Any, revision_id: str, model: Any,
        identity_override: RuntimeIdentity | None = None,
    ) -> RuntimeKey:
        revision = self.source_store.get_revision(source.id, revision_id)
        if identity_override is not None:
            identity = identity_override
        elif self.runtime_identity_resolver is not None:
            identity = self.runtime_identity_resolver(source, revision)
        else:
            identity = (str(getattr(revision, "mdl_digest", None) or ""), "none")
        if (
            not isinstance(identity, tuple)
            or len(identity) != 2
            or not all(isinstance(value, str) for value in identity)
            or not identity[1]
        ):
            raise RuntimeError("runtime identity resolver returned an invalid identity")
        return (source.id, revision_id, model.id, model.updated_at, *identity)

    async def acquire_runtime(
        self, data_source_id: str, model_profile_id: str | None = None
    ) -> RuntimeLease:
        source = self._resolve_source(self.source_store, data_source_id)
        revision_id = source.active_revision_id
        model = self._resolve_model(model_profile_id)
        key = self._runtime_key(source, revision_id, model)
        async with self._lock:
            current_source = self._resolve_source(self.source_store, data_source_id)
            current_model = self._resolve_model(model_profile_id)
            if (
                current_source.active_revision_id != revision_id
                or self._runtime_key(current_source, revision_id, current_model) != key
            ):
                raise RuntimeDataSourceUnavailable("DATA_SOURCE_UNAVAILABLE")
            snapshot = self._registry.get(key)
            if snapshot is not None:
                self._retire_stale_memory_snapshots(key)
                self._in_flight[key] = self._in_flight.get(key, 0) + 1
                return RuntimeLease(snapshot)

        candidate = await self._snapshot(source, revision_id, model)
        if candidate.key != key:
            raise RuntimeError("snapshot builder returned a mismatched cache key")
        async with self._lock:
            current_source = self._resolve_source(self.source_store, data_source_id)
            current_model = self._resolve_model(model_profile_id)
            if (
                current_source.active_revision_id != revision_id
                or self._runtime_key(current_source, revision_id, current_model) != key
            ):
                raise RuntimeDataSourceUnavailable("DATA_SOURCE_UNAVAILABLE")
            snapshot = self._registry.get(key)
            if snapshot is None:
                snapshot = self._registry.setdefault(key, candidate)
            self._retire_stale_memory_snapshots(key)
            self._in_flight[key] = self._in_flight.get(key, 0) + 1
        return RuntimeLease(snapshot)

    def _retire_stale_memory_snapshots(self, active_key: RuntimeKey) -> None:
        semantic_identity = active_key[:4]
        stale_keys = tuple(
            key
            for key in self._registry
            if key[:4] == semantic_identity and key[4:] != active_key[4:]
        )
        for key in stale_keys:
            snapshot = self._registry.pop(key)
            if self._in_flight.get(key, 0):
                self._retired[key] = snapshot

    async def current_runtime(
        self, data_source_id: str, model_profile_id: str | None = None
    ) -> RuntimeSnapshot:
        lease = await self.acquire_runtime(data_source_id, model_profile_id)
        await self.release_runtime(lease)
        return lease.snapshot

    async def release_runtime(self, lease: RuntimeLease) -> None:
        async with self._lock:
            if lease.released:
                return
            lease.released = True
            count = self._in_flight.get(lease.key, 0)
            if count <= 1:
                self._in_flight.pop(lease.key, None)
                self._retired.pop(lease.key, None)
            else:
                self._in_flight[lease.key] = count - 1

    async def has_in_flight_revision(self, data_source_id: str, revision_id: str) -> bool:
        async with self._lock:
            return any(
                key[0] == data_source_id and key[1] == revision_id and count > 0
                for key, count in self._in_flight.items()
            )

    async def in_flight_memory_revisions(self) -> frozenset[str]:
        async with self._lock:
            return frozenset(
                key[5] for key, count in self._in_flight.items() if count > 0
            )

    @asynccontextmanager
    async def memory_revision_retention_guard(self) -> AsyncIterator[frozenset[str]]:
        """Pin the in-flight set stable while expired corpus artifacts are unlinked."""
        async with self._lock:
            yield frozenset(
                key[5] for key, count in self._in_flight.items() if count > 0
            )

    async def prepare_source_revision(
        self, source_id: str, revision_id: str, *,
        identity_override: RuntimeIdentity | None = None,
        candidate_builder: Callable[[Any, Any, Any], RuntimeSnapshot] | None = None,
    ) -> dict[RuntimeKey, RuntimeSnapshot]:
        try:
            source = self.source_store.get_data_source(source_id)
            revision = self.source_store.get_revision(source_id, revision_id)
        except (KeyError, LookupError) as exc:
            raise RuntimeDataSourceNotFound(source_id) from exc
        if not getattr(source, "enabled", False):
            raise RuntimeDataSourceUnavailable("DATA_SOURCE_UNAVAILABLE")
        _default_id, models = self.model_store.list_profiles(seed_if_missing=False)
        candidates: dict[RuntimeKey, RuntimeSnapshot] = {}
        for model in models:
            if getattr(model, "model_kind", "chat") != "chat" or not getattr(model, "api_key", ""):
                continue
            built = (candidate_builder or self.snapshot_builder)(source, revision, model)
            if inspect.isawaitable(built):
                built = await built
            key = self._runtime_key(
                source, revision_id, model, identity_override=identity_override
            )
            if not isinstance(built, RuntimeSnapshot) or built.key != key:
                raise RuntimeError("snapshot builder returned a mismatched cache key")
            candidates[key] = built
        if not candidates:
            raise RuntimeModelNotConfigured("MODEL_NOT_CONFIGURED")
        return candidates

    async def activate_source_revision(
        self,
        source_id: str,
        revision_id: str,
        candidates: dict[RuntimeKey, RuntimeSnapshot],
    ) -> None:
        if not candidates or any(
            key[0] != source_id or key[1] != revision_id or snapshot.key != key
            for key, snapshot in candidates.items()
        ):
            raise ValueError("candidate snapshots do not belong to this source revision")
        async with self._lock:
            next_registry = {
                key: snapshot for key, snapshot in self._registry.items() if key[0] != source_id
            }
            next_registry.update(candidates)
            retired = [
                (key, snapshot)
                for key, snapshot in self._registry.items()
                if key[0] == source_id and key not in next_registry
            ]
            self._registry = next_registry
            for key, snapshot in retired:
                if self._in_flight.get(key, 0):
                    self._retired[key] = snapshot

    async def invalidate_model_profile(self, model_profile_id: str) -> None:
        async with self._lock:
            stale = {
                key: snapshot for key, snapshot in self._registry.items()
                if key[2] == model_profile_id
            }
            self._registry = {
                key: snapshot for key, snapshot in self._registry.items()
                if key[2] != model_profile_id
            }
            for key, snapshot in stale.items():
                if self._in_flight.get(key, 0):
                    self._retired[key] = snapshot
