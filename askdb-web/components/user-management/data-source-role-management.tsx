"use client";

import { useState, type FormEvent } from "react";
import { PlusIcon, ShieldCheckIcon } from "lucide-react";
import type { AdminUser } from "@/lib/auth-api";
import type { DataSourceSummary } from "@/lib/data-sources";
import {
  createRole,
  deleteRole,
  fetchPreviewPage,
  rolePreview,
  saveRole,
  type DataSourceRole,
  type RolePreview,
} from "@/lib/data-source-roles";

type Props = {
  roles: DataSourceRole[];
  users: AdminUser[];
  sources: DataSourceSummary[];
  onToggleBinding: (user: AdminUser, roleId: string) => Promise<void>;
  onChanged: () => Promise<void>;
};

const inputClass =
  "h-10 w-full rounded-lg border border-[#e3ddd2] bg-white px-3 text-sm outline-none focus:border-[#c57650] focus:ring-4 focus:ring-[#c57650]/10";
const buttonClass =
  "rounded-lg border border-[#d9c9b3] px-4 py-2 text-xs font-medium text-[#655747] hover:bg-[#f4eee2] focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:opacity-50";

export function DataSourceRoleManagement({
  roles,
  users,
  sources,
  onToggleBinding,
  onChanged,
}: Props) {
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newSources, setNewSources] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftDescription, setDraftDescription] = useState("");
  const [draftSources, setDraftSources] = useState<string[]>([]);
  const [preview, setPreview] = useState<RolePreview | null>(null);
  const [previewAction, setPreviewAction] = useState<"update" | "delete" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const selected = roles.find((role) => role.id === selectedId);

  function selectRole(role: DataSourceRole) {
    setSelectedId(role.id);
    setDraftName(role.name);
    setDraftDescription(role.description);
    setDraftSources(role.data_source_ids);
    setPreview(null);
    setPreviewAction(null);
    setError("");
  }

  function toggleSource(sourceId: string, current: string[], setValue: (value: string[]) => void) {
    setValue(
      current.includes(sourceId) ? current.filter((id) => id !== sourceId) : [...current, sourceId],
    );
    setPreview(null);
    setPreviewAction(null);
  }

  async function addRole(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const created = await createRole(newName.trim(), newDescription, newSources);
      await onChanged();
      selectRole(created);
      setNewName("");
      setNewDescription("");
      setNewSources([]);
      setNotice("数据源角色已创建。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建角色失败。");
    } finally {
      setBusy(false);
    }
  }

  async function prepare(action: "update" | "delete") {
    if (!selected || busy) return;
    setBusy(true);
    setError("");
    try {
      setPreview(
        await rolePreview(
          selected.id,
          action === "delete" ? null : draftSources,
          action === "delete",
        ),
      );
      setPreviewAction(action);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取影响账号失败。");
    } finally {
      setBusy(false);
    }
  }

  async function showPage(page: number) {
    if (!preview || busy) return;
    setBusy(true);
    try {
      setPreview(await fetchPreviewPage(preview.preview_token, page));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取影响账号失败。");
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }

  async function commit() {
    if (!selected || !preview || !previewAction || busy) return;
    setBusy(true);
    setError("");
    try {
      if (previewAction === "delete") {
        await deleteRole(selected.id, preview.preview_token);
        setSelectedId(null);
        setNotice("数据源角色已删除。");
      } else {
        await saveRole(selected.id, {
          name: draftName.trim(),
          description: draftDescription,
          data_source_ids: draftSources,
          preview_token: preview.preview_token,
        });
        setNotice("数据源角色已保存。");
      }
      setPreview(null);
      setPreviewAction(null);
      await onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存角色失败。请重新预览后确认。");
      setPreview(null);
      setPreviewAction(null);
    } finally {
      setBusy(false);
    }
  }

  async function toggleBinding(user: AdminUser, roleId: string) {
    if (busy) return;
    setBusy(true);
    try {
      await onToggleBinding(user, roleId);
    } finally {
      setBusy(false);
    }
  }

  const readySources = sources.filter(
    (source) => source.enabled && source.runtime_status === "ready",
  );
  const eligibleUsers = users.filter((user) => user.role === "member");
  const unavailableSelectedIds = draftSources.filter(
    (id) => !readySources.some((source) => source.id === id),
  );

  return (
    <section className="mt-7" aria-labelledby="data-source-role-heading">
      <div className="mb-3 flex items-center gap-2">
        <ShieldCheckIcon className="size-5 text-[#b96f4b]" aria-hidden="true" />
        <h2 id="data-source-role-heading" className="text-base font-semibold">
          数据源角色
        </h2>
      </div>
      <p className="mb-5 text-xs text-[#77736b]">
        角色只控制非管理员账号能访问哪些数据源。管理员权限在“用户管理”标签页单独管理。
      </p>
      {error && (
        <p
          role="alert"
          className="mb-4 rounded-lg border border-[#e7c6bd] bg-[#f8e9e4] p-3 text-sm text-[#9c4037]"
        >
          {error}
        </p>
      )}
      {notice && (
        <p
          role="status"
          className="mb-4 rounded-lg border border-[#d7e0d0] bg-[#eff3eb] p-3 text-sm text-[#54734d]"
        >
          {notice}
        </p>
      )}
      <form
        onSubmit={(event) => void addRole(event)}
        className="rounded-2xl border border-[#e7e2d8] bg-[#fbfaf7] p-5 sm:p-6"
      >
        <h3 className="mb-4 text-sm font-semibold">创建数据源角色</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-xs">
            角色名称
            <input
              required
              maxLength={128}
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              className={inputClass}
            />
          </label>
          <label className="space-y-1 text-xs">
            说明
            <input
              maxLength={1000}
              value={newDescription}
              onChange={(event) => setNewDescription(event.target.value)}
              className={inputClass}
            />
          </label>
        </div>
        <fieldset className="mt-4">
          <legend className="mb-2 text-xs font-medium">数据源（可留空）</legend>
          <div className="flex flex-wrap gap-2">
            {readySources.map((source) => (
              <label
                key={source.id}
                className="inline-flex items-center gap-2 rounded-lg border border-[#e3ddd2] px-3 py-2 text-xs"
              >
                <input
                  type="checkbox"
                  checked={newSources.includes(source.id)}
                  onChange={() => toggleSource(source.id, newSources, setNewSources)}
                  className="size-4 accent-[#b96f4b]"
                />
                {source.display_name}
              </label>
            ))}
            {readySources.length === 0 && (
              <span className="text-xs text-[#89847a]">
                暂无已启用且就绪的数据源，可先创建空角色。
              </span>
            )}
          </div>
        </fieldset>
        <button
          disabled={busy || !newName.trim()}
          className="mt-4 inline-flex items-center gap-2 rounded-lg bg-[#b96f4b] px-4 py-2.5 text-xs font-medium text-white hover:bg-[#a96040] focus-visible:ring-2 focus-visible:ring-[#30302e] disabled:opacity-50"
        >
          <PlusIcon className="size-4" aria-hidden="true" />
          创建角色
        </button>
      </form>

      <div className="mt-6 grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
        <div className="space-y-2" aria-label="角色列表">
          {roles.map((role) => (
            <button
              type="button"
              id={`data-source-role-${role.id}`}
              key={role.id}
              onClick={() => selectRole(role)}
              className={`w-full rounded-xl border px-4 py-3 text-left text-sm focus-visible:ring-2 focus-visible:ring-[#c57650] ${selectedId === role.id ? "border-[#c57650] bg-[#f4eee2]" : "border-[#e7e2d8] bg-[#fbfaf7] hover:bg-white"}`}
            >
              <span className="block font-medium">{role.name}</span>
              <span className="mt-1 block text-xs text-[#89847a]">
                {role.user_ids.length} 个账号 · {role.data_source_ids.length} 个数据源
              </span>
            </button>
          ))}
          {roles.length === 0 && (
            <p className="rounded-xl border border-dashed border-[#d9d1c4] p-5 text-xs text-[#89847a]">
              暂无数据源角色。
            </p>
          )}
        </div>
        {selected && (
          <div className="rounded-2xl border border-[#e7e2d8] bg-[#fbfaf7] p-5 sm:p-6">
            <h3 className="mb-4 text-sm font-semibold">编辑 {selected.name}</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-xs">
                角色名称
                <input
                  maxLength={128}
                  value={draftName}
                  onChange={(event) => {
                    setDraftName(event.target.value);
                    setPreview(null);
                  }}
                  className={inputClass}
                />
              </label>
              <label className="space-y-1 text-xs">
                说明
                <input
                  maxLength={1000}
                  value={draftDescription}
                  onChange={(event) => {
                    setDraftDescription(event.target.value);
                    setPreview(null);
                  }}
                  className={inputClass}
                />
              </label>
            </div>
            <fieldset className="mt-4">
              <legend className="mb-2 text-xs font-medium">授权数据源</legend>
              <div className="flex flex-wrap gap-2">
                {readySources.map((source) => (
                  <label
                    key={source.id}
                    className="inline-flex items-center gap-2 rounded-lg border border-[#e3ddd2] px-3 py-2 text-xs"
                  >
                    <input
                      type="checkbox"
                      checked={draftSources.includes(source.id)}
                      onChange={() => toggleSource(source.id, draftSources, setDraftSources)}
                      className="size-4 accent-[#b96f4b]"
                    />
                    {source.display_name}
                  </label>
                ))}
                {unavailableSelectedIds.map((id) => (
                  <label
                    key={id}
                    className="inline-flex items-center gap-2 rounded-lg border border-[#e3ddd2] px-3 py-2 text-xs text-[#89847a]"
                  >
                    <input
                      type="checkbox"
                      checked
                      onChange={() => toggleSource(id, draftSources, setDraftSources)}
                      className="size-4 accent-[#b96f4b]"
                    />
                    {id}（当前不可用）
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="mt-5 border-t border-[#eee9e0] pt-5">
              <h4 className="text-xs font-semibold text-[#514b42]">绑定现有账号</h4>
              <p className="mt-1 text-xs text-[#89847a]">更改绑定前会预览账号的数据源权限变化。</p>
              <div className="mt-3 max-h-64 space-y-2 overflow-y-auto">
                {eligibleUsers.map((user) => {
                  const bound = (user.role_ids ?? []).includes(selected.id);
                  return (
                    <div
                      key={user.id}
                      className="flex items-center justify-between gap-3 rounded-lg border border-[#e7e2d8] px-3 py-2"
                    >
                      <div className="min-w-0">
                        <span className="block truncate text-xs font-medium text-[#514b42]">
                          {user.username}
                        </span>
                        <span className="text-[11px] text-[#89847a]">
                          {bound ? "已绑定" : "未绑定"}
                        </span>
                      </div>
                      <button
                        type="button"
                        aria-label={`${bound ? "解除" : "绑定"} ${user.username} 与 ${selected.name} 的角色绑定`}
                        disabled={busy}
                        onClick={() => void toggleBinding(user, selected.id)}
                        className="shrink-0 rounded-lg border border-[#d9c9b3] px-3 py-1.5 text-xs text-[#655747] hover:bg-[#f4eee2] focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:opacity-50"
                      >
                        {bound ? "解除绑定" : "绑定"}
                      </button>
                    </div>
                  );
                })}
                {eligibleUsers.length === 0 && (
                  <p className="rounded-lg border border-dashed border-[#d9d1c4] p-3 text-xs text-[#89847a]">
                    暂无可绑定账号。请先创建非管理员账号，或在“用户管理”中关闭账号的管理员权限。
                  </p>
                )}
              </div>
            </div>
            <div className="mt-5 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy || !draftName.trim()}
                onClick={() => void prepare("update")}
                className={buttonClass}
              >
                预览并保存
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void prepare("delete")}
                className={`${buttonClass} text-[#9c4037]`}
              >
                预览并删除
              </button>
            </div>
            {preview && previewAction && (
              <div className="mt-5 rounded-xl border border-[#d8c6a9] bg-[#f4eee2] p-4">
                <h4 className="text-sm font-semibold">
                  {previewAction === "delete" ? "删除" : "修改"}影响预览 · {preview.total}{" "}
                  个绑定账号
                </h4>
                <p className="mt-1 text-xs text-[#77736b]">
                  确认前请翻阅全部账号；其他来源覆盖时显示“访问权保留”。
                </p>
                <ul className="mt-3 max-h-60 space-y-2 overflow-y-auto text-xs">
                  {preview.users.map((item) => (
                    <li key={item.user_id} className="rounded-lg bg-white p-2">
                      <span className="font-medium">{item.username}</span> ·{" "}
                      {item.lost_data_source_ids.length
                        ? `将失去：${item.lost_data_source_ids.map((id) => sources.find((source) => source.id === id)?.display_name ?? id).join("、")}`
                        : "访问权保留"}
                    </li>
                  ))}
                  {preview.total === 0 && <li>没有绑定账号。</li>}
                </ul>
                <div className="mt-3 flex items-center gap-3 text-xs">
                  <button
                    type="button"
                    disabled={busy || preview.page <= 1}
                    onClick={() => void showPage(preview.page - 1)}
                    className={buttonClass}
                  >
                    上一页
                  </button>
                  <span>
                    第 {preview.page} / {Math.max(1, Math.ceil(preview.total / preview.page_size))}{" "}
                    页
                  </span>
                  <button
                    type="button"
                    disabled={busy || preview.page * preview.page_size >= preview.total}
                    onClick={() => void showPage(preview.page + 1)}
                    className={buttonClass}
                  >
                    下一页
                  </button>
                </div>
                <div className="mt-4 flex gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void commit()}
                    className="rounded-lg bg-[#b96f4b] px-4 py-2 text-xs font-medium text-white focus-visible:ring-2 focus-visible:ring-[#30302e] disabled:opacity-50"
                  >
                    确认{previewAction === "delete" ? "删除" : "保存"}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setPreview(null);
                      setPreviewAction(null);
                    }}
                    className={buttonClass}
                  >
                    取消
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
