import { authMutation, responseError } from "@/lib/auth-api";

export type DataSourceRole = {
  id: string;
  name: string;
  name_key: string;
  description: string;
  version: number;
  data_source_ids: string[];
  user_ids: string[];
};

export type RoleImpactUser = {
  user_id: string;
  username: string;
  lost_data_source_ids: string[];
};

export type RolePreview = {
  preview_token: string;
  total: number;
  page: number;
  page_size: number;
  users: RoleImpactUser[];
  expires_at: string;
};

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isRole(value: unknown): value is DataSourceRole {
  if (!value || typeof value !== "object") return false;
  const role = value as Record<string, unknown>;
  return (
    typeof role.id === "string" &&
    typeof role.name === "string" &&
    typeof role.name_key === "string" &&
    typeof role.description === "string" &&
    typeof role.version === "number" &&
    isStringArray(role.data_source_ids) &&
    isStringArray(role.user_ids)
  );
}

function isPreview(value: unknown): value is RolePreview {
  if (!value || typeof value !== "object") return false;
  const preview = value as Record<string, unknown>;
  return (
    typeof preview.preview_token === "string" &&
    typeof preview.total === "number" &&
    typeof preview.page === "number" &&
    typeof preview.page_size === "number" &&
    typeof preview.expires_at === "string" &&
    Array.isArray(preview.users) &&
    preview.users.every(
      (item) =>
        item &&
        typeof item === "object" &&
        typeof item.user_id === "string" &&
        typeof item.username === "string" &&
        isStringArray(item.lost_data_source_ids),
    )
  );
}

async function checkedJson(response: Response, fallback: string): Promise<unknown> {
  if (!response.ok) throw new Error(await responseError(response, fallback));
  return response.json();
}

export async function fetchRoles(): Promise<DataSourceRole[]> {
  const body = await checkedJson(
    await fetch("/api/admin/data-source-roles", { cache: "no-store" }),
    "读取数据源角色失败。",
  );
  if (
    !body ||
    typeof body !== "object" ||
    !("roles" in body) ||
    !Array.isArray(body.roles) ||
    !body.roles.every(isRole)
  )
    throw new Error("数据源角色响应无效。");
  return body.roles;
}

export async function createRole(
  name: string,
  description: string,
  dataSourceIds: string[],
): Promise<DataSourceRole> {
  const body = await checkedJson(
    await authMutation("/api/admin/data-source-roles", "POST", {
      name,
      description,
      data_source_ids: dataSourceIds,
    }),
    "创建角色失败。",
  );
  if (!body || typeof body !== "object" || !("role" in body) || !isRole(body.role))
    throw new Error("角色响应无效。");
  return body.role;
}

export async function rolePreview(
  roleId: string,
  dataSourceIds: string[] | null,
  deleting: boolean,
): Promise<RolePreview> {
  const body = await checkedJson(
    await authMutation(
      `/api/admin/data-source-roles/${encodeURIComponent(roleId)}/preview`,
      "POST",
      { data_source_ids: dataSourceIds, delete: deleting },
    ),
    "读取权限影响失败。",
  );
  if (!isPreview(body)) throw new Error("权限影响响应无效。");
  return body;
}

export async function fetchPreviewPage(token: string, page: number): Promise<RolePreview> {
  const body = await checkedJson(
    await fetch(
      `/api/admin/data-source-roles/previews/${encodeURIComponent(token)}?page=${page}&page_size=50`,
      { cache: "no-store" },
    ),
    "读取影响账号失败。",
  );
  if (!isPreview(body)) throw new Error("权限影响响应无效。");
  return body;
}

export async function saveRole(
  roleId: string,
  body: {
    name?: string;
    description?: string;
    data_source_ids?: string[];
    preview_token?: string;
  },
): Promise<DataSourceRole> {
  const result = await checkedJson(
    await authMutation(`/api/admin/data-source-roles/${encodeURIComponent(roleId)}`, "PATCH", body),
    "保存角色失败。",
  );
  if (!result || typeof result !== "object" || !("role" in result) || !isRole(result.role))
    throw new Error("角色响应无效。");
  return result.role;
}

export async function deleteRole(roleId: string, previewToken: string): Promise<void> {
  await checkedJson(
    await authMutation(`/api/admin/data-source-roles/${encodeURIComponent(roleId)}`, "DELETE", {
      preview_token: previewToken,
    }),
    "删除角色失败。",
  );
}

export async function previewBindings(userId: string, roleIds: string[]): Promise<RolePreview> {
  const body = await checkedJson(
    await authMutation(
      `/api/admin/users/${encodeURIComponent(userId)}/data-source-roles/preview`,
      "POST",
      { role_ids: roleIds },
    ),
    "读取账号权限影响失败。",
  );
  if (!isPreview(body)) throw new Error("权限影响响应无效。");
  return body;
}

export async function replaceBindings(
  userId: string,
  roleIds: string[],
  previewToken?: string,
): Promise<void> {
  await checkedJson(
    await authMutation(`/api/admin/users/${encodeURIComponent(userId)}/data-source-roles`, "PUT", {
      role_ids: roleIds,
      ...(previewToken ? { preview_token: previewToken } : {}),
    }),
    "保存账号角色失败。",
  );
}
