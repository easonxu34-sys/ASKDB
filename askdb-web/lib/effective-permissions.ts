import { responseError } from "@/lib/auth-api";

export type PermissionView = "user" | "source";
export type PermissionExportView = PermissionView | "all";
export type PermissionScope = "granted" | "all";
export type PermissionStatus =
  | "all"
  | "usable"
  | "granted_blocked"
  | "ungranted"
  | "multiple_sources";

type GrantSourceBase = {
  key: string;
  granted_by: string | null;
  granted_at: string | null;
};

export type EffectiveGrantSource =
  | (GrantSourceBase & {
      type: "direct";
      key: "direct";
      role_id: null;
      role_name: null;
      bound_by: null;
      bound_at: null;
    })
  | (GrantSourceBase & {
      type: "role";
      role_id: string;
      role_name: string | null;
      bound_by: string | null;
      bound_at: string | null;
    })
  | {
      type: "admin_identity";
      key: "admin_identity";
      granted_by: null;
      granted_at: null;
      role_id: null;
      role_name: null;
      bound_by: null;
      bound_at: null;
    };

export type EffectivePermissionItem = {
  user: { id: string; username: string; role: "admin" | "member"; is_active: boolean };
  data_source: {
    id: string;
    display_name: string;
    exists: boolean;
    enabled: boolean;
    runtime_status: string | null;
  };
  grant_sources: EffectiveGrantSource[];
  has_grant: boolean;
  access_allowed: boolean;
  runtime_ready: boolean;
  usable_now: boolean;
  block_reasons: PermissionBlockReason[];
  removal_impacts: Array<{
    source_key: string;
    remaining_sources: string[];
    access_allowed_after: boolean;
    would_lose_access: boolean;
  }>;
};

export type PermissionBlockReason =
  | "ACCOUNT_DISABLED"
  | "SOURCE_MISSING"
  | "SOURCE_DISABLED"
  | "SOURCE_NOT_READY"
  | "NO_GRANT";

export type EffectivePermissionPage = {
  read_at: string;
  target:
    | { type: "user"; id: string; username: string; role: "admin" | "member"; is_active: boolean }
    | {
        type: "source";
        id: string;
        display_name: string;
        enabled: boolean;
        runtime_status: string;
      };
  counts: {
    total: number;
    usable: number;
    granted_blocked: number;
    ungranted: number;
    multiple_sources: number;
    matching: number;
  };
  items: EffectivePermissionItem[];
  next_cursor: string | null;
};

export type EffectivePermissionQuery = {
  view: PermissionView;
  user_id?: string;
  data_source_id?: string;
  scope?: PermissionScope;
  status?: PermissionStatus;
  limit?: number;
  cursor?: string;
};

export type EffectivePermissionExportQuery = Omit<EffectivePermissionQuery, "limit" | "cursor"> & {
  view: PermissionExportView;
};

const BLOCK_REASONS = new Set<PermissionBlockReason>([
  "ACCOUNT_DISABLED",
  "SOURCE_MISSING",
  "SOURCE_DISABLED",
  "SOURCE_NOT_READY",
  "NO_GRANT",
]);
const STATUSES = new Set<PermissionStatus>([
  "all",
  "usable",
  "granted_blocked",
  "ungranted",
  "multiple_sources",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function isText(value: unknown, maxLength = 256): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= maxLength;
}

function isOptionalText(value: unknown): value is string | null {
  return value === null || isText(value, 512);
}

function isTimestamp(value: unknown): value is string {
  return typeof value === "string" && value.length <= 64 && Number.isFinite(Date.parse(value));
}

function isGrantSource(value: unknown): value is EffectiveGrantSource {
  if (!isRecord(value) || !isText(value.key, 256)) return false;
  if (value.type === "admin_identity") {
    return (
      value.key === "admin_identity" &&
      value.granted_by === null &&
      value.granted_at === null &&
      value.role_id === null &&
      value.role_name === null &&
      value.bound_by === null &&
      value.bound_at === null
    );
  }
  if (
    !isOptionalText(value.granted_by) ||
    !(value.granted_at === null || isTimestamp(value.granted_at))
  )
    return false;
  if (value.type === "direct") {
    return (
      value.key === "direct" &&
      value.role_id === null &&
      value.role_name === null &&
      value.bound_by === null &&
      value.bound_at === null
    );
  }
  return (
    value.type === "role" &&
    isText(value.role_id, 128) &&
    value.key === `role:${value.role_id}` &&
    (value.role_name === null || isText(value.role_name)) &&
    isOptionalText(value.bound_by) &&
    (value.bound_at === null || isTimestamp(value.bound_at))
  );
}

function isPermissionItem(value: unknown): value is EffectivePermissionItem {
  if (!isRecord(value) || !isRecord(value.user) || !isRecord(value.data_source)) return false;
  const user = value.user;
  const source = value.data_source;
  return (
    isText(user.id, 128) &&
    isText(user.username) &&
    (user.role === "admin" || user.role === "member") &&
    typeof user.is_active === "boolean" &&
    isText(source.id, 128) &&
    isText(source.display_name) &&
    typeof source.exists === "boolean" &&
    typeof source.enabled === "boolean" &&
    (source.runtime_status === null || isText(source.runtime_status, 64)) &&
    Array.isArray(value.grant_sources) &&
    value.grant_sources.length <= 256 &&
    value.grant_sources.every(isGrantSource) &&
    typeof value.has_grant === "boolean" &&
    typeof value.access_allowed === "boolean" &&
    typeof value.runtime_ready === "boolean" &&
    typeof value.usable_now === "boolean" &&
    Array.isArray(value.block_reasons) &&
    value.block_reasons.every((item) => BLOCK_REASONS.has(item as PermissionBlockReason)) &&
    Array.isArray(value.removal_impacts) &&
    value.removal_impacts.length <= 256 &&
    value.removal_impacts.every(
      (impact) =>
        isRecord(impact) &&
        isText(impact.source_key, 256) &&
        Array.isArray(impact.remaining_sources) &&
        impact.remaining_sources.length <= 256 &&
        impact.remaining_sources.every((key) => isText(key, 256)) &&
        typeof impact.access_allowed_after === "boolean" &&
        typeof impact.would_lose_access === "boolean",
    )
  );
}

export function isEffectivePermissionPage(value: unknown): value is EffectivePermissionPage {
  if (!isRecord(value) || !isRecord(value.target) || !isRecord(value.counts)) return false;
  const target = value.target;
  const counts = value.counts;
  const targetValid =
    target.type === "user"
      ? isText(target.id, 128) &&
        isText(target.username) &&
        (target.role === "admin" || target.role === "member") &&
        typeof target.is_active === "boolean"
      : target.type === "source" &&
        isText(target.id, 128) &&
        isText(target.display_name) &&
        typeof target.enabled === "boolean" &&
        isText(target.runtime_status, 64);
  const countKeys = [
    "total",
    "usable",
    "granted_blocked",
    "ungranted",
    "multiple_sources",
    "matching",
  ] as const;
  return (
    targetValid &&
    isTimestamp(value.read_at) &&
    countKeys.every((key) => Number.isSafeInteger(counts[key]) && (counts[key] as number) >= 0) &&
    Array.isArray(value.items) &&
    value.items.length <= 100 &&
    value.items.every(isPermissionItem) &&
    (value.next_cursor === null || isText(value.next_cursor, 2048))
  );
}

export function effectivePermissionAgentPath(url: URL, exportMode = false): string {
  const incoming = url.searchParams;
  const allowed = new Set(
    exportMode
      ? ["view", "user_id", "data_source_id", "scope", "status"]
      : ["view", "user_id", "data_source_id", "scope", "status", "limit", "cursor"],
  );
  const values = new Map<string, string>();
  for (const [key, value] of incoming) {
    if (!allowed.has(key) || values.has(key)) throw new Error("query parameter is invalid");
    values.set(key, value);
  }
  const view = values.get("view");
  const userId = values.get("user_id");
  const sourceId = values.get("data_source_id");
  if (
    (view === "user" && (!userId || sourceId !== undefined)) ||
    (view === "source" && (!sourceId || userId !== undefined)) ||
    (view === "all" && (!exportMode || userId !== undefined || sourceId !== undefined)) ||
    (view !== "user" && view !== "source" && view !== "all") ||
    (!exportMode && view === "all")
  ) {
    throw new Error("query target is invalid");
  }
  if ((userId && userId.length > 128) || (sourceId && sourceId.length > 128))
    throw new Error("query target is invalid");
  const scope = values.get("scope");
  const status = values.get("status");
  if (scope !== undefined && scope !== "granted" && scope !== "all")
    throw new Error("query scope is invalid");
  if (status !== undefined && !STATUSES.has(status as PermissionStatus))
    throw new Error("query status is invalid");
  const limit = values.get("limit");
  if (limit !== undefined && (!/^\d+$/.test(limit) || Number(limit) < 1 || Number(limit) > 100))
    throw new Error("query page size is invalid");
  const cursor = values.get("cursor");
  if (cursor !== undefined && (cursor.length > 2048 || !/^[A-Za-z0-9_-]+$/.test(cursor)))
    throw new Error("query cursor is invalid");

  const orderedKeys = exportMode
    ? ["view", "user_id", "data_source_id", "scope", "status"]
    : ["view", "user_id", "data_source_id", "scope", "status", "limit", "cursor"];
  const outgoing = new URLSearchParams();
  for (const key of orderedKeys) {
    const value = values.get(key);
    if (value !== undefined) outgoing.set(key, value);
  }
  const suffix = outgoing.toString();
  return `/v1/admin/effective-permissions${exportMode ? "/export" : ""}${suffix ? `?${suffix}` : ""}`;
}

export async function fetchEffectivePermissions(
  query: EffectivePermissionQuery,
): Promise<EffectivePermissionPage> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  const response = await fetch(`/api/admin/effective-permissions?${params}`, {
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error(await responseError(response, "读取有效权限失败。"));
  const payload: unknown = await response.json();
  if (!isEffectivePermissionPage(payload)) throw new Error("有效权限响应格式无效，请刷新后重试。");
  return payload;
}

export async function downloadEffectivePermissions(
  query: EffectivePermissionExportQuery,
): Promise<{ rowCount: number; generatedAt: string; sha256: string }> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  const response = await fetch(`/api/admin/effective-permissions/export?${params}`, {
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!response.ok) throw new Error(await responseError(response, "导出有效权限失败。"));
  if (!response.headers.get("content-type")?.toLowerCase().startsWith("text/csv"))
    throw new Error("导出响应格式无效。");
  const rowCount = Number(response.headers.get("x-export-row-count"));
  const generatedAt = response.headers.get("x-export-generated-at");
  const sha256 = response.headers.get("x-export-sha256");
  if (
    !Number.isSafeInteger(rowCount) ||
    rowCount < 0 ||
    !generatedAt ||
    !isTimestamp(generatedAt) ||
    !sha256 ||
    !/^[a-f0-9]{64}$/i.test(sha256)
  )
    throw new Error("导出文件校验信息无效。");
  const filename = response.headers
    .get("content-disposition")
    ?.match(/filename="([A-Za-z0-9._-]+\.csv)"/i)?.[1];
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename ?? "askdb-effective-permissions.csv";
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 30_000);
  return { rowCount, generatedAt, sha256 };
}
