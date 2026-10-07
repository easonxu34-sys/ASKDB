"use client";

import { CheckIcon, CopyIcon, KeyRoundIcon, LoaderCircleIcon, PlusIcon, ShieldCheckIcon, UserRoundIcon, UsersIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { authMutation, fetchCurrentUser, responseError, type AdminUser, type AuthUser } from "@/lib/auth-api";
import { SettingsPageHeader } from "@/components/settings/settings-page-header";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ComposerSelect } from "@/components/ui/composer-select";
import {
  fetchDataSourceCatalog,
  isChatAvailableDataSource,
  type DataSourceSummary,
} from "@/lib/data-sources";

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
  return typeof user.id === "string" && typeof user.username === "string" &&
    (user.role === "admin" || user.role === "member") && typeof user.is_active === "boolean" &&
    typeof user.must_change_password === "boolean" && Array.isArray(user.data_source_ids) &&
    user.data_source_ids.every((id) => typeof id === "string");
}

export function UserManagement() {
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [sources, setSources] = useState<DataSourceSummary[]>([]);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState<"admin" | "member">("member");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [oneTimePassword, setOneTimePassword] = useState<OneTimePassword | null>(null);
  const [copied, setCopied] = useState(false);
  const [selectedGrants, setSelectedGrants] = useState<Record<string, string>>({});
  const [drafts, setDrafts] = useState<Record<string, Pick<AdminUser, "username" | "role" | "is_active">>>({});
  const [confirmation, setConfirmation] = useState<ConfirmationRequest | null>(null);
  const confirmationResolverRef = useRef<((confirmed: boolean) => void) | null>(null);

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

  async function loadUsers() {
    const response = await fetch("/api/admin/users", { cache: "no-store" });
    if (!response.ok) throw new Error(await responseError(response, "读取账号列表失败。"));
    const payload: unknown = await response.json();
    if (!payload || typeof payload !== "object" || !("users" in payload) ||
      !Array.isArray(payload.users) || !payload.users.every(isAdminUser)) {
      throw new Error("账号列表响应无效。");
    }
    setUsers(payload.users);
    setDrafts(Object.fromEntries(payload.users.map((user) => [user.id, {
      username: user.username,
      role: user.role,
      is_active: user.is_active,
    }])));
  }

  useEffect(() => {
    let active = true;
    fetchCurrentUser()
      .then(async (user) => {
        if (!active) return;
        setCurrentUser(user);
        if (user?.role === "admin") {
          await loadUsers();
          try {
            const catalog = await fetchDataSourceCatalog();
            if (active) setSources(catalog.data_sources.filter(isChatAvailableDataSource));
          } catch {
            if (active) setNotice("暂时无法读取可用数据源；账号管理仍可继续。");
          }
        }
      })
      .catch((cause) => {
        if (active) setError(cause instanceof Error ? cause.message : "读取管理信息失败。");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const sourceById = useMemo(() => new Map(sources.map((source) => [source.id, source])), [sources]);

  async function createUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !username.trim()) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await authMutation("/api/admin/users", "POST", { username: username.trim(), role });
      if (!response.ok) throw new Error(await responseError(response, "创建账号失败。"));
      const payload = await response.json() as { user?: AdminUser; temporary_password?: string };
      if (!payload.user || !isAdminUser(payload.user) || typeof payload.temporary_password !== "string") {
        throw new Error("账号已创建，但临时密码响应无效。请通过重置密码重新生成。");
      }
      setOneTimePassword({ username: payload.user.username, password: payload.temporary_password });
      setCopied(false);
      setUsername("");
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
    if ((draft.role !== user.role || draft.is_active !== user.is_active) &&
      !await requestConfirmation({
        title: "确认保存账号设置？",
        description: "角色或账号状态变更会立即撤销该账号的现有登录会话。",
        confirmLabel: "保存设置",
      })) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await authMutation(`/api/admin/users/${encodeURIComponent(user.id)}`, "PATCH", draft);
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
    if (!await requestConfirmation({
      title: "确认重置密码？",
      description: `重置 ${user.username} 的密码会撤销其所有登录会话，并要求下次登录修改临时密码。`,
      confirmLabel: "重置密码",
      confirmVariant: "destructive",
    })) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const response = await authMutation(`/api/admin/users/${encodeURIComponent(user.id)}/reset-password`, "POST");
      if (!response.ok) throw new Error(await responseError(response, "重置密码失败。"));
      const payload = await response.json() as { temporary_password?: unknown };
      if (typeof payload.temporary_password !== "string") throw new Error("重置成功，但临时密码响应无效。");
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
    if (!await requestConfirmation({
      title: `确认${grant ? "分配" : "撤销"}数据源权限？`,
      description: `${grant ? "向" : "从"} ${user.username} ${grant ? "分配" : "撤销"}数据源“${sourceName}”的访问权限。`,
      confirmLabel: grant ? "分配权限" : "撤销权限",
      confirmVariant: grant ? "default" : "destructive",
    })) return false;
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

  if (loading) {
    return (
      <>
        <SettingsPageHeader
          title="用户与权限"
          description="管理用户账号和可访问的数据源"
          icon={UsersIcon}
        />
        <main className="flex min-h-[calc(100dvh-4rem)] items-center justify-center bg-[#f7f5f0] text-sm text-[#77736b]" role="status">
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
          <Link className="mt-5 inline-flex rounded-lg bg-[#b96f4b] px-4 py-2 text-sm text-white focus-visible:ring-2 focus-visible:ring-[#30302e]" href="/">返回对话</Link>
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
        rightSlot={<span className="hidden rounded-full border border-[#e7e2d8] bg-[#fbfaf7] px-3 py-1.5 text-xs text-[#77736b] sm:inline-flex">管理员 · {currentUser.username}</span>}
      />
      <main className="min-h-[calc(100dvh-4rem)] bg-[#f7f5f0] px-4 py-7 text-[#30302e] sm:px-8 sm:py-10">
        <div className="mx-auto max-w-6xl">
        {error && <p role="alert" className="mt-6 rounded-xl border border-[#e7c6bd] bg-[#f8e9e4] px-4 py-3 text-sm text-[#9c4037]">{error}</p>}
        {notice && <p role="status" className="mt-6 rounded-xl border border-[#d7e0d0] bg-[#eff3eb] px-4 py-3 text-sm text-[#54734d]">{notice}</p>}
        {oneTimePassword && (
          <section className="mt-4 rounded-2xl border border-[#d8c6a9] bg-[#f4eee2] p-5 sm:flex sm:items-center sm:justify-between sm:gap-6">
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-sm font-semibold text-[#514b42]"><KeyRoundIcon className="size-4 text-[#b96f4b]" aria-hidden="true" />一次性临时密码 · {oneTimePassword.username}</div>
              <p className="mt-2 break-all font-mono text-sm tracking-wide text-[#35332e]">{oneTimePassword.password}</p>
              <p className="mt-2 text-xs text-[#77736b]">此密码只显示这一次；首次登录时系统会要求修改。</p>
            </div>
            <div className="mt-4 flex shrink-0 gap-2 sm:mt-0">
              <button type="button" onClick={() => void navigator.clipboard.writeText(oneTimePassword.password).then(() => setCopied(true)).catch(() => setError("复制失败，请手动安全保存临时密码。"))} className="inline-flex h-9 items-center gap-2 rounded-lg border border-[#d9c9b3] bg-[#fbfaf7] px-3 text-xs text-[#514b42] hover:bg-white focus-visible:ring-2 focus-visible:ring-[#c57650]">
                {copied ? <CheckIcon className="size-3.5" aria-hidden="true" /> : <CopyIcon className="size-3.5" aria-hidden="true" />}{copied ? "已复制" : "复制密码"}
              </button>
              <button type="button" onClick={() => { setOneTimePassword(null); setCopied(false); }} className="h-9 rounded-lg px-3 text-xs text-[#77736b] hover:bg-[#e8e3d8] focus-visible:ring-2 focus-visible:ring-[#c57650]">关闭</button>
            </div>
          </section>
        )}

        <section className="mt-7 rounded-2xl border border-[#e7e2d8] bg-[#fbfaf7] p-5 sm:p-6">
          <div className="flex items-start gap-3">
            <div className="flex size-8 items-center justify-center rounded-xl bg-[#eee8dc] text-[#9b654b]"><PlusIcon className="size-4" aria-hidden="true" /></div>
            <div><h2 className="text-sm font-semibold">创建账号</h2><p className="mt-1 text-xs leading-5 text-[#89847a]">生成一次性临时密码。新账号首次登录后必须设置个人密码。</p></div>
          </div>
          <form onSubmit={(event) => void createUser(event)} className="mt-5 grid gap-3 sm:grid-cols-[1fr_11rem_auto]">
            <label className="sr-only" htmlFor="new-username">用户名或工号</label>
            <input id="new-username" autoComplete="off" maxLength={128} required value={username} onChange={(event) => setUsername(event.target.value)} placeholder="输入用户名或工号" className="h-11 min-w-0 rounded-xl border border-[#e3ddd2] bg-white px-3.5 text-sm outline-none focus:border-[#c57650] focus:ring-4 focus:ring-[#c57650]/10" />
            <label className="sr-only" htmlFor="new-role">账号角色</label>
            <ComposerSelect
              id="new-role"
              ariaLabel="账号角色"
              value={role}
              placeholder="选择角色"
              options={[
                { value: "member", label: "普通用户" },
                { value: "admin", label: "管理员" },
              ]}
              onValueChange={(value) => setRole(value as "admin" | "member")}
              triggerClassName="h-11 w-full max-w-full rounded-xl border border-[#e3ddd2] bg-white px-3 text-sm text-[#514b42] focus-visible:ring-4 focus-visible:ring-[#c57650]/10"
            />
            <button disabled={busy || !username.trim()} className="inline-flex h-11 items-center justify-center gap-2 rounded-xl bg-[#b96f4b] px-5 text-sm font-medium text-white hover:bg-[#a96040] focus-visible:ring-2 focus-visible:ring-[#30302e] disabled:cursor-wait disabled:opacity-60">
              {busy ? <LoaderCircleIcon className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <PlusIcon className="size-4" aria-hidden="true" />}创建账号
            </button>
          </form>
        </section>

        <section className="mt-7">
          <div className="mb-3 flex items-end justify-between gap-3">
            <div><h2 className="text-sm font-semibold">现有账号</h2><p className="mt-1 text-xs text-[#89847a]">普通用户只能使用分配给自己的已启用且配置就绪的数据源。</p></div>
            <span className="text-xs text-[#89847a]">{users.length} 个账号</span>
          </div>
          <div className="space-y-3">
            {users.map((user) => {
              const draft = drafts[user.id] ?? { username: user.username, role: user.role, is_active: user.is_active };
              const grantOptions = sources.filter((source) => !user.data_source_ids.includes(source.id));
              return (
                <article key={user.id} className="rounded-2xl border border-[#e7e2d8] bg-[#fbfaf7] p-5 sm:p-6">
                  <div className="grid gap-4 lg:grid-cols-[minmax(12rem,1fr)_10rem_8rem_auto_auto] lg:items-end">
                    <label className="block space-y-1.5"><span className="text-[11px] text-[#89847a]">用户名 / 工号</span><input maxLength={128} value={draft.username} onChange={(event) => setDrafts((old) => ({ ...old, [user.id]: { ...draft, username: event.target.value } }))} className="h-10 w-full rounded-lg border border-[#e3ddd2] bg-white px-3 text-sm outline-none focus:border-[#c57650] focus:ring-4 focus:ring-[#c57650]/10" /></label>
                    <label className="block space-y-1.5"><span className="text-[11px] text-[#89847a]">角色</span><ComposerSelect
                      id={`user-role-${user.id}`}
                      ariaLabel={`${user.username} 的角色`}
                      value={draft.role}
                      placeholder="选择角色"
                      options={[
                        { value: "member", label: "普通用户" },
                        { value: "admin", label: "管理员" },
                      ]}
                      onValueChange={(value) => setDrafts((old) => ({ ...old, [user.id]: { ...draft, role: value as "admin" | "member" } }))}
                      triggerClassName="h-10 w-full max-w-full rounded-lg border border-[#e3ddd2] bg-white px-3 text-sm focus-visible:ring-4 focus-visible:ring-[#c57650]/10"
                    /></label>
                    <label className="flex h-10 items-center gap-2 text-xs text-[#625d54]"><input type="checkbox" checked={draft.is_active} onChange={(event) => setDrafts((old) => ({ ...old, [user.id]: { ...draft, is_active: event.target.checked } }))} className="size-4 accent-[#b96f4b]" />账号启用</label>
                    <button disabled={busy || !draft.username.trim()} onClick={() => void saveUser(user)} className="h-10 rounded-lg border border-[#d9c9b3] px-4 text-xs font-medium text-[#655747] hover:bg-[#f4eee2] focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:opacity-50">保存设置</button>
                    <button disabled={busy} onClick={() => void resetPassword(user)} className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-[#e3ddd2] px-3 text-xs text-[#625d54] hover:bg-[#f1eee7] focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:opacity-50"><KeyRoundIcon className="size-3.5" aria-hidden="true" />重置密码</button>
                  </div>
                  <div className="mt-4 border-t border-[#eee9e0] pt-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="mr-1 text-[11px] text-[#89847a]">可用数据源</span>
                      {user.data_source_ids.map((sourceId) => {
                        const source = sourceById.get(sourceId);
                        return <span key={sourceId} className="inline-flex items-center gap-1 rounded-full bg-[#eee8dc] px-2.5 py-1 text-[11px] text-[#73573f]">{source?.display_name ?? sourceId}<button type="button" aria-label={`撤销 ${source?.display_name ?? sourceId} 权限`} disabled={busy} onClick={() => void updateGrant(user, sourceId, false)} className="ml-1 rounded-full text-[#9c6046] hover:text-[#7a392c] focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:opacity-50">×</button></span>;
                      })}
                      {user.role === "member" && grantOptions.length > 0 && (
                        <div className="flex items-center gap-2">
                          <label className="sr-only" htmlFor={`grant-${user.id}`}>选择要分配的数据源</label>
                          <ComposerSelect
                            id={`grant-${user.id}`}
                            ariaLabel="选择要分配的数据源"
                            value={selectedGrants[user.id] ?? ""}
                            placeholder="分配数据源…"
                            options={[
                              { value: "", label: "分配数据源…" },
                              ...grantOptions.map((source) => ({ value: source.id, label: source.display_name })),
                            ]}
                            onValueChange={(value) => setSelectedGrants((old) => ({ ...old, [user.id]: value }))}
                            triggerClassName="h-8 rounded-lg border border-[#e3ddd2] bg-white px-2 text-[11px] text-[#625d54] focus-visible:ring-[#c57650]"
                          />
                          <button type="button" disabled={busy || !selectedGrants[user.id]} onClick={() => { const sourceId = selectedGrants[user.id]; if (sourceId) void updateGrant(user, sourceId, true).then((changed) => { if (changed) setSelectedGrants((old) => ({ ...old, [user.id]: "" })); }); }} className="h-8 rounded-lg px-2.5 text-[11px] text-[#9c6046] hover:bg-[#f1eee7] focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:opacity-40">添加</button>
                        </div>
                      )}
                      {user.role === "admin" && <span className="text-[11px] text-[#89847a]">管理员可访问所有启用数据源</span>}
                      {user.role === "member" && grantOptions.length === 0 && sources.length === 0 && <span className="text-[11px] text-[#89847a]">暂无已启用且配置就绪的数据源</span>}
                    </div>
                    <p className="mt-3 text-[10px] text-[#aaa397]">{user.must_change_password ? "下次登录需要先修改临时密码" : "已完成首次密码设置"}{user.last_login_at ? ` · 最近登录 ${new Date(user.last_login_at).toLocaleString()}` : " · 尚未登录"}</p>
                  </div>
                </article>
              );
            })}
            {users.length === 0 && <div className="rounded-2xl border border-dashed border-[#d9d1c4] px-6 py-12 text-center text-sm text-[#89847a]"><UserRoundIcon className="mx-auto mb-3 size-5" aria-hidden="true" />还没有账号。</div>}
          </div>
        </section>
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
