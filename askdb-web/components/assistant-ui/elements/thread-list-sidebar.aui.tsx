"use client";

import { ThreadListItemPrimitive, ThreadListPrimitive } from "@assistant-ui/react";
import { useEffect, useState } from "react";
import Link from "next/link";
import { DatabaseIcon, LogOutIcon, MessageSquareIcon, PlusIcon, SearchIcon, Settings2Icon } from "lucide-react";
import { fetchDataSourceCatalog, type DataSourceCatalog } from "@/lib/data-sources";
import {
  getThreadDataSourceId,
  getThreadDataSourceName,
  getThreadExpiresAt,
  isThreadHistoryImportPending,
  migrateLegacyThreadSources,
} from "@/lib/local-thread-adapter";
import type { AuthUser } from "@/lib/auth-api";

type ThreadListSidebarProps = {
  mobileOpen: boolean;
  onNavigate: () => void;
  user: AuthUser;
  onLogout: () => void;
};

export const ThreadListSidebar = ({ mobileOpen, onNavigate, user, onLogout }: ThreadListSidebarProps) => {
  const [search, setSearch] = useState("");
  const [sourceCatalog, setSourceCatalog] = useState<DataSourceCatalog | null>(null);
  const [threadNotice, setThreadNotice] = useState("");
  const [, setSourceRevision] = useState(0);

  useEffect(() => {
    let active = true;
    const loadSources = async () => {
      try {
        const catalog = await fetchDataSourceCatalog();
        if (!active) return;
        const defaultSource = catalog.data_sources.find(
          (source) => source.id === catalog.default_data_source_id,
        );
        if (defaultSource) migrateLegacyThreadSources(user.user_id, defaultSource.id, defaultSource.display_name);
        setSourceCatalog(catalog);
      } catch {
        if (active) setSourceCatalog(null);
      }
    };
    const refreshLocalBindings = () => setSourceRevision((revision) => revision + 1);
    void loadSources();
    window.addEventListener("askdb:data-source-catalog-updated", loadSources);
    window.addEventListener("askdb:thread-data-source-updated", refreshLocalBindings);
    return () => {
      active = false;
      window.removeEventListener("askdb:data-source-catalog-updated", loadSources);
      window.removeEventListener("askdb:thread-data-source-updated", refreshLocalBindings);
    };
  }, [user.user_id]);

  useEffect(() => {
    const showNotice = (event: Event) => {
      const detail = (event as CustomEvent<unknown>).detail;
      if (typeof detail === "string") setThreadNotice(detail);
    };
    window.addEventListener("askdb:thread-import-notice", showNotice);
    window.addEventListener("askdb:thread-delete-notice", showNotice);
    return () => {
      window.removeEventListener("askdb:thread-import-notice", showNotice);
      window.removeEventListener("askdb:thread-delete-notice", showNotice);
    };
  }, []);

  return (
    <aside
      aria-label="会话导航"
      className={`absolute inset-y-0 left-0 z-30 flex w-[min(18rem,calc(100vw-3.5rem))] flex-col border-r border-[#e7e2d8] bg-[#f1eee7] transition-transform duration-200 motion-reduce:transition-none md:static md:z-auto md:w-[17rem] md:translate-x-0 ${
        mobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"
      }`}
    >
      <header className="flex h-16 shrink-0 items-center gap-3 px-5">
        <div className="flex size-9 items-center justify-center rounded-xl bg-[#e8e3d8] text-[#5c554c]">
          <DatabaseIcon className="size-[18px]" aria-hidden="true" />
        </div>
        <div className="min-w-0">
          <p className="text-sm font-semibold tracking-tight text-[#35332e]">AskDB</p>
          <p className="mt-0.5 text-[11px] text-[#89847a]">数据查询助手</p>
        </div>
      </header>

      <div className="px-3 pb-5 pt-2">
        <ThreadListPrimitive.New
          className="group flex h-10 w-full items-center gap-2.5 rounded-xl border border-[#e2d9cb] bg-[#fbfaf7] px-3 text-left text-[13px] font-medium text-[#514b42] shadow-[0_1px_2px_rgba(59,48,35,0.04)] transition-colors hover:border-[#d7c7b3] hover:bg-white focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none"
          onClick={onNavigate}
        >
          <PlusIcon className="size-4 text-[#b76d4b]" aria-hidden="true" />
          新建对话
        </ThreadListPrimitive.New>
      </div>

      <div className="px-5 pb-2 text-[10px] font-medium tracking-[0.13em] text-[#9b9589]">
        最近会话
      </div>
      <label className="mx-3 mb-2 flex h-9 items-center gap-2 rounded-lg border border-[#e7e2d8]/80 bg-[#f7f5f0]/75 px-2.5 text-[#989186] focus-within:border-[#d8cbb9] focus-within:ring-2 focus-within:ring-[#c57650]/20">
        <SearchIcon className="size-3.5 shrink-0" aria-hidden="true" />
        <input
          type="search"
          aria-label="搜索会话"
          placeholder="搜索会话"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="min-w-0 flex-1 bg-transparent text-xs text-[#514b42] outline-none placeholder:text-[#aaa397]"
        />
      </label>
      {threadNotice && (
        <div role="status" className="mx-3 mb-2 rounded-lg border border-[#ead6b6] bg-[#fff8e9] px-3 py-2 text-[11px] leading-5 text-[#765b36]">
          <div className="flex items-start gap-2">
            <p className="min-w-0 flex-1">{threadNotice}</p>
            <button type="button" aria-label="关闭会话提示" onClick={() => setThreadNotice("")}>×</button>
          </div>
        </div>
      )}
      <nav aria-label="最近会话" className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        <ThreadListPrimitive.Root className="flex flex-col gap-1">
          <ThreadListPrimitive.Items>
            {({ threadListItem }) => {
              const normalizedSearch = search.trim().toLocaleLowerCase();
              const title = threadListItem.title?.toLocaleLowerCase() ?? "新对话";
              if (normalizedSearch && !title.includes(normalizedSearch)) return null;
              return (
                <ThreadListItem
                  key={threadListItem.id}
                  user={user}
                  remoteId={threadListItem.remoteId}
                  sourceCatalog={sourceCatalog}
                  onNavigate={onNavigate}
                />
              );
            }}
          </ThreadListPrimitive.Items>
        </ThreadListPrimitive.Root>
      </nav>

      <footer className="shrink-0 border-t border-[#e4dfd5] px-4 py-3">
        <div className="flex items-center justify-between gap-2">
          <Link
            href="/settings"
            aria-label="个人设置"
            title="个人设置"
            onClick={onNavigate}
            className="flex min-w-0 flex-1 items-center gap-2.5 rounded-xl px-2 py-1.5 text-left transition-colors hover:bg-[#e9e5dc] focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none"
          >
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-[#e8e3d8] text-[#82796c]">
              <Settings2Icon className="size-3.5" aria-hidden="true" />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-xs font-medium text-[#514b42]">个人设置</span>
              <span className="block truncate text-[10px] text-[#89847a]">{user.username} · {user.role === "admin" ? "管理员" : "普通用户"}</span>
            </span>
          </Link>
          <button
            type="button"
            aria-label="退出登录"
            title="退出登录"
            className="flex size-9 shrink-0 items-center justify-center rounded-lg text-[#837d73] transition-colors hover:bg-[#e9e5dc] hover:text-[#514b42] focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none"
            onClick={onLogout}
          >
            <LogOutIcon className="size-4" aria-hidden="true" />
          </button>
        </div>
      </footer>
    </aside>
  );
};

const ThreadListItem = ({
  user,
  remoteId,
  sourceCatalog,
  onNavigate,
}: {
  user: AuthUser;
  remoteId?: string;
  sourceCatalog: DataSourceCatalog | null;
  onNavigate: () => void;
}) => {
  const sourceId = remoteId ? getThreadDataSourceId(user.user_id, remoteId) : undefined;
  const snapshotName = remoteId ? getThreadDataSourceName(user.user_id, remoteId) : undefined;
  const source = sourceId
    ? sourceCatalog?.data_sources.find((item) => item.id === sourceId)
    : undefined;
  const sourceName = source?.display_name ?? snapshotName;
  const unavailable = Boolean(
    sourceId && sourceCatalog && (!source || !source.enabled || source.runtime_status !== "ready"),
  );
  const importPending = isThreadHistoryImportPending(user.user_id, remoteId);
  const expiresAt = remoteId ? getThreadExpiresAt(user.user_id, remoteId) : undefined;
  const expiresDate = expiresAt ? new Date(expiresAt) : null;
  const expiryLabel = expiresDate && !Number.isNaN(expiresDate.getTime())
    ? `到期 ${expiresDate.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" })}`
    : null;

  return (
    <ThreadListItemPrimitive.Root>
      <ThreadListItemPrimitive.Trigger
        className="group flex w-full min-w-0 items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-[13px] text-[#69645b] transition-colors hover:bg-[#e9e5dc] hover:text-[#393630] focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none data-[active]:bg-[#e7e1d6] data-[active]:font-medium data-[active]:text-[#393630]"
        onClick={onNavigate}
      >
        <MessageSquareIcon
          className="size-3.5 shrink-0 text-[#a29b8e] group-data-[active]:text-[#b76d4b]"
          aria-hidden="true"
        />
        <span className="min-w-0 flex-1 truncate">
          <ThreadListItemPrimitive.Title fallback="新对话" />
        </span>
        {importPending && (
          <span title="打开会话后自动续传旧记录" className="shrink-0 rounded-full bg-[#fff0d7] px-1.5 py-0.5 text-[9px] font-normal text-[#855b22]">
            导入待续传
          </span>
        )}
        {sourceId && (
          <span
            title={
              unavailable ? `${sourceName ?? "数据源"} · 当前不可用` : (sourceName ?? "数据源")
            }
            className={`max-w-24 shrink-0 truncate rounded-full px-1.5 py-0.5 text-[9px] font-normal tracking-normal ${
              unavailable ? "bg-[#f8e9e4] text-[#9c4037]" : "bg-[#eee8dc] text-[#8c6149]"
            }`}
          >
            {sourceName ?? "数据源不可用"}
          </span>
        )}
        {expiryLabel && expiresDate && (
          <span
            title={`会话将于 ${expiresDate.toLocaleString("zh-CN")} 到期`}
            className="shrink-0 text-[9px] font-normal text-[#9b6651]"
          >
            {expiryLabel}
          </span>
        )}
      </ThreadListItemPrimitive.Trigger>
    </ThreadListItemPrimitive.Root>
  );
};
