"use client";

import {
  CheckIcon,
  CopyIcon,
  KeyRoundIcon,
  LoaderCircleIcon,
  PlusIcon,
  ShieldCheckIcon,
  UserRoundIcon,
  UsersIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  authMutation,
  fetchCurrentUser,
  responseError,
  type AdminUser,
  type AuthUser,
} from "@/lib/auth-api";
import { SettingsPageHeader } from "@/components/settings/settings-page-header";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ComposerSelect } from "@/components/ui/composer-select";
import {
  fetchDataSourceCatalog,
  isChatAvailableDataSource,
  type DataSourceSummary,
} from "@/lib/data-sources";
import {
  fetchRoles,
  previewBindings,
  replaceBindings,
  type DataSourceRole,
} from "@/lib/data-source-roles";
import { DataSourceRoleManagement } from "@/components/user-management/data-source-role-management";
import { EffectivePermissionsView } from "@/components/user-management/effective-permissions-view";

type OneTimePassword = { username: string; password: string };
type ConfirmationRequest = {
  title: string;
  description: string;
  confirmLabel?: string;
  confirmVariant?: "default" | "destructive";
};

function isAdminUser(value: unknown): value is AdminUser {
  if (!value || typeof value !== "object") return false;
  const user = value as Record<string, unknown>;
  return (
    typeof user.id === "string" &&
    typeof user.username === "string" &&
    (user.role === "admin" || user.role === "member") &&
    typeof user.is_active === "boolean" &&
    typeof user.must_change_password === "boolean" &&
    Array.isArray(user.data_source_ids) &&
    user.data_source_ids.every((id) => typeof id === "string") &&
    (user.role_ids === undefined ||
      (Array.isArray(user.role_ids) && user.role_ids.every((id) => typeof id === "string"))) &&
    (user.direct_data_source_ids === undefined ||
      (Array.isArray(user.direct_data_source_ids) &&
        user.direct_data_source_ids.every((id) => typeof id === "string"))) &&
    (user.data_source_roles === undefined ||
      (Array.isArray(user.data_source_roles) &&
        user.data_source_roles.every(
          (item) =>
            item &&
            typeof item.id === "string" &&
            typeof item.name === "string" &&
            Array.isArray(item.data_source_ids) &&
            item.data_source_ids.every((id: unknown) => typeof id === "string"),
        ))) &&
    (user.effective_data_sources === undefined ||
      (Array.isArray(user.effective_data_sources) &&
        user.effective_data_sources.every(
          (item) =>
            item &&
            typeof item.data_source_id === "string" &&
            typeof item.available === "boolean" &&
            Array.isArray(item.source_ids) &&
            item.source_ids.every((id: unknown) => typeof id === "string"),
        )))
  );
}

export function UserManagement() {
  const [activeTab, setActiveTab] = useState<"accounts" | "roles" | "effective">("accounts");
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [sources, setSources] = useState<DataSourceSummary[]>([]);
  const [permissionSources, setPermissionSources] = useState<DataSourceSummary[]>([]);
  const [roles, setRoles] = useState<DataSourceRole[]>([]);
  const [permissionUserId, setPermissionUserId] = useState<string | null>(null);
  const [permissionRequestKey, setPermissionRequestKey] = useState(0);
  const [focusUserId, setFocusUserId] = useState<string | null>(null);
  const [focusRoleId, setFocusRoleId] = useState<string | null>(null);
  const [newUserRoleIds, setNewUserRoleIds] = useState<string[]>([]);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<"admin" | "member">("member");
  const [busy, setBusy] = useState(false);
  const [refreshingTab, setRefreshingTab] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [oneTimePassword, setOneTimePassword] = useState<OneTimePassword | null>(null);
  const [copied, setCopied] = useState(false);
  const [selectedGrants, setSelectedGrants] = useState<Record<string, string>>({});
  const [drafts, setDrafts] = useState<
    Record<string, Pick<AdminUser, "username" | "role" | "is_active"> & { role_ids: string[] }>
  >({});
  const [confirmation, setConfirmation] = useState<ConfirmationRequest | null>(null);
  const confirmationResolverRef = useRef<((confirmed: boolean) => void) | null>(null);
  const tabRefreshIdRef = useRef(0);
  const tabOrder = ["accounts", "roles", "effective"] as const;

  function requestConfirmation(request: ConfirmationRequest): Promise<boolean> {
    if (confirmationResolverRef.current) return Promise.resolve(false);
    return new Promise((resolve) => {
      confirmationResolverRef.current = resolve;
      setConfirmation(request);
    });
  }

  function finishConfirmation(confirmed: boolean) {
    const resolve = confirmationResolverRef.current;
    confirmationResolverRef.current = null;
    setConfirmation(null);
    resolve?.(confirmed);
  }

  async function fetchUsers(): Promise<AdminUser[]> {
    const response = await fetch("/api/admin/users", { cache: "no-store" });
    if (!response.ok) throw new Error(await responseError(response, "读取账号列表失败。"));
    const payload: unknown = await response.json();
    if (
      !payload ||
      typeof payload !== "object" ||
      !("users" in payload) ||
      !Array.isArray(payload.users) ||
      !payload.users.every(isAdminUser)
    ) {
      throw new Error("账号列表响应无效。");
    }
    return payload.users;
  }

  function applyUsers(nextUsers: AdminUser[], preserveDrafts = false) {
    setUsers(nextUsers);
    setDrafts((current) =>
      Object.fromEntries(
        nextUsers.map((user) => [
          user.id,
          preserveDrafts && current[user.id]
            ? current[user.id]
            : {
                username: user.username,
                role: user.role,
                is_active: user.is_active,
                role_ids: user.role_ids ?? [],
              },
        ]),
      ),
    );
  }

  async function loadUsers() {
    applyUsers(await fetchUsers());
  }

  async function loadRoles() {
    setRoles(await fetchRoles());
  }

  function selectTab(nextTab: "accounts" | "roles" | "effective") {
    if (nextTab === activeTab) return;
    setActiveTab(nextTab);
    setError("");
    setRefreshingTab(true);
    const requestId = ++tabRefreshIdRef.current;
    void Promise.all([fetchUsers(), fetchRoles()])
      .then(([nextUsers, nextRoles]) => {
        if (requestId !== tabRefreshIdRef.current) return;
        applyUsers(nextUsers, true);
        setRoles(nextRoles);
      })
      .catch((cause) => {
        if (requestId === tabRefreshIdRef.current)
          setError(cause instanceof Error ? cause.message : "刷新账号与角色失败，请重试。");
      })
      .finally(() => {
        if (requestId === tabRefreshIdRef.current) setRefreshingTab(false);
      });
  }

  useEffect(() => {
    let active = true;
    fetchCurrentUser()
      .then(async (user) => {
        if (!active) return;
        setCurrentUser(user);
        if (user?.role === "admin") {
          await loadUsers();
          await loadRoles();
          try {
            const catalog = await fetchDataSourceCatalog();
            if (active) {
              setPermissionSources(catalog.data_sources);
              setSources(catalog.data_sources.filter(isChatAvailableDataSource));
            }
          } catch {
            if (active) setNotice("暂时无法读取可用数据源；账号管理仍可继续。");
          }
        }
      })
      .catch((cause) => {
        if (active) setError(cause instanceof Error ? cause.message : "读取管理信息失败。");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (activeTab !== "accounts" || !focusUserId || !users.some((user) => user.id === focusUserId))
      return;
    const card = document.getElementById(`user-card-${focusUserId}`);
    if (card) {
      card.scrollIntoView({ block: "center" });
      card.focus();
      setFocusUserId(null);
    }
  }, [activeTab, focusUserId, users]);

  useEffect(() => {
    if (activeTab !== "roles" || !focusRoleId || !roles.some((item) => item.id === focusRoleId))
      return;
    const role = document.getElementById(`data-source-role-${focusRoleId}`);
    if (role) {
      role.scrollIntoView({ block: "center" });
      role.focus();
      setFocusRoleId(null);
    }
  }, [activeTab, focusRoleId, roles]);

  const sourceById = useMemo(
    () => new Map(sources.map((source) => [source.id, source])),
    [sources],
  );

  async function createUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !username.trim()) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await authMutation("/api/admin/users", "POST", {
        username: username.trim(),
        role,
        ...(role === "member" ? { role_ids: newUserRoleIds } : {}),
      });
      if (!response.ok) throw new Error(await responseError(response, "创建账号失败。"));
      const payload = (await response.json()) as { user?: AdminUser; temporary_password?: string };
      if (
        !payload.user ||
        !isAdminUser(payload.user) ||
        typeof payload.temporary_password !== "string"
      ) {
        throw new Error("账号已创建，但临时密码响应无效。请通过重置密码重新生成。");
      }
      setOneTimePassword({ username: payload.user.username, password: payload.temporary_password });
      setCopied(false);
      setUsername("");
      setRole("member");
      setNewUserRoleIds([]);
      await loadUsers();
      setNotice("账号已创建。请立即安全地交付临时密码。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建账号失败。");
    } finally {
      setBusy(false);
    }
  }

  async function saveUser(user: AdminUser) {
    const draft = drafts[user.id];
    if (!draft || busy) return;
    if (
      (draft.role !== user.role || draft.is_active !== user.is_active) &&
      !(await requestConfirmation({
        title: "确认保存账号设置？",
        description: "管理员权限或账号状态变更会立即撤销该账号的现有登录会话。",
        confirmLabel: "保存设置",
      }))
    )
      return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await authMutation(
        `/api/admin/users/${encodeURIComponent(user.id)}`,
        "PATCH",
        {
          username: draft.username,
          role: draft.role,
          is_active: draft.is_active,
          ...(user.role === "admin" && draft.role === "member" ? { role_ids: draft.role_ids } : {}),
        },
      );
      if (!response.ok) throw new Error(await responseError(response, "保存账号失败。"));
      await loadUsers();
      setNotice(`已保存 ${draft.username} 的账号设置。`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存账号失败。");
    } finally {
      setBusy(false);
    }
  }

  async function resetPassword(user: AdminUser) {
    if (busy) return;
    if (
      !(await requestConfirmation({
        title: "确认重置密码？",
        description: `重置 ${user.username} 的密码会撤销其所有登录会话，并要求下次登录修改临时密码。`,
        confirmLabel: "重置密码",
        confirmVariant: "destructive",
      }))
    )
      return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await authMutation(
        `/api/admin/users/${encodeURIComponent(user.id)}/reset-password`,
        "POST",
      );
      if (!response.ok) throw new Error(await responseError(response, "重置密码失败。"));
      const payload = (await response.json()) as { temporary_password?: unknown };
      if (typeof payload.temporary_password !== "string")
        throw new Error("重置成功，但临时密码响应无效。");
      setOneTimePassword({ username: user.username, password: payload.temporary_password });
      setCopied(false);
      setNotice(`已为 ${user.username} 生成一次性临时密码。`);
      await loadUsers();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "重置密码失败。");
    } finally {
      setBusy(false);
    }
  }

  async function updateGrant(user: AdminUser, sourceId: string, grant: boolean): Promise<boolean> {
    if (busy) return false;
    const sourceName = sourceById.get(sourceId)?.display_name ?? sourceId;
    if (
      !(await requestConfirmation({
        title: `确认${grant ? "分配" : "撤销"}数据源权限？`,
        description: grant
          ? `向 ${user.username} 分配数据源“${sourceName}”的直接授权。`
          : `撤销 ${user.username} 对“${sourceName}”的直接授权。${user.effective_data_sources?.find((entry) => entry.data_source_id === sourceId)?.source_ids.some((id) => id !== "direct") ? "其他角色仍授予此数据源，访问权保留。" : "账号将失去此数据源访问权。"}`,
        confirmLabel: grant ? "分配权限" : "撤销权限",
        confirmVariant: grant ? "default" : "destructive",
      }))
    )
      return false;
    setBusy(true);
    setError("");
    try {
      const response = await authMutation(
        `/api/admin/users/${encodeURIComponent(user.id)}/data-sources/${encodeURIComponent(sourceId)}`,
        grant ? "PUT" : "DELETE",
      );
      if (!response.ok) throw new Error(await responseError(response, "更新数据源权限失败。"));
      await loadUsers();
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "更新数据源权限失败。");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function toggleUserRole(user: AdminUser, roleId: string) {
    if (busy) return;
    const current = user.role_ids ?? [];
    const next = current.includes(roleId)
      ? current.filter((id) => id !== roleId)
      : [...current, roleId];
    try {
      const preview = await previewBindings(user.id, next);
      const lost = preview.users[0]?.lost_data_source_ids ?? [];
      const description = lost.length
        ? `此账号将失去 ${lost.map((id) => sourceById.get(id)?.display_name ?? id).join("、")} 的访问权。`
        : "其他直接授权或角色仍覆盖相同数据源，现有访问权保留。";
      if (
        !(await requestConfirmation({
          title: "确认更改账号的数据源角色？",
          description,
          confirmLabel: "保存绑定",
          confirmVariant: current.includes(roleId) ? "destructive" : "default",
        }))
      )
        return;
      setBusy(true);
      await replaceBindings(user.id, next, preview.preview_token);
      await Promise.all([loadUsers(), loadRoles()]);
      setNotice("账号的数据源角色已更新。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "更新账号角色失败。");
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <>
        <SettingsPageHeader
          title="用户与权限"
          description="管理用户账号和可访问的数据源"
          icon={UsersIcon}
        />
        <main
          className="flex min-h-[calc(100dvh-4rem)] items-center justify-center bg-[#f7f5f0] text-sm text-[#77736b]"
          role="status"
        >
          正在加载账号管理…
        </main>
      </>
    );
  }
  if (!currentUser || currentUser.role !== "admin") {
    return (
      <main className="flex min-h-dvh items-center justify-center bg-[#f7f5f0] px-6 text-[#30302e]">
        <section className="max-w-md rounded-2xl border border-[#e7e2d8] bg-[#fbfaf7] p-7 text-center">
          <ShieldCheckIcon className="mx-auto size-7 text-[#c57650]" aria-hidden="true" />
          <h1 className="mt-4 text-lg font-semibold">此页面仅供管理员使用</h1>
          <p className="mt-2 text-sm text-[#77736b]">请使用管理员账号登录，或返回 AskDB 对话。</p>
          <Link
            className="mt-5 inline-flex rounded-lg bg-[#b96f4b] px-4 py-2 text-sm text-white focus-visible:ring-2 focus-visible:ring-[#30302e]"
            href="/"
          >
            返回对话
          </Link>
        </section>
      </main>
    );
  }

  return (
    <>
      <SettingsPageHeader
        title="用户与权限"
        description="管理用户账号和可访问的数据源"
        icon={UsersIcon}
        rightSlot={
          <span className="hidden rounded-full border border-[#e7e2d8] bg-[#fbfaf7] px-3 py-1.5 text-xs text-[#77736b] sm:inline-flex">
            管理员 · {currentUser.username}
          </span>
        }
      />
      <main className="min-h-[calc(100dvh-4rem)] bg-[#f7f5f0] px-4 py-7 text-[#30302e] sm:px-8 sm:py-10">
        <div className="mx-auto max-w-6xl">
          <div
            role="tablist"
            aria-label="用户与权限管理"
            className="flex w-fit max-w-full gap-1 rounded-xl bg-[#eee9e0] p-1"
          >
            {(
              [
                ["accounts", "用户管理", users.length],
                ["roles", "数据源角色", roles.length],
                ["effective", "有效权限", null],
              ] as const
            ).map(([tab, label, count]) => (
              <button
                key={tab}
                id={`user-management-tab-${tab}`}
                type="button"
                role="tab"
                aria-selected={activeTab === tab}
                aria-controls={`user-management-panel-${tab}`}
                tabIndex={activeTab === tab ? 0 : -1}
                onClick={() => selectTab(tab)}
                onKeyDown={(event) => {
                  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                  event.preventDefault();
                  const index = tabOrder.indexOf(tab);
                  const nextIndex =
                    event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? tabOrder.length - 1
                        : event.key === "ArrowRight"
                          ? (index + 1) % tabOrder.length
                          : (index - 1 + tabOrder.length) % tabOrder.length;
                  const nextTab = tabOrder[nextIndex];
                  selectTab(nextTab);
                  document.getElementById(`user-management-tab-${nextTab}`)?.focus();
                }}
                className={`rounded-lg px-4 py-2.5 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#c57650] ${
                  activeTab === tab
                    ? "bg-[#fbfaf7] text-[#514b42] shadow-sm"
                    : "text-[#77736b] hover:text-[#514b42]"
                }`}
              >
                {label} {count !== null && <span className="ml-1 text-xs opacity-70">{count}</span>}
              </button>
            ))}
          </div>
          {refreshingTab && (
            <p role="status" className="mt-3 text-xs text-[#77736b]">
              正在刷新账号与角色…
            </p>
          )}
          {error && (
            <p
              role="alert"
              className="mt-6 rounded-xl border border-[#e7c6bd] bg-[#f8e9e4] px-4 py-3 text-sm text-[#9c4037]"
            >
              {error}
            </p>
          )}
          {notice && (
            <p
              role="status"
              className="mt-6 rounded-xl border border-[#d7e0d0] bg-[#eff3eb] px-4 py-3 text-sm text-[#54734d]"
            >
              {notice}
            </p>
          )}
          <div
            id="user-management-panel-accounts"
            role="tabpanel"
            aria-labelledby="user-management-tab-accounts"
            hidden={activeTab !== "accounts"}
          >
            {oneTimePassword && (
              <section className="mt-4 rounded-2xl border border-[#d8c6a9] bg-[#f4eee2] p-5 sm:flex sm:items-center sm:justify-between sm:gap-6">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-sm font-semibold text-[#514b42]">
                    <KeyRoundIcon className="size-4 text-[#b96f4b]" aria-hidden="true" />
                    一次性临时密码 · {oneTimePassword.username}
                  </div>
                  <p className="mt-2 break-all font-mono text-sm tracking-wide text-[#35332e]">
                    {oneTimePassword.password}
                  </p>
                  <p className="mt-2 text-xs text-[#77736b]">
                    此密码只显示这一次；首次登录时系统会要求修改。
                  </p>
                </div>
                <div className="mt-4 flex shrink-0 gap-2 sm:mt-0">
                  <button
                    type="button"
                    onClick={() =>
                      void navigator.clipboard
                        .writeText(oneTimePassword.password)
                        .then(() => setCopied(true))
                        .catch(() => setError("复制失败，请手动安全保存临时密码。"))
                    }
                    className="inline-flex h-9 items-center gap-2 rounded-lg border border-[#d9c9b3] bg-[#fbfaf7] px-3 text-xs text-[#514b42] hover:bg-white focus-visible:ring-2 focus-visible:ring-[#c57650]"
                  >
                    {copied ? (
                      <CheckIcon className="size-3.5" aria-hidden="true" />
                    ) : (
                      <CopyIcon className="size-3.5" aria-hidden="true" />
                    )}
                    {copied ? "已复制" : "复制密码"}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setOneTimePassword(null);
                      setCopied(false);
                    }}
                    className="h-9 rounded-lg px-3 text-xs text-[#77736b] hover:bg-[#e8e3d8] focus-visible:ring-2 focus-visible:ring-[#c57650]"
                  >
                    关闭
                  </button>
                </div>
              </section>
            )}

            <section className="mt-7 rounded-2xl border border-[#e7e2d8] bg-[#fbfaf7] p-5 sm:p-6">
              <div className="flex items-start gap-3">
                <div className="flex size-8 items-center justify-center rounded-xl bg-[#eee8dc] text-[#9b654b]">
                  <PlusIcon className="size-4" aria-hidden="true" />
                </div>
                <div>
                  <h2 className="text-sm font-semibold">创建账号</h2>
                  <p className="mt-1 text-xs leading-5 text-[#89847a]">
                    生成一次性临时密码。新账号首次登录后必须设置个人密码。
                  </p>
                </div>
              </div>
              <form
                onSubmit={(event) => void createUser(event)}
                className="mt-5 grid gap-3 sm:grid-cols-[1fr_auto_auto] sm:items-center"
              >
                <label className="sr-only" htmlFor="new-username">
                  用户名或工号
                </label>
                <input
                  id="new-username"
                  autoComplete="off"
                  maxLength={128}
                  required
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  placeholder="输入用户名或工号"
                  className="h-11 min-w-0 rounded-xl border border-[#e3ddd2] bg-white px-3.5 text-sm outline-none focus:border-[#c57650] focus:ring-4 focus:ring-[#c57650]/10"
                />
                <label className="flex h-11 items-center gap-2 rounded-xl border border-[#e3ddd2] bg-white px-3 text-xs text-[#625d54]">
                  <input
                    type="checkbox"
                    checked={role === "admin"}
                    onChange={(event) => setRole(event.target.checked ? "admin" : "member")}
                    className="size-4 accent-[#b96f4b]"
                  />
                  管理员权限
                </label>
                <button
                  disabled={busy || !username.trim()}
                  className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-[#b96f4b] px-5 text-sm font-medium text-white hover:bg-[#a96040] focus-visible:ring-2 focus-visible:ring-[#30302e] disabled:cursor-wait disabled:opacity-60"
                >
                  {busy ? (
                    <LoaderCircleIcon
                      className="size-4 animate-spin motion-reduce:animate-none"
                      aria-hidden="true"
                    />
                  ) : (
                    <PlusIcon className="size-4" aria-hidden="true" />
                  )}
                  创建账号
                </button>
              </form>
              {role === "member" && roles.length > 0 && (
                <fieldset className="mt-4 flex flex-wrap gap-3 text-xs text-[#625d54]">
                  <legend className="mb-2 font-medium">数据源角色（可多选）</legend>
                  {roles.map((item) => (
                    <label
                      key={item.id}
                      className="inline-flex items-center gap-2 rounded-lg border border-[#e3ddd2] px-3 py-2"
                    >
                      <input
                        type="checkbox"
                        checked={newUserRoleIds.includes(item.id)}
                        onChange={(event) =>
                          setNewUserRoleIds((old) =>
                            event.target.checked
                              ? [...old, item.id]
                              : old.filter((id) => id !== item.id),
                          )
                        }
                        className="size-4 accent-[#b96f4b]"
                      />
                      {item.name}
                    </label>
                  ))}
                </fieldset>
              )}
            </section>

            <section className="mt-7">
              <div className="mb-3 flex items-end justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold">现有账号</h2>
                  <p className="mt-1 text-xs text-[#89847a]">
                    非管理员账号只能使用分配给自己的已启用且配置就绪的数据源。
                  </p>
                </div>
                <span className="text-xs text-[#89847a]">{users.length} 个账号</span>
              </div>
              <div className="space-y-3">
                {users.map((user) => {
                  const draft = drafts[user.id] ?? {
                    username: user.username,
                    role: user.role,
                    is_active: user.is_active,
                  };
                  const grantOptions = sources.filter(
                    (source) => !user.data_source_ids.includes(source.id),
                  );
                  return (
                    <article
                      key={user.id}
                      id={`user-card-${user.id}`}
                      tabIndex={-1}
                      className="rounded-2xl border border-[#e7e2d8] bg-[#fbfaf7] p-5 sm:p-6"
                    >
                      <div className="grid gap-4 lg:grid-cols-[minmax(12rem,1fr)_9rem_8rem_auto_auto] lg:items-end">
                        <label className="block space-y-1.5">
                          <span className="text-[11px] text-[#89847a]">用户名 / 工号</span>
                          <input
                            maxLength={128}
                            value={draft.username}
                            onChange={(event) =>
                              setDrafts((old) => ({
                                ...old,
                                [user.id]: { ...draft, username: event.target.value },
                              }))
                            }
                            className="h-10 w-full rounded-lg border border-[#e3ddd2] bg-white px-3 text-sm outline-none focus:border-[#c57650] focus:ring-4 focus:ring-[#c57650]/10"
                          />
                        </label>
                        <label className="flex h-10 items-center gap-2 text-xs text-[#625d54]">
                          <input
                            type="checkbox"
                            checked={draft.role === "admin"}
                            disabled={user.id === currentUser.user_id}
                            onChange={(event) =>
                              setDrafts((old) => ({
                                ...old,
                                [user.id]: {
                                  ...draft,
                                  role: event.target.checked ? "admin" : "member",
                                },
                              }))
                            }
                            className="size-4 accent-[#b96f4b] disabled:cursor-not-allowed"
                          />
                          管理员权限
                        </label>
                        <label className="flex h-10 items-center gap-2 text-xs text-[#625d54]">
                          <input
                            type="checkbox"
                            checked={draft.is_active}
                            disabled={user.id === currentUser.user_id}
                            onChange={(event) =>
                              setDrafts((old) => ({
                                ...old,
                                [user.id]: { ...draft, is_active: event.target.checked },
                              }))
                            }
                            className="size-4 accent-[#b96f4b] disabled:cursor-not-allowed"
                          />
                          账号启用
                        </label>
                        <button
                          disabled={busy || !draft.username.trim()}
                          onClick={() => void saveUser(user)}
                          className="h-10 rounded-lg border border-[#d9c9b3] px-4 text-xs font-medium text-[#655747] hover:bg-[#f4eee2] focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:opacity-50"
                        >
                          保存设置
                        </button>
                        <button
                          disabled={busy}
                          onClick={() => void resetPassword(user)}
                          className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-[#e3ddd2] px-3 text-xs text-[#625d54] hover:bg-[#f1eee7] focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:opacity-50"
                        >
                          <KeyRoundIcon className="size-3.5" aria-hidden="true" />
                          重置密码
                        </button>
                      </div>
                      {user.id === currentUser.user_id && (
                        <p className="mt-2 text-xs text-[#89847a]">
                          当前登录账号不能取消自己的管理员权限或停用自己。
                        </p>
                      )}
                      <div className="mt-4 border-t border-[#eee9e0] pt-4">
                        <div className="mb-4 rounded-xl border border-[#e7e2d8] bg-white/70 p-4">
                          <p className="text-xs font-semibold text-[#514b42]">数据源角色绑定</p>
                          {user.role === "admin" && (
                            <p className="mt-1 text-xs leading-5 text-[#77736b]">
                              {draft.role === "member"
                                ? "先选择数据源角色，再点击上方“保存设置”；取消管理员权限与角色绑定会同时生效。"
                                : "此账号拥有管理员权限，可访问所有启用的数据源。若要按角色限制访问，请先取消上方“管理员权限”并保存设置。"}
                            </p>
                          )}
                          <div className="mt-3 flex flex-wrap gap-2">
                            {roles.map((item) => (
                              <label
                                key={item.id}
                                className={`inline-flex items-center gap-2 rounded-lg border border-[#e3ddd2] px-3 py-2 text-xs ${draft.role === "admin" ? "cursor-not-allowed opacity-50" : "cursor-pointer hover:bg-[#f4eee2]"}`}
                              >
                                <input
                                  type="checkbox"
                                  checked={(user.role === "admin"
                                    ? draft.role_ids
                                    : (user.role_ids ?? [])
                                  ).includes(item.id)}
                                  disabled={busy || draft.role === "admin"}
                                  onChange={() => {
                                    if (user.role === "admin") {
                                      setDrafts((old) => ({
                                        ...old,
                                        [user.id]: {
                                          ...draft,
                                          role_ids: draft.role_ids.includes(item.id)
                                            ? draft.role_ids.filter((id) => id !== item.id)
                                            : [...draft.role_ids, item.id],
                                        },
                                      }));
                                    } else {
                                      void toggleUserRole(user, item.id);
                                    }
                                  }}
                                  className="size-4 accent-[#b96f4b]"
                                />
                                {item.name}
                              </label>
                            ))}
                            {roles.length === 0 && (
                              <button
                                type="button"
                                onClick={() => selectTab("roles")}
                                className="text-xs text-[#9c6046] underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:ring-[#c57650]"
                              >
                                暂无数据源角色，前往创建
                              </button>
                            )}
                          </div>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="mr-1 text-[11px] text-[#89847a]">直接授权</span>
                          {user.data_source_ids.map((sourceId) => {
                            const source = sourceById.get(sourceId);
                            return (
                              <span
                                key={sourceId}
                                className="inline-flex items-center gap-1 rounded-full bg-[#eee8dc] px-2.5 py-1 text-[11px] text-[#73573f]"
                              >
                                {source?.display_name ?? sourceId}
                                <button
                                  type="button"
                                  aria-label={`撤销 ${source?.display_name ?? sourceId} 权限`}
                                  disabled={busy}
                                  onClick={() => void updateGrant(user, sourceId, false)}
                                  className="ml-1 rounded-full text-[#9c6046] hover:text-[#7a392c] focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:opacity-50"
                                >
                                  ×
                                </button>
                              </span>
                            );
                          })}
                          {user.role === "member" && grantOptions.length > 0 && (
                            <div className="flex items-center gap-2">
                              <label className="sr-only" htmlFor={`grant-${user.id}`}>
                                选择要分配的数据源
                              </label>
                              <ComposerSelect
                                id={`grant-${user.id}`}
                                ariaLabel="选择要分配的数据源"
                                value={selectedGrants[user.id] ?? ""}
                                placeholder="分配数据源…"
                                options={[
                                  { value: "", label: "分配数据源…" },
                                  ...grantOptions.map((source) => ({
                                    value: source.id,
                                    label: source.display_name,
                                  })),
                                ]}
                                onValueChange={(value) =>
                                  setSelectedGrants((old) => ({ ...old, [user.id]: value }))
                                }
                                triggerClassName="h-8 rounded-lg border border-[#e3ddd2] bg-white px-2 text-[11px] text-[#625d54] focus-visible:ring-[#c57650]"
                              />
                              <button
                                type="button"
                                disabled={busy || !selectedGrants[user.id]}
                                onClick={() => {
                                  const sourceId = selectedGrants[user.id];
                                  if (sourceId)
                                    void updateGrant(user, sourceId, true).then((changed) => {
                                      if (changed)
                                        setSelectedGrants((old) => ({ ...old, [user.id]: "" }));
                                    });
                                }}
                                className="h-8 rounded-lg px-2.5 text-[11px] text-[#9c6046] hover:bg-[#f1eee7] focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:opacity-40"
                              >
                                添加
                              </button>
                            </div>
                          )}
                          {user.role === "admin" && (
                            <span className="text-[11px] text-[#89847a]">
                              管理员可访问所有启用数据源
                            </span>
                          )}
                          {user.role === "member" &&
                            grantOptions.length === 0 &&
                            sources.length === 0 && (
                              <span className="text-[11px] text-[#89847a]">
                                暂无已启用且配置就绪的数据源
                              </span>
                            )}
                        </div>
                        {user.role === "member" &&
                          (user.effective_data_sources?.length ?? 0) > 0 && (
                            <div className="mt-3 text-[11px] text-[#77736b]">
                              有效授权：
                              {user.effective_data_sources
                                ?.map(
                                  (entry) =>
                                    `${sourceById.get(entry.data_source_id)?.display_name ?? entry.data_source_id}（${entry.source_ids.map((id) => (id === "direct" ? "直接授权" : (roles.find((item) => item.id === id)?.name ?? id))).join("、")}；${entry.available ? "当前可用" : "当前不可用"}）`,
                                )
                                .join("、")}
                            </div>
                          )}
                        <div className="mt-3 flex justify-end">
                          <button
                            type="button"
                            onClick={() => {
                              setPermissionUserId(user.id);
                              setPermissionRequestKey((key) => key + 1);
                              selectTab("effective");
                            }}
                            className="rounded-lg border border-[#d9c9b3] px-3 py-2 text-[11px] font-medium text-[#655747] hover:bg-[#f4eee2] focus-visible:ring-2 focus-visible:ring-[#c57650]"
                          >
                            查看完整权限
                          </button>
                        </div>
                        <p className="mt-3 text-[10px] text-[#aaa397]">
                          {user.must_change_password
                            ? "下次登录需要先修改临时密码"
                            : "已完成首次密码设置"}
                          {user.last_login_at
                            ? ` · 最近登录 ${new Date(user.last_login_at).toLocaleString()}`
                            : " · 尚未登录"}
                        </p>
                      </div>
                    </article>
                  );
                })}
                {users.length === 0 && (
                  <div className="rounded-2xl border border-dashed border-[#d9d1c4] px-6 py-12 text-center text-sm text-[#89847a]">
                    <UserRoundIcon className="mx-auto mb-3 size-5" aria-hidden="true" />
                    还没有账号。
                  </div>
                )}
              </div>
            </section>
          </div>
          <div
            id="user-management-panel-roles"
            role="tabpanel"
            aria-labelledby="user-management-tab-roles"
            hidden={activeTab !== "roles"}
          >
            <DataSourceRoleManagement
              roles={roles}
              users={users}
              sources={sources}
              onToggleBinding={toggleUserRole}
              onChanged={async () => {
                await loadRoles();
                await loadUsers();
              }}
            />
          </div>
          <div
            id="user-management-panel-effective"
            role="tabpanel"
            aria-labelledby="user-management-tab-effective"
            hidden={activeTab !== "effective"}
          >
            <EffectivePermissionsView
              active={activeTab === "effective"}
              users={users}
              sources={permissionSources}
              initialUserId={permissionUserId}
              requestKey={permissionRequestKey}
              onNavigateToUser={(userId) => {
                setFocusUserId(userId);
                selectTab("accounts");
              }}
              onNavigateToRole={(roleId) => {
                setFocusRoleId(roleId);
                selectTab("roles");
              }}
            />
          </div>
        </div>
        <ConfirmDialog
          open={confirmation !== null}
          title={confirmation?.title ?? ""}
          description={confirmation?.description ?? ""}
          confirmLabel={confirmation?.confirmLabel}
          confirmVariant={confirmation?.confirmVariant}
          onConfirm={() => finishConfirmation(true)}
          onCancel={() => finishConfirmation(false)}
        />
      </main>
    </>
  );
}
