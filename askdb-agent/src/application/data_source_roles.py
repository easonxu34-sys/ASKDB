from __future__ import annotations

import secrets
import threading
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from integrations.data_source_role_store import DataSourceRoleStore, RoleStoreError


def normalize_role_name(value: str) -> tuple[str, str]:
    name = unicodedata.normalize("NFKC", value).strip()
    if not name or len(name) > 128 or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("角色名称长度或格式无效。")
    return name, name.casefold()


def normalize_ids(values: list[str]) -> list[str]:
    if len(values) > 100 or len(set(values)) != len(values) or any(
        not value or len(value) > 128 for value in values
    ):
        raise ValueError("角色或数据源 ID 列表无效。")
    return sorted(values)


@dataclass(frozen=True)
class _Preview:
    actor_id: str
    target_id: str
    proposal: tuple[Any, ...]
    impact: dict[str, Any]
    expires_at: datetime


class DataSourceRolesApplication:
    def __init__(self, store: DataSourceRoleStore):
        self.store = store
        self._previews: dict[str, _Preview] = {}
        self._preview_lock = threading.Lock()

    def list_roles(self) -> list[dict[str, Any]]:
        return self.store.list_roles()

    def get_role(self, role_id: str) -> dict[str, Any]:
        return self.store.get_role(role_id)

    def create_role(self, *, name: str, description: str, data_source_ids: list[str],
                    actor_id: str, request_id: str | None) -> dict[str, Any]:
        display, key = normalize_role_name(name)
        return self.store.create_role(name=display, name_key=key,
                                      description=description, source_ids=normalize_ids(data_source_ids),
                                      actor_id=actor_id, request_id=request_id)

    def _save_preview(self, actor_id: str, target_id: str, proposal: tuple[Any, ...],
                      impact: dict[str, Any]) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        with self._preview_lock:
            self._previews = {key: value for key, value in self._previews.items()
                              if value.expires_at > now}
            self._previews[token] = _Preview(actor_id, target_id, proposal, impact,
                                             now + timedelta(minutes=5))
        return token

    def _require_preview(self, token: str | None, actor_id: str, target_id: str,
                         proposal: tuple[Any, ...]) -> str | None:
        if token is None:
            return None
        with self._preview_lock:
            preview = self._previews.get(token)
        if preview is None or preview.expires_at <= datetime.now(UTC) or (
            preview.actor_id, preview.target_id, preview.proposal
        ) != (actor_id, target_id, proposal):
            raise RoleStoreError("DATA_SOURCE_ROLE_PREVIEW_STALE", "影响预览已过期，请重新预览。", 409)
        return preview.impact["digest"]

    def preview_page(self, token: str, actor_id: str, page: int, page_size: int) -> dict[str, Any]:
        with self._preview_lock:
            preview = self._previews.get(token)
        if preview is None or preview.actor_id != actor_id or preview.expires_at <= datetime.now(UTC):
            raise RoleStoreError("DATA_SOURCE_ROLE_PREVIEW_STALE", "影响预览已过期，请重新预览。", 409)
        start = (page - 1) * page_size
        return {"preview_token": token, "total": preview.impact["total"],
                "page": page, "page_size": page_size,
                "users": preview.impact["users"][start:start + page_size],
                "expires_at": preview.expires_at.isoformat()}

    def preview_role(self, *, role_id: str, data_source_ids: list[str] | None, delete: bool,
                     actor_id: str, page: int, page_size: int) -> dict[str, Any]:
        source_ids = None if delete else (normalize_ids(data_source_ids) if data_source_ids is not None else None)
        proposal = (delete, tuple(source_ids) if source_ids is not None else None)
        impact = self.store.impact_role(role_id, source_ids)
        token = self._save_preview(actor_id, role_id, proposal, impact)
        return self.preview_page(token, actor_id, page, page_size)

    def change_role(self, *, role_id: str, name: str | None, description: str | None,
                    data_source_ids: list[str] | None, delete: bool, preview_token: str | None,
                    actor_id: str, request_id: str | None) -> dict[str, Any] | None:
        display, key = normalize_role_name(name) if name is not None else (None, None)
        source_ids = None if data_source_ids is None else normalize_ids(data_source_ids)
        proposal = (delete, tuple(source_ids) if source_ids is not None else None)
        digest = self._require_preview(preview_token, actor_id, role_id, proposal)
        result = self.store.change_role(role_id=role_id, name=display, name_key=key,
                                        description=description, source_ids=source_ids, delete=delete,
                                        expected_digest=digest, actor_id=actor_id, request_id=request_id)
        if preview_token:
            with self._preview_lock:
                self._previews.pop(preview_token, None)
        return result

    def get_bindings(self, user_id: str) -> list[str]:
        return self.store.get_bindings(user_id)

    def preview_bindings(self, *, user_id: str, role_ids: list[str], actor_id: str) -> dict[str, Any]:
        ids = normalize_ids(role_ids)
        impact = self.store.impact_bindings(user_id, ids)
        token = self._save_preview(actor_id, user_id, (tuple(ids),), impact)
        return self.preview_page(token, actor_id, 1, 1)

    def replace_bindings(self, *, user_id: str, role_ids: list[str], preview_token: str | None,
                         actor_id: str, request_id: str | None) -> list[str]:
        ids = normalize_ids(role_ids)
        digest = self._require_preview(preview_token, actor_id, user_id, (tuple(ids),))
        result = self.store.replace_bindings(user_id=user_id, role_ids=ids, expected_digest=digest,
                                             actor_id=actor_id, request_id=request_id)
        if preview_token:
            with self._preview_lock:
                self._previews.pop(preview_token, None)
        return result
