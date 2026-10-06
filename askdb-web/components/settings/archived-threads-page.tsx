"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ArchiveIcon, Clock3Icon, RotateCcwIcon, SearchIcon, Trash2Icon } from "lucide-react";
import { SettingsPageHeader } from "@/components/settings/settings-page-header";
import { ThreadDeleteConfirmationDialog } from "@/components/threads/thread-delete-confirmation-dialog";
import { fetchCurrentUser } from "@/lib/auth-api";
import { removeDeletedThreadCache, saveServerThreadMetadata } from "@/lib/local-thread-adapter";
import {
  listThreads,
  restoreThread,
  ThreadApiError,
  type ThreadMetadata,
} from "@/lib/thread-api";

function archivedAtLabel(value: string | null) {
  if (!value) return "归档时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "归档时间未知" : date.toLocaleString("zh-CN");
}

export function ArchivedThreadsPage() {
  const [threads, setThreads] = useState<ThreadMetadata[]>([]);
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [deleteThreadId, setDeleteThreadId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [userId, setUserId] = useState<string | null>(null);
  const requestId = useRef(0);

  useEffect(() => {
    let active = true;
    void fetchCurrentUser().then((user) => {
      if (!active) return;
      if (!user) {
        setUserId("");
        setError("登录状态已失效，请重新登录后管理归档会话。");
        return;
      }
      setUserId(user.user_id);
    }).catch(() => {
      if (active) {
        setUserId("");
        setError("登录状态已失效，请重新登录后管理归档会话。");
      }
    });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (userId === null) return;
    if (!userId) {
      setLoading(false);
      return;
    }
    const currentRequest = ++requestId.current;
    let active = true;
    setLoading(true);
    setLoadingMore(false);
    setCursor(null);
    const timer = window.setTimeout(() => {
      void listThreads({ view: "archived", q: query.trim(), limit: 50 })
        .then((page) => {
          if (!active || requestId.current !== currentRequest) return;
          setThreads(page.threads);
          setCursor(page.next_cursor);
          setError("");
        })
        .catch((reason: unknown) => {
          if (!active || requestId.current !== currentRequest) return;
          setThreads([]);
          setCursor(null);
          setError(reason instanceof Error ? reason.message : "归档会话读取失败，请重试。");
        })
        .finally(() => {
          if (active && requestId.current === currentRequest) setLoading(false);
        });
    }, 180);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [query, refreshKey, userId]);

  const groups = useMemo(() => {
    const result: Array<{ id: string; name: string; threads: ThreadMetadata[] }> = [];
    for (const thread of threads) {
      const id = thread.data_source_id;
      let group = result.at(-1);
      if (!group || group.id !== id) {
        group = {
          id,
          name: thread.data_source_name ?? "未命名数据源",
          threads: [],
        };
        result.push(group);
      }
      group.threads.push(thread);
    }
    return result;
  }, [threads]);

  async function loadMore() {
    if (!userId || !cursor || loadingMore) return;
    const currentRequest = requestId.current;
    setLoadingMore(true);
    setError("");
    try {
      const page = await listThreads({ view: "archived", q: query.trim(), limit: 50, cursor });
      if (requestId.current !== currentRequest) return;
      setThreads((current) => [...current, ...page.threads]);
      setCursor(page.next_cursor);
    } catch (reason) {
      if (requestId.current !== currentRequest) return;
      setCursor(null);
      setError(reason instanceof ThreadApiError && reason.code === "THREAD_CURSOR_STALE"
        ? "归档列表已变化，请刷新后继续。"
        : reason instanceof Error ? reason.message : "加载更多归档会话失败，请重试。");
    } finally {
      if (requestId.current === currentRequest) setLoadingMore(false);
    }
  }

  async function restore(item: ThreadMetadata) {
    if (!userId) return;
    setBusyId(item.thread_id);
    setError("");
    try {
      const updated = await restoreThread(item.thread_id, item.metadata_revision);
      saveServerThreadMetadata(userId, updated);
      setRefreshKey((value) => value + 1);
    } catch (reason) {
      if (reason instanceof ThreadApiError && reason.current) {
        saveServerThreadMetadata(userId, reason.current);
        setError("会话状态已在其他窗口变化，请刷新归档列表后重试。");
        setRefreshKey((value) => value + 1);
      } else {
        setError(reason instanceof Error ? reason.message : "恢复会话失败，请重试。");
      }
    } finally {
      setBusyId(null);
    }
  }

  function remove(item: ThreadMetadata) {
    setError("");
    setDeleteThreadId(item.thread_id);
  }

  return (
    <div className="min-h-dvh">
      <SettingsPageHeader
        title="归档会话"
        description="按数据源查看已归档的会话；归档期间保留期限暂停。"
        icon={ArchiveIcon}
      />
      <main className="mx-auto max-w-5xl px-4 py-7 sm:px-8 sm:py-10">
        <label className="flex h-10 max-w-lg items-center gap-2 rounded-xl border border-[#e5ded3] bg-[#fbfaf7] px-3 text-[#989186] focus-within:border-[#d8cbb9] focus-within:ring-2 focus-within:ring-[#c57650]/20">
          <SearchIcon className="size-4 shrink-0" aria-hidden="true" />
          <input
            type="search"
            aria-label="搜索归档会话"
            placeholder="搜索标题或数据源"
            maxLength={120}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="min-w-0 flex-1 bg-transparent text-sm text-[#514b42] outline-none placeholder:text-[#aaa397]"
          />
        </label>

        {error && (
          <div role="alert" className="mt-5 flex items-center justify-between gap-3 rounded-xl border border-[#ead6b6] bg-[#fff8e9] px-4 py-3 text-sm text-[#765b36]">
            <p>{error}</p>
            <button type="button" onClick={() => setRefreshKey((value) => value + 1)} className="shrink-0 underline underline-offset-2">刷新</button>
          </div>
        )}

        {loading ? (
          <div role="status" className="py-16 text-center text-sm text-[#89847a]">正在读取归档会话…</div>
        ) : groups.length === 0 ? (
          <div className="mt-8 rounded-2xl border border-dashed border-[#ded6ca] bg-[#fbfaf7] px-5 py-16 text-center">
            <ArchiveIcon className="mx-auto size-6 text-[#b0a696]" aria-hidden="true" />
            <p className="mt-3 text-sm font-medium text-[#514b42]">没有找到归档会话</p>
            <p className="mt-1 text-xs text-[#89847a]">归档的会话会显示在这里。</p>
          </div>
        ) : (
          <div className="mt-8 space-y-8">
            {groups.map((group) => (
              <section key={group.id} aria-labelledby={`source-${group.id}`}>
                <h2 id={`source-${group.id}`} className="mb-3 flex items-center gap-2 text-xs font-semibold tracking-wide text-[#857b6d]">
                  <span className="size-1.5 rounded-full bg-[#b76d4b]" aria-hidden="true" />
                  {group.name}
                </h2>
                <div className="divide-y divide-[#eee8df] overflow-hidden rounded-2xl border border-[#e7e0d5] bg-[#fbfaf7]">
                  {group.threads.map((item) => (
                    <article key={item.thread_id} className="flex flex-col gap-4 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
                      <div className="min-w-0">
                        <h3 className="truncate text-sm font-medium text-[#393630]">{item.title || "新对话"}</h3>
                        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-[#89847a]">
                          <span className="inline-flex items-center gap-1.5"><Clock3Icon className="size-3" aria-hidden="true" />已于 {archivedAtLabel(item.archived_at)} 归档</span>
                          {item.is_pinned && <span className="text-[#a76445]">已置顶</span>}
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <button
                          type="button"
                          disabled={!userId || busyId === item.thread_id || deleteThreadId === item.thread_id}
                          onClick={() => void restore(item)}
                          className="inline-flex h-9 items-center gap-2 rounded-lg border border-[#dfd2c2] bg-white px-3 text-xs font-medium text-[#755740] transition-colors hover:border-[#c9ad92] hover:bg-[#fbf7f1] focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none disabled:opacity-50"
                        >
                          <RotateCcwIcon className="size-3.5" aria-hidden="true" />恢复
                        </button>
                        <button
                          type="button"
                          disabled={!userId || busyId === item.thread_id || deleteThreadId === item.thread_id}
                          onClick={() => void remove(item)}
                          className="inline-flex h-9 items-center gap-2 rounded-lg px-3 text-xs text-[#8d6255] transition-colors hover:bg-[#f3e5df] hover:text-[#984b3b] focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none disabled:opacity-50"
                        >
                          <Trash2Icon className="size-3.5" aria-hidden="true" />删除
                        </button>
                      </div>
                    </article>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}

        {cursor && !loading && (
          <div className="mt-6 text-center">
            <button
              type="button"
              disabled={loadingMore}
              onClick={() => void loadMore()}
              className="rounded-lg px-4 py-2 text-xs font-medium text-[#8b7867] hover:bg-[#eee9e0] focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none disabled:opacity-50"
            >
              {loadingMore ? "正在加载…" : "加载更多"}
            </button>
          </div>
        )}
      </main>
      <ThreadDeleteConfirmationDialog
        open={Boolean(deleteThreadId)}
        threadId={deleteThreadId}
        onOpenChange={(open) => { if (!open) setDeleteThreadId(null); }}
        onDeleted={(threadId) => {
          if (userId) removeDeletedThreadCache(userId, threadId);
          setDeleteThreadId(null);
          setRefreshKey((value) => value + 1);
        }}
      />
    </div>
  );
}
