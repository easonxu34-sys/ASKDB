"use client";

import {
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  DownloadIcon,
  LoaderCircleIcon,
  MinusIcon,
  RefreshCwIcon,
  ShieldAlertIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ComposerSelect } from "@/components/ui/composer-select";
import type { AdminUser } from "@/lib/auth-api";
import type { DataSourceSummary } from "@/lib/data-sources";
import {
  downloadEffectivePermissions,
  fetchEffectivePermissions,
  type EffectivePermissionItem,
  type EffectivePermissionPage,
  type EffectivePermissionQuery,
  type PermissionScope,
  type PermissionStatus,
  type PermissionView,
} from "@/lib/effective-permissions";

type Props = {
  active: boolean;
  users: AdminUser[];
  sources: DataSourceSummary[];
  initialUserId: string | null;
  requestKey: number;
  onNavigateToUser: (userId: string) => void;
  onNavigateToRole: (roleId: string) => void;
};

const STATUS_OPTIONS: Array<{ value: PermissionStatus; label: string }> = [
  { value: "all", label: "全部状态" },
  { value: "usable", label: "当前可使用" },
  { value: "granted_blocked", label: "有授权但不可使用" },
  { value: "ungranted", label: "无授权" },
  { value: "multiple_sources", label: "多重授权" },
];

const BLOCK_REASON_LABELS: Record<string, string> = {
  ACCOUNT_DISABLED: "账号已停用",
  SOURCE_MISSING: "数据源不存在",
  SOURCE_DISABLED: "数据源已停用",
  SOURCE_NOT_READY: "数据源尚未就绪",
  NO_GRANT: "没有授权来源",
};

function readableTime(value: string | null): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间无效" : date.toLocaleString("zh-CN");
}

function sourceLabel(item: EffectivePermissionItem, key: string): string {
  if (key === "direct") return "直接授权";
  if (key === "admin_identity") return "管理员身份";
  const grant = item.grant_sources.find((source) => source.key === key);
  return grant?.type === "role" ? `${grant.role_name ?? "数据源角色"}（${grant.role_id}）` : key;
}

export function EffectivePermissionsView({
  active,
  users,
  sources,
  initialUserId,
  requestKey,
  onNavigateToUser,
  onNavigateToRole,
}: Props) {
  const [view, setView] = useState<PermissionView>("user");
  const [userId, setUserId] = useState(initialUserId ?? "");
  const [sourceId, setSourceId] = useState("");
  const [scope, setScope] = useState<PermissionScope>("granted");
  const [status, setStatus] = useState<PermissionStatus>("all");
  const [page, setPage] = useState<EffectivePermissionPage | null>(null);
  const [currentCursor, setCurrentCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([]);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const requestIdRef = useRef(0);
  const lastAutoRequestKeyRef = useRef(0);

  const load = useCallback(
    async (query: EffectivePermissionQuery, requestedCursor: string | null) => {
      const requestId = ++requestIdRef.current;
      setLoading(true);
      setError("");
      setNotice("");
      try {
        const result = await fetchEffectivePermissions(query);
        if (requestId !== requestIdRef.current) return;
        setPage(result);
        setCurrentCursor(requestedCursor);
      } catch (cause) {
        if (requestId !== requestIdRef.current) return;
        setPage(null);
        setError(cause instanceof Error ? cause.message : "读取有效权限失败，请重试。");
      } finally {
        if (requestId === requestIdRef.current) setLoading(false);
      }
    },
    [],
  );

  const currentQuery = useCallback(
    (cursor: string | null): EffectivePermissionQuery | null => {
      if (view === "user") {
        if (!userId) return null;
        return {
          view,
          user_id: userId,
          scope,
          status,
          limit: 50,
          ...(cursor ? { cursor } : {}),
        };
      }
      if (!sourceId) return null;
      return {
        view,
        data_source_id: sourceId,
        scope,
        status,
        limit: 50,
        ...(cursor ? { cursor } : {}),
      };
    },
    [scope, sourceId, status, userId, view],
  );

  const clearResult = useCallback(() => {
    requestIdRef.current += 1;
    setLoading(false);
    setError("");
    setPage(null);
    setCurrentCursor(null);
    setCursorHistory([]);
    setNotice("");
  }, []);

  function retryQuery() {
    const query = currentQuery(null);
    if (!query) return;
    setCursorHistory([]);
    void load(query, null);
  }

  function nextPage() {
    if (!page?.next_cursor) return;
    const query = currentQuery(page.next_cursor);
    if (!query) return;
    setCursorHistory((history) => [...history, currentCursor]);
    void load(query, page.next_cursor);
  }

  function previousPage() {
    if (cursorHistory.length === 0) return;
    const previousCursor = cursorHistory[cursorHistory.length - 1] ?? null;
    const query = currentQuery(previousCursor);
    if (!query) return;
    setCursorHistory((history) => history.slice(0, -1));
    void load(query, previousCursor);
  }

  useEffect(() => {
    if (!initialUserId) return;
    setView("user");
    setUserId(initialUserId);
    clearResult();
  }, [clearResult, initialUserId]);

  useEffect(() => {
    if (!initialUserId || requestKey <= 0 || requestKey === lastAutoRequestKeyRef.current) return;
    lastAutoRequestKeyRef.current = requestKey;
    setView("user");
    setUserId(initialUserId);
    setScope("granted");
    setStatus("all");
    clearResult();
  }, [clearResult, initialUserId, requestKey]);

  useEffect(() => {
    if (!active) return;
    const query = currentQuery(null);
    if (!query) return;

    const timeoutId = window.setTimeout(() => {
      setCursorHistory([]);
      void load(query, null);
    }, 180);
    return () => window.clearTimeout(timeoutId);
  }, [active, currentQuery, initialUserId, load, requestKey]);

  async function exportCurrentRange() {
    const query = currentQuery(null);
    if (!query) {
      setError(view === "user" ? "请选择账号后再导出。" : "请选择数据源后再导出。");
      return;
    }
    setExporting(true);
    setError("");
    setNotice("");
    try {
      const result = await downloadEffectivePermissions({
        view: query.view,
        ...(query.user_id ? { user_id: query.user_id } : {}),
        ...(query.data_source_id ? { data_source_id: query.data_source_id } : {}),
        scope,
        status,
      });
      setNotice(
        `CSV 已生成：${result.rowCount} 行，快照时间 ${readableTime(result.generatedAt)}，SHA-256 ${result.sha256.slice(0, 12)}…`,
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "导出有效权限失败，请重试。");
    } finally {
      setExporting(false);
    }
  }

  return (
    <section className="mt-6 rounded-2xl border border-[#e7e2d8] bg-white p-4 shadow-sm sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-[#393630]">有效权限视图</h2>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-[#77736b]">
            查看账号与数据源之间的当前授权来源。页面按页实时读取；正式复核请导出带快照时间和摘要的
            CSV。选择目标或调整筛选条件后会自动查询。
          </p>
        </div>
        {page && (
          <button
            type="button"
            onClick={() => {
              const query = currentQuery(currentCursor);
              if (query) void load(query, currentCursor);
            }}
            disabled={loading}
            className="inline-flex h-9 items-center gap-2 rounded-lg border border-[#e3ddd2] px-3 text-xs text-[#625d54] hover:bg-[#f7f5f0] focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:opacity-50"
          >
            <RefreshCwIcon className="size-3.5" aria-hidden="true" />
            刷新
          </button>
        )}
      </div>

      <div className="mt-5 grid gap-4 xl:grid-cols-[minmax(12rem,0.7fr)_minmax(17rem,1.3fr)_minmax(17rem,1.3fr)_minmax(12rem,0.9fr)]">
        <fieldset className="min-w-0">
          <legend className="mb-2 text-[11px] font-medium text-[#77736b]">查看方式</legend>
          <div className="flex h-11 gap-2 rounded-xl border border-[#e3ddd2] bg-[#fbfaf7] p-1">
            {(
              [
                ["user", "按用户"],
                ["source", "按数据源"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={view === value}
                onClick={() => {
                  setView(value);
                  clearResult();
                }}
                className={`flex-1 rounded-lg px-2 text-xs font-medium focus-visible:outline-2 focus-visible:outline-[#c57650] ${view === value ? "bg-white text-[#514b42] shadow-sm" : "text-[#89847a] hover:text-[#514b42]"}`}
              >
                {label}
              </button>
            ))}
          </div>
        </fieldset>

        <div>
          <label
            className="mb-2 block text-[11px] font-medium text-[#77736b]"
            htmlFor="effective-permissions-target"
          >
            {view === "user" ? "目标账号" : "目标数据源"}
          </label>
          <ComposerSelect
            id="effective-permissions-target"
            ariaLabel={view === "user" ? "选择目标账号" : "选择目标数据源"}
            value={view === "user" ? userId : sourceId}
            placeholder={view === "user" ? "选择账号…" : "选择数据源…"}
            options={
              view === "user"
                ? [
                    { value: "", label: "选择账号…" },
                    ...users.map((user) => ({
                      value: user.id,
                      label: `${user.username} · ${user.role === "admin" ? "管理员" : "普通账号"}${user.is_active ? "" : " · 已停用"}`,
                    })),
                  ]
                : [
                    { value: "", label: "选择数据源…" },
                    ...sources.map((source) => ({
                      value: source.id,
                      label: `${source.display_name}${source.enabled ? "" : " · 已停用"}${source.runtime_status === "ready" ? "" : " · 未就绪"}`,
                    })),
                  ]
            }
            onValueChange={(value) => {
              if (view === "user") setUserId(value);
              else setSourceId(value);
              clearResult();
            }}
            triggerClassName="h-11 w-full rounded-xl border border-[#e3ddd2] bg-white px-3 text-sm text-[#514b42] focus-visible:ring-[#c57650]"
          />
        </div>

        <div>
          <label
            className="mb-2 block text-[11px] font-medium text-[#77736b]"
            htmlFor="effective-permissions-status"
          >
            状态筛选
          </label>
          <ComposerSelect
            id="effective-permissions-status"
            ariaLabel="有效权限状态筛选"
            placeholder="全部状态"
            value={status}
            options={STATUS_OPTIONS}
            onValueChange={(value) => {
              setStatus(value as PermissionStatus);
              clearResult();
            }}
            triggerClassName="h-11 w-full rounded-xl border border-[#e3ddd2] bg-white px-3 text-sm text-[#514b42] focus-visible:ring-[#c57650]"
          />
        </div>

        <fieldset>
          <legend className="mb-2 text-[11px] font-medium text-[#77736b]">显示范围</legend>
          <div className="flex h-11 items-center gap-4 rounded-xl border border-[#e3ddd2] bg-[#fbfaf7] px-3">
            {(
              [
                ["granted", "有授权"],
                ["all", "包含无授权"],
              ] as const
            ).map(([value, label]) => (
              <label key={value} className="inline-flex items-center gap-2 text-xs text-[#625d54]">
                <input
                  type="radio"
                  name="effective-permissions-scope"
                  checked={scope === value}
                  onChange={() => {
                    setScope(value);
                    clearResult();
                  }}
                  className="size-3.5 accent-[#b96f4b]"
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void exportCurrentRange()}
          disabled={exporting || loading || !page}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-[#d9c9b3] px-4 text-xs font-medium text-[#655747] hover:bg-[#f4eee2] focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:cursor-wait disabled:opacity-50"
        >
          {exporting ? (
            <LoaderCircleIcon
              className="size-3.5 animate-spin motion-reduce:animate-none"
              aria-hidden="true"
            />
          ) : (
            <DownloadIcon className="size-3.5" aria-hidden="true" />
          )}
          导出当前范围 CSV
        </button>
      </div>

      {error && (
        <div
          role="alert"
          className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#e7c6bd] bg-[#f8e9e4] px-3.5 py-3 text-xs leading-5 text-[#9c4037]"
        >
          <p>{error}</p>
          {!page && currentQuery(null) && (
            <button
              type="button"
              onClick={retryQuery}
              disabled={loading}
              className="rounded-lg border border-[#dba99b] px-3 py-1.5 font-medium text-[#8e3e35] hover:bg-[#f5ddd6] focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:opacity-50"
            >
              重新查询
            </button>
          )}
        </div>
      )}
      {notice && (
        <p
          role="status"
          className="mt-4 rounded-xl border border-[#d7e0d0] bg-[#eff3eb] px-3.5 py-3 text-xs leading-5 text-[#54734d]"
        >
          {notice}
        </p>
      )}
      {loading && (
        <div
          role="status"
          aria-live="polite"
          className="mt-5 space-y-3 rounded-xl bg-[#fbfaf7] p-4"
        >
          <span className="sr-only">正在读取当前权限与授权来源…</span>
          {[0, 1, 2].map((item) => (
            <div
              key={item}
              aria-hidden="true"
              className="flex items-center gap-3 motion-safe:animate-pulse"
            >
              <span className="size-8 rounded-full bg-[#eee9e0]" />
              <span className="h-3 flex-1 rounded bg-[#eee9e0]" />
              <span className="h-6 w-24 rounded-full bg-[#eee9e0]" />
            </div>
          ))}
        </div>
      )}
      {!loading && !page && !error && (
        <p className="mt-5 rounded-xl border border-dashed border-[#d9d1c4] px-4 py-8 text-center text-xs leading-5 text-[#89847a]">
          首次进入不会读取完整权限矩阵。选择账号或数据源后会自动加载对应权限。
        </p>
      )}

      {page && !loading && (
        <div className="mt-5" aria-live="polite">
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            <Summary label="当前可使用" value={page.counts.usable} />
            <Summary label="有授权但不可使用" value={page.counts.granted_blocked} />
            <Summary label="多重授权" value={page.counts.multiple_sources} />
            <Summary label="当前筛选结果" value={page.counts.matching} />
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-[11px] text-[#89847a]">
            <span>
              读取时间：{readableTime(page.read_at)} · 当前范围共 {page.counts.total} 项
            </span>
            <span>
              {page.target.type === "user"
                ? `账号：${page.target.username}（${page.target.id}）`
                : `数据源：${page.target.display_name}（${page.target.id}）`}
            </span>
          </div>
          {page.items.length === 0 ? (
            <p className="mt-3 rounded-xl border border-dashed border-[#d9d1c4] px-4 py-8 text-center text-xs text-[#89847a]">
              当前筛选范围没有匹配结果。
            </p>
          ) : (
            <div className="mt-3 space-y-2">
              {page.items.map((item) => (
                <PermissionCard
                  key={`${item.user.id}:${item.data_source.id}`}
                  item={item}
                  view={view}
                  onNavigateToUser={onNavigateToUser}
                  onNavigateToRole={onNavigateToRole}
                />
              ))}
            </div>
          )}
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <span className="text-[11px] text-[#89847a]">
              第 {cursorHistory.length + 1} 页 · 下一页会重新读取实时权限状态。
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={previousPage}
                disabled={cursorHistory.length === 0 || loading}
                className="inline-flex h-9 items-center gap-1 rounded-lg border border-[#e3ddd2] px-3 text-xs text-[#625d54] hover:bg-[#f7f5f0] focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:opacity-40"
              >
                <ChevronLeftIcon className="size-3.5" aria-hidden="true" />
                上一页
              </button>
              <button
                type="button"
                onClick={nextPage}
                disabled={!page.next_cursor || loading}
                className="inline-flex h-9 items-center gap-1 rounded-lg border border-[#e3ddd2] px-3 text-xs text-[#625d54] hover:bg-[#f7f5f0] focus-visible:ring-2 focus-visible:ring-[#c57650] disabled:opacity-40"
              >
                下一页
                <ChevronRightIcon className="size-3.5" aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function Summary({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-[#eee9e0] bg-[#fbfaf7] px-3.5 py-3">
      <p className="text-[10px] text-[#89847a]">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums text-[#514b42]">{value}</p>
    </div>
  );
}

function PermissionCard({
  item,
  view,
  onNavigateToUser,
  onNavigateToRole,
}: {
  item: EffectivePermissionItem;
  view: PermissionView;
  onNavigateToUser: (userId: string) => void;
  onNavigateToRole: (roleId: string) => void;
}) {
  const heading = view === "user" ? item.data_source.display_name : item.user.username;
  const subheading =
    view === "user"
      ? `${item.user.username} · ${item.data_source.id}`
      : `${item.user.role === "admin" ? "管理员" : "普通账号"} · ${item.user.id}`;
  return (
    <details className="group rounded-xl border border-[#e7e2d8] bg-[#fbfaf7] open:bg-white">
      <summary className="flex cursor-pointer list-none flex-wrap items-center justify-between gap-3 rounded-xl px-3.5 py-3 outline-none focus-visible:ring-2 focus-visible:ring-[#c57650] sm:px-4">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-[#514b42]">{heading}</p>
          <p className="mt-1 truncate text-[10px] text-[#89847a]">{subheading}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span
            className={`rounded-full px-2.5 py-1 text-[10px] font-medium ${item.usable_now ? "bg-[#e8f0e4] text-[#45633e]" : "bg-[#f5ece4] text-[#8a5c3f]"}`}
          >
            {item.usable_now ? (
              <CheckIcon className="mr-1 inline size-3" aria-hidden="true" />
            ) : item.has_grant ? (
              <ShieldAlertIcon className="mr-1 inline size-3" aria-hidden="true" />
            ) : (
              <MinusIcon className="mr-1 inline size-3" aria-hidden="true" />
            )}
            {item.usable_now ? "当前可使用" : item.has_grant ? "有授权但不可使用" : "无授权"}
          </span>
          {item.grant_sources.length > 1 && (
            <span className="rounded-full border border-[#ded6c9] px-2.5 py-1 text-[10px] text-[#77736b]">
              {item.grant_sources.length} 条来源
            </span>
          )}
          <span className="text-[10px] text-[#9a9388] group-open:hidden">查看详情</span>
          <span className="hidden text-[10px] text-[#9a9388] group-open:inline">收起详情</span>
        </div>
      </summary>
      <div className="border-t border-[#eee9e0] px-3.5 py-4 sm:px-4">
        <p className="mb-3 text-xs leading-5 text-[#625d54]">
          {item.user.username}当前{item.usable_now ? "可使用" : "不可使用"}数据源“
          {item.data_source.display_name}”；
          {item.grant_sources.length > 0
            ? `授权来源为${item.grant_sources.map((grant) => sourceLabel(item, grant.key)).join("、")}。`
            : "当前没有授权来源。"}
        </p>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-[11px] font-semibold text-[#514b42]">授权来源</p>
            {item.grant_sources.length === 0 ? (
              <p className="mt-2 text-xs text-[#89847a]">当前没有直授或角色授权来源。</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {item.grant_sources.map((grant) => (
                  <li
                    key={grant.key}
                    className="rounded-lg border border-[#eee9e0] bg-[#fbfaf7] px-3 py-2.5"
                  >
                    <p className="text-xs font-medium text-[#514b42]">
                      {grant.type === "role"
                        ? `${grant.role_name ?? "数据源角色"} · ${grant.role_id}`
                        : grant.type === "direct"
                          ? "直接授权"
                          : "管理员身份"}
                    </p>
                    {grant.type !== "admin_identity" && (
                      <p className="mt-1 text-[10px] leading-5 text-[#77736b]">
                        授予人 {grant.granted_by ?? "未知"} · 授予于{" "}
                        {readableTime(grant.granted_at)}
                        {grant.type === "role" && (
                          <>
                            <br />
                            绑定人 {grant.bound_by ?? "未知"} · 绑定于{" "}
                            {readableTime(grant.bound_at)}
                          </>
                        )}
                      </p>
                    )}
                    {grant.type !== "admin_identity" && (
                      <div className="mt-2 flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => onNavigateToUser(item.user.id)}
                          className="rounded-md border border-[#e3ddd2] px-2 py-1 text-[10px] text-[#655747] hover:bg-white focus-visible:ring-2 focus-visible:ring-[#c57650]"
                        >
                          {grant.type === "direct" ? "调整直授" : "调整角色绑定"}
                        </button>
                        {grant.type === "role" && (
                          <button
                            type="button"
                            onClick={() => onNavigateToRole(grant.role_id)}
                            className="rounded-md border border-[#e3ddd2] px-2 py-1 text-[10px] text-[#655747] hover:bg-white focus-visible:ring-2 focus-visible:ring-[#c57650]"
                          >
                            编辑角色数据源
                          </button>
                        )}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
          {item.block_reasons.length > 0 && (
            <div className="min-w-48 flex-1 rounded-lg border border-[#ecd7c8] bg-[#fcf6f1] px-3 py-2.5">
              <p className="flex items-center gap-1.5 text-[11px] font-medium text-[#835a41]">
                <ShieldAlertIcon className="size-3.5" aria-hidden="true" />
                阻断原因
              </p>
              <ul className="mt-1.5 space-y-1 text-[10px] leading-4 text-[#835a41]">
                {item.block_reasons.map((reason) => (
                  <li key={reason}>{BLOCK_REASON_LABELS[reason] ?? reason}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
        <div className="mt-3 rounded-lg bg-[#f7f5f0] px-3 py-2.5">
          <p className="text-[11px] font-semibold text-[#514b42]">移除单一来源的预判</p>
          {item.removal_impacts.length === 0 ? (
            <p className="mt-1 text-[10px] leading-5 text-[#77736b]">
              管理员身份是权限来源，不能作为单条业务授权移除。
            </p>
          ) : (
            <ul className="mt-1.5 space-y-1.5">
              {item.removal_impacts.map((impact) => (
                <li key={impact.source_key} className="text-[10px] leading-5 text-[#625d54]">
                  移除{sourceLabel(item, impact.source_key)}后，
                  {impact.remaining_sources.length > 0
                    ? `访问仍保留；剩余来源：${impact.remaining_sources.map((key) => sourceLabel(item, key)).join("、")}`
                    : impact.would_lose_access
                      ? "将失去访问权限。"
                      : "将不再有授权来源；当前访问已被账号或数据源状态阻断。"}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </details>
  );
}
