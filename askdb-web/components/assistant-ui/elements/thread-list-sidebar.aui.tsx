"use client";

import {
  ThreadListItemMorePrimitive,
  ThreadListItemPrimitive,
  ThreadListPrimitive,
  useAui,
} from "@assistant-ui/react";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  ArchiveIcon,
  ChevronDownIcon,
  DatabaseIcon,
  LogOutIcon,
  MessageSquareIcon,
  MoreHorizontalIcon,
  PinIcon,
  PlusIcon,
  SearchIcon,
  Settings2Icon,
  Trash2Icon,
} from "lucide-react";
import { fetchDataSourceCatalog, type DataSourceCatalog } from "@/lib/data-sources";
import {
  getCachedThread,
  getRecentThreadPageState,
  getThreadDataSourceId,
  getThreadDataSourceName,
  isThreadHistoryImportPending,
  removeDeletedThreadCache,
  requestThreadDeletionFromUI,
  saveServerThreadMetadata,
  setThreadSearchQuery,
} from "@/lib/local-thread-adapter";
import { ConversationTimestamp } from "@/components/assistant-ui/elements/conversation-time";
import type { AuthUser } from "@/lib/auth-api";
import { archiveThread, patchThreadMetadata, ThreadApiError } from "@/lib/thread-api";
import { getThreadGroupKey } from "@/lib/thread-grouping.mjs";
import { ThreadDeleteConfirmationDialog } from "@/components/threads/thread-delete-confirmation-dialog";

type ThreadListSidebarProps = {
  mobileOpen: boolean;
  onNavigate: () => void;
  user: AuthUser;
  onLogout: () => void;
};

type PendingThreadDelete = {
  threadId: string;
  originalId: string;
  resolve?: (deleted: boolean) => void;
};

export const ThreadListSidebar = ({
  mobileOpen,
  onNavigate,
  user,
  onLogout,
}: ThreadListSidebarProps) => {
  const aui = useAui();
  const [search, setSearch] = useState("");
  const [threadPageState, setThreadPageState] = useState(() => ({
    query: search.trim(),
    hasMore: getRecentThreadPageState(user.user_id, search),
  }));
  const [sourceCatalog, setSourceCatalog] = useState<DataSourceCatalog | null>(null);
  const [threadNotice, setThreadNotice] = useState("");
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => new Set());
  const [, setSourceRevision] = useState(0);
  const [deleteTarget, setDeleteTarget] = useState<PendingThreadDelete | null>(null);
  const deleteTargetRef = useRef<PendingThreadDelete | null>(null);

  const showLoadMore = threadPageState.query === search.trim() && threadPageState.hasMore;

  useEffect(() => {
    setThreadPageState({
      query: search.trim(),
      hasMore: getRecentThreadPageState(user.user_id, search),
    });
    const timer = window.setTimeout(() => {
      setThreadSearchQuery(user.user_id, search);
      void aui.threads.reload();
    }, 250);
    return () => window.clearTimeout(timer);
  }, [aui, search, user.user_id]);

  useEffect(() => {
    let active = true;
    const loadSources = async (refreshThreads = false) => {
      try {
        const catalog = await fetchDataSourceCatalog();
        if (!active) return;
        setSourceCatalog(catalog);
        if (refreshThreads) void aui.threads.reload();
      } catch {
        if (active) setSourceCatalog(null);
      }
    };
    const reloadSourcesAndThreads = () => {
      void loadSources(true);
    };
    const reloadThreads = () => {
      void aui.threads.reload();
    };
    const refreshLocalBindings = () => setSourceRevision((revision) => revision + 1);
    void loadSources();
    window.addEventListener("askdb:data-source-catalog-updated", reloadSourcesAndThreads);
    window.addEventListener("askdb:thread-list-refresh-requested", reloadThreads);
    window.addEventListener("askdb:thread-data-source-updated", refreshLocalBindings);
    window.addEventListener("askdb:thread-metadata-updated", refreshLocalBindings);
    return () => {
      active = false;
      window.removeEventListener("askdb:data-source-catalog-updated", reloadSourcesAndThreads);
      window.removeEventListener("askdb:thread-list-refresh-requested", reloadThreads);
      window.removeEventListener("askdb:thread-data-source-updated", refreshLocalBindings);
      window.removeEventListener("askdb:thread-metadata-updated", refreshLocalBindings);
    };
  }, [aui, user.user_id]);

  useEffect(() => {
    const showNotice = (event: Event) => {
      const detail = (event as CustomEvent<unknown>).detail;
      if (typeof detail === "string") setThreadNotice(detail);
    };
    window.addEventListener("askdb:thread-import-notice", showNotice);
    return () => {
      window.removeEventListener("askdb:thread-import-notice", showNotice);
    };
  }, []);

  useEffect(() => {
    const updatePageState = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          userId?: unknown;
          query?: unknown;
          hasMore?: unknown;
        }>
      ).detail;
      if (
        detail?.userId === user.user_id &&
        detail.query === search.trim() &&
        typeof detail.hasMore === "boolean"
      ) {
        setThreadPageState({ query: search.trim(), hasMore: detail.hasMore });
      }
    };
    window.addEventListener("askdb:thread-list-page-state", updatePageState);
    return () => window.removeEventListener("askdb:thread-list-page-state", updatePageState);
  }, [search, user.user_id]);

  useEffect(() => {
    const requestDelete = (event: Event) => {
      const customEvent = event as CustomEvent<{
        userId?: unknown;
        threadId?: unknown;
        originalId?: unknown;
        resolve?: unknown;
      }>;
      const detail = customEvent.detail;
      if (
        detail?.userId !== user.user_id ||
        typeof detail.threadId !== "string" ||
        typeof detail.originalId !== "string" ||
        typeof detail.resolve !== "function"
      )
        return;
      customEvent.preventDefault();
      deleteTargetRef.current?.resolve?.(false);
      const target = {
        threadId: detail.threadId,
        originalId: detail.originalId,
        resolve: detail.resolve as (deleted: boolean) => void,
      };
      deleteTargetRef.current = target;
      setDeleteTarget(target);
    };
    window.addEventListener("askdb:thread-delete-requested", requestDelete);
    return () => {
      window.removeEventListener("askdb:thread-delete-requested", requestDelete);
      deleteTargetRef.current?.resolve?.(false);
      deleteTargetRef.current = null;
    };
  }, [user.user_id]);

  useEffect(() => {
    const showError = (event: Event) => {
      const detail = (event as CustomEvent<unknown>).detail;
      if (typeof detail === "string") setThreadNotice(detail);
    };
    window.addEventListener("askdb:thread-list-error", showError);
    return () => window.removeEventListener("askdb:thread-list-error", showError);
  }, []);

  function finishDelete(deleted: boolean, threadId?: string) {
    const target = deleteTargetRef.current;
    if (deleted && target) {
      removeDeletedThreadCache(user.user_id, threadId ?? target.threadId, target.originalId);
    }
    target?.resolve?.(deleted);
    deleteTargetRef.current = null;
    setDeleteTarget(null);
  }

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
        <div
          role="status"
          className="mx-3 mb-2 rounded-lg border border-[#ead6b6] bg-[#fff8e9] px-3 py-2 text-[11px] leading-5 text-[#765b36]"
        >
          <div className="flex items-start gap-2">
            <p className="min-w-0 flex-1">{threadNotice}</p>
            <button
              type="button"
              onClick={() => void aui.threads.reload()}
              className="shrink-0 underline underline-offset-2"
            >
              刷新
            </button>
            <button type="button" aria-label="关闭会话提示" onClick={() => setThreadNotice("")}>
              ×
            </button>
          </div>
        </div>
      )}
      <nav aria-label="最近会话" className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        <ThreadListPrimitive.Root className="flex flex-col gap-1">
          <ThreadListPrimitive.Items>
            {({ threadListItem }) => {
              const remoteId = threadListItem.remoteId;
              const metadata = remoteId ? getCachedThread(user.user_id, remoteId) : undefined;
              const sourceId = metadata?.dataSourceId ?? "";
              const sourceName = metadata?.dataSourceNameSnapshot?.trim() || "未命名数据源";
              const groupKey = getThreadGroupKey(sourceId, sourceName);
              const custom = (
                threadListItem as typeof threadListItem & {
                  custom?: {
                    showDataSourceHeading?: boolean;
                    showDataSourceDivider?: boolean;
                  };
                }
              ).custom;
              const groupCollapsed = collapsedGroups.has(groupKey);
              const showGroupHeading = custom?.showDataSourceHeading ?? false;
              const showGroupDivider = custom?.showDataSourceDivider ?? false;
              return (
                <ThreadListItem
                  key={threadListItem.id}
                  user={user}
                  remoteId={remoteId}
                  showGroupHeading={showGroupHeading}
                  showGroupDivider={showGroupDivider}
                  groupSourceName={sourceName}
                  groupCollapsed={groupCollapsed}
                  renderThread={!groupCollapsed}
                  onToggleGroup={() => {
                    setCollapsedGroups((groups) => {
                      const next = new Set(groups);
                      if (next.has(groupKey)) next.delete(groupKey);
                      else next.add(groupKey);
                      return next;
                    });
                  }}
                  sourceCatalog={sourceCatalog}
                  onNavigate={onNavigate}
                  onError={setThreadNotice}
                  onReload={() => aui.threads.reload()}
                />
              );
            }}
          </ThreadListPrimitive.Items>
          {showLoadMore && (
            <ThreadListPrimitive.LoadMore className="mx-2 mt-2 rounded-lg px-3 py-2 text-left text-xs text-[#8b7867] hover:bg-[#e9e5dc] focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none">
              加载更多会话
            </ThreadListPrimitive.LoadMore>
          )}
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
              <span className="block truncate text-[10px] text-[#89847a]">
                {user.username} · {user.role === "admin" ? "管理员" : "普通用户"}
              </span>
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
      <ThreadDeleteConfirmationDialog
        open={Boolean(deleteTarget)}
        threadId={deleteTarget?.threadId ?? null}
        onOpenChange={(open) => {
          if (!open) finishDelete(false);
        }}
        onDeleted={(threadId) => finishDelete(true, threadId)}
      />
    </aside>
  );
};

const ThreadListItem = ({
  user,
  remoteId,
  showGroupHeading,
  showGroupDivider,
  groupSourceName,
  groupCollapsed,
  renderThread,
  onToggleGroup,
  sourceCatalog,
  onNavigate,
  onError,
  onReload,
}: {
  user: AuthUser;
  remoteId?: string;
  showGroupHeading: boolean;
  showGroupDivider: boolean;
  groupSourceName: string;
  groupCollapsed: boolean;
  renderThread: boolean;
  onToggleGroup: () => void;
  sourceCatalog: DataSourceCatalog | null;
  onNavigate: () => void;
  onError: (message: string) => void;
  onReload: () => void | Promise<unknown>;
}) => {
  const [busy, setBusy] = useState(false);
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
  const metadata = remoteId ? getCachedThread(user.user_id, remoteId) : undefined;
  const isPinned = metadata?.isPinned === true;

  async function pinThread() {
    if (!remoteId || !metadata?.metadataRevision) return;
    setBusy(true);
    try {
      const updated = await patchThreadMetadata(remoteId, metadata.metadataRevision, {
        is_pinned: !isPinned,
      });
      saveServerThreadMetadata(user.user_id, updated);
      await onReload();
    } catch (error) {
      if (error instanceof ThreadApiError && error.current) {
        saveServerThreadMetadata(user.user_id, error.current);
        onError("会话已在其他窗口修改，已刷新当前信息；请重试操作。");
        await onReload();
      } else {
        onError(error instanceof Error ? error.message : "置顶状态保存失败，请重试。");
      }
    } finally {
      setBusy(false);
    }
  }

  async function archive() {
    if (!remoteId || !metadata?.metadataRevision) return;
    setBusy(true);
    try {
      const updated = await archiveThread(remoteId, metadata.metadataRevision);
      saveServerThreadMetadata(user.user_id, updated);
      await onReload();
    } catch (error) {
      if (error instanceof ThreadApiError && error.current) {
        saveServerThreadMetadata(user.user_id, error.current);
        onError("会话状态已变化，已刷新当前信息；请重试操作。");
        await onReload();
      } else {
        onError(error instanceof Error ? error.message : "归档失败，请重试。");
      }
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!remoteId) return;
    setBusy(true);
    try {
      if (await requestThreadDeletionFromUI(user.user_id, remoteId)) await onReload();
    } catch (error) {
      onError(error instanceof Error ? error.message : "删除会话失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  if (!showGroupHeading && !renderThread) return null;

  return (
    <div>
      {showGroupHeading && (
        <div
          className={`mx-1 ${showGroupDivider ? "mt-2 border-t border-[#e3ded4] pt-2" : "pt-1"}`}
        >
          <button
            type="button"
            aria-expanded={!groupCollapsed}
            aria-label={`${groupSourceName}，${groupCollapsed ? "展开" : "收起"}`}
            onClick={onToggleGroup}
            className="flex min-h-9 w-full items-center gap-2 rounded-lg px-2.5 text-left text-[11px] font-medium text-[#777166] transition-colors hover:bg-[#e9e5dc] focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none"
          >
            <DatabaseIcon className="size-3.5 shrink-0 text-[#9a9183]" aria-hidden="true" />
            <span className="min-w-0 flex-1 truncate">{groupSourceName}</span>
            <ChevronDownIcon
              className={`size-3.5 shrink-0 text-[#938b7f] transition-transform ${groupCollapsed ? "-rotate-90" : ""}`}
              aria-hidden="true"
            />
          </button>
        </div>
      )}
      {renderThread && (
        <ThreadListItemPrimitive.Root className="group flex min-w-0 items-center rounded-lg transition-colors hover:bg-[#e9e5dc] data-[active]:bg-[#e7e1d6]">
          <ThreadListItemPrimitive.Trigger
            className="flex min-w-0 flex-1 items-start gap-2.5 rounded-lg py-2.5 pl-5 pr-2.5 text-left text-[13px] text-[#69645b] hover:text-[#393630] focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none data-[active]:font-medium data-[active]:text-[#393630]"
            onClick={onNavigate}
          >
            <MessageSquareIcon
              className="mt-0.5 size-3.5 shrink-0 text-[#a29b8e] group-data-[active]:text-[#b76d4b]"
              aria-hidden="true"
            />
            <span className="min-w-0 flex-1">
              <span className="block truncate leading-4">
                <ThreadListItemPrimitive.Title fallback="新对话" />
              </span>
              {metadata?.lastUserTurnAt && (
                <ConversationTimestamp
                  value={metadata.lastUserTurnAt}
                  variant="activity"
                  accessibleLabel="最近提问时间"
                  className="mt-1 block text-[10px] font-normal leading-3 text-[#777166] tabular-nums"
                />
              )}
            </span>
            {isPinned && <PinIcon className="size-3 shrink-0 text-[#b76d4b]" aria-label="已置顶" />}
            {importPending && (
              <span
                title="打开会话后自动续传旧记录"
                className="shrink-0 rounded-full bg-[#fff0d7] px-1.5 py-0.5 text-[9px] font-normal text-[#855b22]"
              >
                导入待续传
              </span>
            )}
            {sourceId && unavailable && (
              <span
                title={`${sourceName ?? "数据源"} · 当前不可用`}
                className="shrink-0 rounded-full bg-[#f8e9e4] px-1.5 py-0.5 text-[9px] font-normal text-[#9c4037]"
              >
                不可用
              </span>
            )}
          </ThreadListItemPrimitive.Trigger>
          <ThreadListItemMorePrimitive.Root sharedFocusGroup>
            <ThreadListItemMorePrimitive.Trigger
              aria-label="会话操作"
              title="会话操作"
              className="mr-1 flex size-7 shrink-0 items-center justify-center rounded-md text-[#89847a] opacity-0 transition-opacity hover:bg-[#ddd6ca] hover:text-[#514b42] focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none group-hover:opacity-100 group-has-focus-visible:opacity-100 data-[state=open]:opacity-100"
            >
              <MoreHorizontalIcon className="size-4" aria-hidden="true" />
            </ThreadListItemMorePrimitive.Trigger>
            <ThreadListItemMorePrimitive.Content className="z-50 min-w-40 rounded-xl border border-[#e5ded3] bg-[#fbfaf7] p-1.5 text-[#514b42] shadow-lg">
              <ThreadListItemMorePrimitive.Item
                disabled={busy || !metadata?.metadataRevision}
                onSelect={() => void pinThread()}
                className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-xs outline-none hover:bg-[#eee9e0] focus:bg-[#eee9e0] data-[disabled]:pointer-events-none data-[disabled]:opacity-50"
              >
                <PinIcon
                  className={`size-3.5 ${isPinned ? "text-[#b76d4b]" : "text-[#89847a]"}`}
                  aria-hidden="true"
                />
                {isPinned ? "取消置顶" : "置顶会话"}
              </ThreadListItemMorePrimitive.Item>
              <ThreadListItemMorePrimitive.Item
                disabled={busy || !metadata?.metadataRevision}
                onSelect={() => void archive()}
                className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-xs outline-none hover:bg-[#eee9e0] focus:bg-[#eee9e0] data-[disabled]:pointer-events-none data-[disabled]:opacity-50"
              >
                <ArchiveIcon className="size-3.5 text-[#89847a]" aria-hidden="true" />
                归档会话
              </ThreadListItemMorePrimitive.Item>
              <ThreadListItemMorePrimitive.Separator className="my-1 h-px bg-[#eee8df]" />
              <ThreadListItemMorePrimitive.Item
                disabled={busy}
                onSelect={() => void remove()}
                className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-xs text-[#984b3b] outline-none hover:bg-[#f3e5df] focus:bg-[#f3e5df] data-[disabled]:pointer-events-none data-[disabled]:opacity-50"
              >
                <Trash2Icon className="size-3.5" aria-hidden="true" />
                删除会话
              </ThreadListItemMorePrimitive.Item>
            </ThreadListItemMorePrimitive.Content>
          </ThreadListItemMorePrimitive.Root>
        </ThreadListItemPrimitive.Root>
      )}
    </div>
  );
};
