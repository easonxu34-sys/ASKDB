from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Mapping

from integrations.effective_permission_store import (
    MAX_EXPORT_BYTES,
    EffectivePermissionStore,
    EffectivePermissionStoreError,
)


GrantSourceType = Literal["direct", "role", "admin_identity"]


@dataclass(frozen=True)
class GrantSource:
    type: GrantSourceType
    key: str
    granted_by: str | None = None
    granted_at: str | None = None
    role_id: str | None = None
    role_name: str | None = None
    bound_by: str | None = None
    bound_at: str | None = None


@dataclass(frozen=True)
class RemovalImpact:
    source_key: str
    remaining_sources: tuple[str, ...]
    access_allowed_after: bool
    would_lose_access: bool


@dataclass(frozen=True)
class EffectivePermission:
    user: dict[str, object]
    data_source: dict[str, object]
    grant_sources: tuple[GrantSource, ...]
    has_grant: bool
    access_allowed: bool
    runtime_ready: bool
    usable_now: bool
    block_reasons: tuple[str, ...]


@dataclass(frozen=True)
class EffectivePermissionExport:
    path: Path
    filename: str
    read_at: str
    row_count: int
    sha256: str
    byte_count: int


CSV_HEADERS = (
    "user_id",
    "user_name",
    "user_role",
    "user_enabled",
    "data_source_id",
    "data_source_name",
    "data_source_enabled",
    "data_source_runtime_status",
    "grant_sources_json",
    "has_grant",
    "access_allowed",
    "usable_now",
    "block_reasons_json",
    "export_view",
    "export_scope",
    "export_status",
    "generated_at",
)


def _permission_item(raw: Mapping[str, object]) -> tuple[EffectivePermission, dict[str, object]]:
    user = raw.get("user")
    source = raw.get("data_source")
    grants = raw.get("grant_sources")
    if not isinstance(user, Mapping) or not isinstance(source, Mapping) or not isinstance(grants, list):
        raise EffectivePermissionStoreError(
            "EFFECTIVE_PERMISSION_STORE_INVALID", "权限视图数据格式无效。", 503
        )
    permission = explain_permission(user, source, grants)
    impacts = [
        preview_source_removal(permission, grant.key)
        for grant in permission.grant_sources
        if grant.type in {"direct", "role"}
    ]
    serialized = {
        "user": permission.user,
        "data_source": permission.data_source,
        "grant_sources": [
            {
                "type": grant.type,
                "key": grant.key,
                "granted_by": grant.granted_by,
                "granted_at": grant.granted_at,
                "role_id": grant.role_id,
                "role_name": grant.role_name,
                "bound_by": grant.bound_by,
                "bound_at": grant.bound_at,
            }
            for grant in permission.grant_sources
        ],
        "has_grant": permission.has_grant,
        "access_allowed": permission.access_allowed,
        "runtime_ready": permission.runtime_ready,
        "usable_now": permission.usable_now,
        "block_reasons": list(permission.block_reasons),
        "removal_impacts": [
            {
                "source_key": impact.source_key,
                "remaining_sources": list(impact.remaining_sources),
                "access_allowed_after": impact.access_allowed_after,
                "would_lose_access": impact.would_lose_access,
            }
            for impact in impacts
        ],
    }
    return permission, serialized


def _csv_cell(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    text = str(value)
    first_visible = text.lstrip(" \t\r\n\ufeff")
    if text and (ord(text[0]) < 32 or text[0] == "\ufeff"):
        return "'" + text
    if first_visible.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _csv_row(
    permission: EffectivePermission,
    *,
    view: str,
    scope: str,
    status: str,
    read_at: str,
) -> tuple[str, ...]:
    user = permission.user
    source = permission.data_source
    grant_sources = json.dumps(
        [
            {
                "type": grant.type,
                "key": grant.key,
                "role_id": grant.role_id,
                "role_name": grant.role_name,
                "granted_by": grant.granted_by,
                "granted_at": grant.granted_at,
                "bound_by": grant.bound_by,
                "bound_at": grant.bound_at,
            }
            for grant in permission.grant_sources
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    block_reasons = json.dumps(permission.block_reasons, ensure_ascii=False, separators=(",", ":"))
    values = (
        user["id"],
        user["username"],
        user["role"],
        user["is_active"],
        source["id"],
        source["display_name"],
        source["enabled"],
        source["runtime_status"],
        grant_sources,
        permission.has_grant,
        permission.access_allowed,
        permission.usable_now,
        block_reasons,
        view,
        scope,
        status,
        read_at,
    )
    return tuple(_csv_cell(value) for value in values)


def _write_csv_row(file: io.BufferedWriter, values: tuple[object, ...], byte_count: int) -> int:
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\r\n").writerow(values)
    encoded = buffer.getvalue().encode("utf-8")
    next_byte_count = byte_count + len(encoded)
    if next_byte_count > MAX_EXPORT_BYTES:
        raise EffectivePermissionStoreError(
            "EXPORT_BYTE_LIMIT", "导出文件超过大小上限，请缩小筛选范围。", 413
        )
    file.write(encoded)
    return next_byte_count


class EffectivePermissionsApplication:
    def __init__(self, store: EffectivePermissionStore):
        self.store = store

    def query(
        self,
        *,
        view: Literal["user", "source"],
        user_id: str | None,
        data_source_id: str | None,
        scope: Literal["granted", "all"],
        status: Literal["all", "usable", "granted_blocked", "ungranted", "multiple_sources"],
        limit: int,
        cursor: str | None,
    ) -> dict[str, object]:
        page = self.store.query(
            view=view,
            user_id=user_id,
            data_source_id=data_source_id,
            scope=scope,
            status=status,
            limit=limit,
            cursor=cursor,
        )
        items = [_permission_item(item)[1] for item in page["items"]]
        return {**page, "items": items}

    def prepare_export(
        self,
        *,
        view: Literal["user", "source", "all"],
        user_id: str | None,
        data_source_id: str | None,
        scope: Literal["granted", "all"],
        status: Literal["all", "usable", "granted_blocked", "ungranted", "multiple_sources"],
        actor_id: str,
        request_id: str | None,
    ) -> EffectivePermissionExport:
        snapshot = self.store.read_export(
            view=view,
            user_id=user_id,
            data_source_id=data_source_id,
            scope=scope,
            status=status,
        )
        read_at = str(snapshot["read_at"])
        permissions = [_permission_item(item)[0] for item in snapshot["items"]]
        target_id = snapshot["target"].get("id") if isinstance(snapshot["target"], dict) else None
        descriptor, name = tempfile.mkstemp(prefix="askdb-effective-permissions-", suffix=".csv")
        path = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as file:
                byte_count = len(b"\xef\xbb\xbf")
                if byte_count > MAX_EXPORT_BYTES:
                    raise EffectivePermissionStoreError(
                        "EXPORT_BYTE_LIMIT", "导出文件超过大小上限，请缩小筛选范围。", 413
                    )
                file.write(b"\xef\xbb\xbf")
                byte_count = _write_csv_row(file, CSV_HEADERS, byte_count)
                for permission in permissions:
                    byte_count = _write_csv_row(
                        file,
                        _csv_row(
                            permission,
                            view=view,
                            scope=scope,
                            status=status,
                            read_at=read_at,
                        ),
                        byte_count,
                    )
            byte_count = path.stat().st_size
            if byte_count > MAX_EXPORT_BYTES:
                raise EffectivePermissionStoreError(
                    "EXPORT_BYTE_LIMIT", "导出文件超过大小上限，请缩小筛选范围。", 413
                )
            digest = hashlib.sha256()
            with path.open("rb") as file:
                for chunk in iter(lambda: file.read(64 * 1024), b""):
                    digest.update(chunk)
            sha256 = digest.hexdigest()
            generated_at = datetime.fromisoformat(read_at)
            filename = f"askdb-effective-permissions-{generated_at:%Y%m%dT%H%M%SZ}.csv"
            artifact = EffectivePermissionExport(
                path=path,
                filename=filename,
                read_at=read_at,
                row_count=len(permissions),
                sha256=sha256,
                byte_count=byte_count,
            )
            self.store.audit_export(
                actor_id=actor_id,
                request_id=request_id,
                summary={
                    "format_version": 1,
                    "view": view,
                    "target_id": target_id,
                    "scope": scope,
                    "status": status,
                    "row_count": len(permissions),
                    "byte_count": byte_count,
                    "generated_at": read_at,
                    "sha256": sha256,
                },
            )
            return artifact
        except BaseException:
            path.unlink(missing_ok=True)
            raise


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _grant_source(raw: Mapping[str, object]) -> GrantSource:
    source_type = raw.get("type")
    if source_type == "direct":
        return GrantSource(
            type="direct",
            key="direct",
            granted_by=_optional_text(raw.get("granted_by")),
            granted_at=_optional_text(raw.get("granted_at")),
        )
    if source_type == "role":
        role_id = _required_text(raw.get("role_id"), "role_id")
        return GrantSource(
            type="role",
            key=f"role:{role_id}",
            role_id=role_id,
            role_name=_optional_text(raw.get("role_name")),
            granted_by=_optional_text(raw.get("granted_by")),
            granted_at=_optional_text(raw.get("granted_at")),
            bound_by=_optional_text(raw.get("bound_by")),
            bound_at=_optional_text(raw.get("bound_at")),
        )
    raise ValueError("grant source type is invalid")


def explain_permission(
    user: Mapping[str, object],
    source: Mapping[str, object],
    grants: list[Mapping[str, object]] | tuple[Mapping[str, object], ...],
) -> EffectivePermission:
    """Explain one user/source pair without collapsing overlapping grant paths."""
    user_id = _required_text(user.get("id"), "user.id")
    username = _required_text(user.get("username"), "user.username")
    role = user.get("role")
    if role not in {"admin", "member"}:
        raise ValueError("user.role is invalid")
    is_active = bool(user.get("is_active"))

    source_id = _required_text(source.get("id"), "data_source.id")
    exists = bool(source.get("exists", True))
    display_name = _optional_text(source.get("display_name")) or source_id
    enabled = bool(source.get("enabled")) if exists else False
    runtime_status = _optional_text(source.get("runtime_status")) if exists else None

    if role == "admin":
        grant_sources = (GrantSource(type="admin_identity", key="admin_identity"),)
    else:
        parsed = [_grant_source(grant) for grant in grants]
        direct_sources = [item for item in parsed if item.type == "direct"]
        role_sources = sorted(
            (item for item in parsed if item.type == "role"),
            key=lambda item: (
                next(
                    (
                        str(grant.get("role_name_key") or "").casefold()
                        for grant in grants
                        if grant.get("type") == "role" and grant.get("role_id") == item.role_id
                    ),
                    (item.role_name or "").casefold(),
                ),
                item.role_id or "",
            ),
        )
        grant_sources = tuple(direct_sources + role_sources)

    has_grant = bool(grant_sources)
    access_allowed = bool(
        is_active and exists and enabled and (role == "admin" or has_grant)
    )
    runtime_ready = bool(exists and runtime_status == "ready")
    usable_now = access_allowed and runtime_ready

    block_reasons: list[str] = []
    if not is_active:
        block_reasons.append("ACCOUNT_DISABLED")
    if not exists:
        block_reasons.append("SOURCE_MISSING")
    else:
        if not enabled:
            block_reasons.append("SOURCE_DISABLED")
        if not runtime_ready:
            block_reasons.append("SOURCE_NOT_READY")
    if not has_grant:
        block_reasons.append("NO_GRANT")

    return EffectivePermission(
        user={
            "id": user_id,
            "username": username,
            "role": role,
            "is_active": is_active,
        },
        data_source={
            "id": source_id,
            "display_name": display_name,
            "exists": exists,
            "enabled": enabled,
            "runtime_status": runtime_status,
        },
        grant_sources=grant_sources,
        has_grant=has_grant,
        access_allowed=access_allowed,
        runtime_ready=runtime_ready,
        usable_now=usable_now,
        block_reasons=tuple(block_reasons),
    )


def preview_source_removal(
    permission: EffectivePermission, source_key: str
) -> RemovalImpact:
    """Describe the effect of removing one direct or role grant path."""
    if any(item.type == "admin_identity" for item in permission.grant_sources):
        raise ValueError("administrator identity is not a removable grant source")

    remaining = tuple(
        item.key for item in permission.grant_sources if item.key != source_key
    )
    if len(remaining) == len(permission.grant_sources):
        raise ValueError("grant source is not present in the current permission")

    source = permission.data_source
    user = permission.user
    after = bool(
        user["is_active"]
        and source["exists"]
        and source["enabled"]
        and remaining
    )
    return RemovalImpact(
        source_key=source_key,
        remaining_sources=remaining,
        access_allowed_after=after,
        would_lose_access=permission.access_allowed and not after,
    )
