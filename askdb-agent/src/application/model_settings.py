from __future__ import annotations

import asyncio
from typing import Any

from agent.graph import build_graph
from config import Settings
from integrations.models import build_model
from integrations.wren import build_wren_toolkit
from runtime import project_dialect
from model_settings import (
    ModelConfiguration,
    ModelConfigurationError,
    ModelSettingsUnavailable,
    ModelSettingsStore,
    validate_configuration,
)


class ModelNotConfigured(RuntimeError):
    pass


class ModelProbeFailed(RuntimeError):
    def __init__(self, code: str, diagnostic_code: str | None = None):
        self.code = code
        self.diagnostic_code = diagnostic_code
        super().__init__(code)


class ModelSettingsApplication:
    """Coordinates the profile catalog, connectivity probes, and model runtimes."""

    def __init__(
        self,
        runtime: Any | None = None,
        store: ModelSettingsStore | None = None,
        runtime_manager: Any | None = None,
        wren_store: Any | None = None,
    ):
        self.store = store or ModelSettingsStore()
        self.runtime_manager = runtime_manager
        self.wren_store = wren_store
        self._base_settings: Settings | None = None
        self._provided_runtime = runtime is not None
        self._provided_runtime_value = runtime
        self._runtimes: dict[tuple[str, str], Any] = {}
        self._toolkit: Any | None = None
        self._lock = asyncio.Lock()

    @property
    def _base(self) -> Settings:
        if self._base_settings is None:
            self._base_settings = Settings.from_env()
        return self._base_settings

    @property
    def ready(self) -> bool:
        return self._provided_runtime or bool(self._runtimes)

    async def public_settings(self) -> dict[str, object]:
        async with self._lock:
            return self.store.get_default().public()

    async def public_catalog(self) -> dict[str, object]:
        async with self._lock:
            default_id, profiles = self.store.list_profiles()
            return {
                "default_profile_id": default_id,
                "profiles": [profile.public() for profile in profiles],
                "service_references": self.store.service_references(),
                "personal_index_status": self.store.personal_index_status(),
            }

    async def test(
        self,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        *,
        profile_id: str | None = None,
        use_default_if_missing: bool = False,
        context_window_tokens: int | None = None,
        max_output_tokens: int | None = None,
        tokenizer_id: str | None = None,
        model_kind: str = "chat",
        service_options: dict | None = None,
    ) -> None:
        async with self._lock:
            self.store.ensure_available()
            existing = (
                self.store.get_profile(profile_id)
                if profile_id else self._default_for_edit() if use_default_if_missing else None
            )
            candidate = self._candidate(
                provider, model, base_url, api_key, existing,
                context_window_tokens=context_window_tokens,
                max_output_tokens=max_output_tokens,
                tokenizer_id=tokenizer_id,
                model_kind=model_kind, service_options=service_options,
            )
            await self._probe(candidate)

    async def create(
        self,
        name: str,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        *,
        context_window_tokens: int | None = None,
        max_output_tokens: int | None = None,
        tokenizer_id: str | None = None,
        model_kind: str = "chat",
        service_options: dict | None = None,
    ) -> dict[str, object]:
        async with self._lock:
            self.store.ensure_available()
            candidate = self._candidate(
                provider, model, base_url, api_key, None,
                context_window_tokens=context_window_tokens,
                max_output_tokens=max_output_tokens,
                tokenizer_id=tokenizer_id,
                model_kind=model_kind, service_options=service_options,
            )
            await self._probe(candidate)
            toolkit, next_runtime = self._build_candidate_runtime(candidate)
            profile = self.store.create(candidate, name)
            self._toolkit = toolkit
            self._runtimes[(profile.id, profile.updated_at)] = next_runtime
            return profile.public()

    async def update(
        self,
        profile_id: str,
        name: str,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        *,
        context_window_tokens: int | None = None,
        max_output_tokens: int | None = None,
        tokenizer_id: str | None = None,
        model_kind: str = "chat",
        service_options: dict | None = None,
    ) -> dict[str, object]:
        async with self._lock:
            self.store.ensure_available()
            existing = self.store.get_profile(profile_id)
            candidate = self._candidate(
                provider, model, base_url, api_key, existing,
                context_window_tokens=context_window_tokens,
                max_output_tokens=max_output_tokens,
                tokenizer_id=tokenizer_id,
                model_kind=model_kind, service_options=service_options,
            )
            await self._probe(candidate)
            toolkit, next_runtime = self._build_candidate_runtime(candidate)
            profile = self.store.update(profile_id, candidate, name)
            await self._invalidate_profile(profile_id)
            self._toolkit = toolkit
            self._runtimes[(profile.id, profile.updated_at)] = next_runtime
            return profile.public()

    async def set_default(self, profile_id: str) -> dict[str, object]:
        async with self._lock:
            self.store.set_default(profile_id)
            if self.store.get_profile(profile_id).model_kind != 'chat':
                default_id, profiles = self.store.list_profiles()
                return {'default_profile_id':default_id,'profiles':[profile.public() for profile in profiles],'service_references':self.store.service_references()}
            return {"default_profile_id": profile_id}

    async def delete(self, profile_id: str, new_default_id: str | None = None) -> dict[str, object]:
        async with self._lock:
            default_id = self.store.delete(profile_id, new_default_id)
            await self._invalidate_profile(profile_id)
            return {"default_profile_id": default_id}

    async def clear_credential(self, profile_id: str) -> dict[str, object]:
        async with self._lock:
            profile = self.store.clear_credential(profile_id)
            await self._invalidate_profile(profile_id)
            return profile.public()

    async def save(
        self,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        *,
        context_window_tokens: int | None = None,
        max_output_tokens: int | None = None,
        tokenizer_id: str | None = None,
        model_kind: str = "chat",
        service_options: dict | None = None,
    ) -> dict[str, object]:
        """Backward-compatible write operation for the original singular API."""
        async with self._lock:
            self.store.ensure_available()
            try:
                existing = self.store.get_default()
            except ModelSettingsUnavailable:
                existing = None
            candidate = self._candidate(
                provider, model, base_url, api_key, existing,
                context_window_tokens=context_window_tokens,
                max_output_tokens=max_output_tokens,
                tokenizer_id=tokenizer_id,
                model_kind=model_kind, service_options=service_options,
            )
            await self._probe(candidate)
            toolkit, next_runtime = self._build_candidate_runtime(candidate)
            if existing:
                profile = self.store.update(
                    existing.id, candidate, existing.name or "默认模型"
                )
                await self._invalidate_profile(existing.id)
            else:
                profile = self.store.create(candidate, "默认模型", make_default=True)
            self._toolkit = toolkit
            self._runtimes[(profile.id, profile.updated_at)] = next_runtime
            return profile.public()

    async def clear_default_credential(self) -> dict[str, object]:
        async with self._lock:
            profile = self.store.get_default()
            cleared = self.store.clear_credential(profile.id)
            await self._invalidate_profile(profile.id)
            return cleared.public()

    def _default_for_edit(self) -> ModelConfiguration | None:
        try:
            return self.store.get_default()
        except ModelSettingsUnavailable:
            return None

    def _candidate(
        self,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        existing: ModelConfiguration | None,
        *,
        context_window_tokens: int | None = None,
        max_output_tokens: int | None = None,
        tokenizer_id: str | None = None,
        model_kind: str = "chat",
        service_options: dict | None = None,
    ) -> ModelConfiguration:
        return validate_configuration(
            provider,
            model,
            base_url,
            api_key.strip() or (existing.api_key if existing else ""),
            context_window_tokens=(
                context_window_tokens
                if context_window_tokens is not None
                else existing.context_window_tokens if existing else None
            ),
            max_output_tokens=(
                max_output_tokens
                if max_output_tokens is not None
                else existing.max_output_tokens if existing else None
            ),
            model_kind=model_kind, service_options=service_options,
            tokenizer_id=(
                tokenizer_id
                if tokenizer_id is not None
                else existing.tokenizer_id if existing else None
            ),
        )

    async def current_runtime(self, profile_id: str | None = None) -> Any:
        if self._provided_runtime:
            if profile_id:
                async with self._lock:
                    configuration = self.store.get_profile(profile_id)
                    if not configuration.api_key:
                        raise ModelNotConfigured("MODEL_NOT_CONFIGURED")
            return self._provided_runtime_value
        if self.runtime_manager is not None and self.wren_store is not None:
            source = self.wren_store.get_default_source()
            if source is None:
                raise ModelNotConfigured("DATA_SOURCE_REQUIRED")
            snapshot = await self.runtime_manager.current_runtime(source.id, profile_id)
            return snapshot.graph
        async with self._lock:
            if profile_id:
                configuration = self.store.get_profile(profile_id)
            elif not self.store.has_encryption_key and not self.store.has_saved_settings():
                configuration = self.store._environment_configuration()
                configuration = ModelConfiguration(
                    configuration.provider, configuration.model, configuration.base_url,
                    configuration.api_key, "environment-default", "默认模型", "environment",
                )
            else:
                configuration = self.store.get_default()
            if not configuration.api_key:
                raise ModelNotConfigured("MODEL_NOT_CONFIGURED")
            cache_key = (configuration.id or "environment-default", configuration.updated_at)
            runtime = self._runtimes.get(cache_key)
            if runtime is None:
                toolkit, runtime = self._build_candidate_runtime(configuration)
                self._toolkit = toolkit
                self._runtimes[cache_key] = runtime
            return runtime

    def _build_candidate_runtime(self, configuration: ModelConfiguration) -> tuple[Any, Any]:
        if configuration.model_kind != "chat":
            return None, None
        if self.runtime_manager is not None:
            # Wren-aware graph snapshots are built per source/revision on demand.
            return None, None
        settings = self._base
        if settings.wren_project_dir is None or not settings.wren_profile:
            raise ModelNotConfigured("DATA_SOURCE_REQUIRED")
        toolkit = self._toolkit or build_wren_toolkit(
            settings.wren_project_dir,
            settings.wren_profile,
            wren_home=settings.legacy_wren_home or settings.wren_home,
        )
        return toolkit, build_graph(
            model=build_model(
                configuration,
                **(
                    {"max_tokens": configuration.max_output_tokens}
                    if configuration.max_output_tokens is not None
                    else {}
                ),
            ),
            toolkit=toolkit,
            dialect=project_dialect(settings.wren_project_dir),
        )

    def _invalidate(self, profile_id: str) -> None:
        self._runtimes = {
            key: runtime for key, runtime in self._runtimes.items() if key[0] != profile_id
        }

    async def _invalidate_profile(self, profile_id: str) -> None:
        self._invalidate(profile_id)
        if self.runtime_manager is not None:
            await self.runtime_manager.invalidate_model_profile(profile_id)

    async def _probe(self, configuration: ModelConfiguration) -> None:
        if configuration.model_kind != "chat":
            from integrations.model_services import (
                ModelResponseInvalid,
                ModelServiceError,
                embed,
                rerank,
            )
            if not configuration.api_key:
                raise ModelProbeFailed("MODEL_AUTH_FAILED")
            try:
                if configuration.model_kind == "embedding":
                    await embed(configuration, ["连接测试"])
                else:
                    await rerank(configuration, "连接测试", ["连接测试", "天气"])
            except Exception as exc:
                if isinstance(exc, ModelConfigurationError):
                    raise
                status = getattr(exc, "status_code", None)
                provider_code = (
                    exc.provider_code.lower().replace("_", "")
                    if isinstance(exc, ModelServiceError)
                    else ""
                )
                if status == 401 or status == 403 or provider_code in {
                    "invalidapikey",
                    "unauthorized",
                    "authenticationerror",
                }:
                    code = "MODEL_AUTH_FAILED"
                elif status is not None and 400 <= status < 500:
                    code = "MODEL_REJECTED"
                elif status is not None and status >= 500:
                    code = "MODEL_CONNECTION_FAILED"
                elif isinstance(exc, ModelServiceError) and provider_code:
                    code = "MODEL_REJECTED"
                elif isinstance(exc, ModelResponseInvalid):
                    code = "MODEL_RESPONSE_INVALID"
                else:
                    code = "MODEL_CONNECTION_FAILED"
                diagnostic_code = exc.diagnostic_code if isinstance(exc, ModelResponseInvalid) else None
                raise ModelProbeFailed(code, diagnostic_code) from None
            return
        if not configuration.api_key:
            raise ModelProbeFailed("MODEL_AUTH_FAILED")
        try:
            model = build_model(configuration, max_tokens=16, timeout=15)
            await asyncio.wait_for(model.ainvoke("Reply with OK."), timeout=15)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status == 401 or status == 403:
                code = "MODEL_AUTH_FAILED"
            elif status is not None and 400 <= status < 500:
                code = "MODEL_REJECTED"
            else:
                code = "MODEL_CONNECTION_FAILED"
            raise ModelProbeFailed(code) from None
