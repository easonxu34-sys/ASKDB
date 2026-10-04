"use client";

import {
  AssistantRuntimeProvider,
  useAui,
  useAuiState,
  useLocalRuntime,
  useRemoteThreadListRuntime,
} from "@assistant-ui/react";
import { MenuIcon, SparklesIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Thread } from "@/components/assistant-ui/elements/thread.aui";
import { ThreadListSidebar } from "@/components/assistant-ui/elements/thread-list-sidebar.aui";
import { fetchCurrentUser, type AuthUser, authMutation } from "@/lib/auth-api";
import { createAgentChatAdapter } from "@/lib/agent-chat-adapter";
import { clearLocalThreadCache, createLocalThreadListAdapter } from "@/lib/local-thread-adapter";

export const Assistant = () => {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    fetchCurrentUser()
      .then((current) => {
        if (!active) return;
        if (!current) {
          router.replace("/login");
          return;
        }
        if (current.must_change_password) {
          router.replace("/change-password");
          return;
        }
        setUser(current);
      })
      .catch(() => {
        if (active) setError("暂时无法确认登录状态，请刷新页面重试。");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [router]);

  async function logout() {
    try {
      const response = await authMutation("/api/auth/logout", "POST");
      if (!response.ok) {
        setError("退出登录失败，请稍后重试。");
        return;
      }
      if (user) clearLocalThreadCache(user.user_id);
      setUser(null);
      router.replace("/login");
    } catch {
      setError("退出登录失败，请稍后重试。");
    }
  }

  if (!user) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-[#f7f5f0] px-6 text-sm text-[#77736b]">
        {error ? (
          <div role="alert" className="max-w-sm rounded-2xl border border-[#e7e2d8] bg-[#fbfaf7] p-6 text-center">
            <p>{error}</p>
            <button
              className="mt-4 rounded-lg bg-[#c57650] px-4 py-2 text-white focus-visible:ring-2 focus-visible:ring-[#30302e]"
              onClick={() => window.location.reload()}
            >
              重试
            </button>
          </div>
        ) : (
          <p role="status">{loading ? "正在确认登录状态…" : "正在跳转…"}</p>
        )}
      </div>
    );
  }
  return <SignedInAssistant key={user.user_id} user={user} onLogout={() => void logout()} />;
};

function activeThreadStorageKey(userId: string) {
  return `askdb:user:${encodeURIComponent(userId)}:chat:active-thread`;
}

function ActiveThreadSession({ userId }: { userId: string }) {
  const aui = useAui();
  const threadItemId = useAuiState((state) => state.optional.threadListItem?.id);
  const remoteId = useAuiState((state) => state.optional.threadListItem?.remoteId);
  const [ready, setReady] = useState(false);
  const previousItemId = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    const restore = async () => {
      await aui.threads.getLoadThreadsPromise();
      let savedThreadId: string | null = null;
      try {
        const value = window.sessionStorage.getItem(activeThreadStorageKey(userId));
        if (value && /^[a-f0-9]{32}$/.test(value)) savedThreadId = value;
      } catch {
        // The current conversation remains usable when session storage is unavailable.
      }

      if (savedThreadId) {
        const savedThreadIsAvailable = Object.values(
          aui.threads.getState().threadItems,
        ).some((thread) => thread.remoteId === savedThreadId);
        if (savedThreadIsAvailable) {
          try {
            await aui.threads.switchToThread(savedThreadId);
          } catch {
            try {
              window.sessionStorage.removeItem(activeThreadStorageKey(userId));
            } catch {
              // Ignore unavailable session storage.
            }
          }
        } else {
          try {
            window.sessionStorage.removeItem(activeThreadStorageKey(userId));
          } catch {
            // Ignore unavailable session storage.
          }
        }
      }
      if (!active) return;
      previousItemId.current = aui.threadListItem.getState().id;
      setReady(true);
    };
    void restore();
    return () => {
      active = false;
    };
  }, [aui, userId]);

  useEffect(() => {
    if (!ready) return;
    const key = activeThreadStorageKey(userId);
    try {
      if (remoteId) {
        window.sessionStorage.setItem(key, remoteId);
      } else if (
        threadItemId &&
        previousItemId.current &&
        threadItemId !== previousItemId.current
      ) {
        window.sessionStorage.removeItem(key);
      }
    } catch {
      // The current conversation remains usable when session storage is unavailable.
    }
    previousItemId.current = threadItemId ?? null;
  }, [ready, remoteId, threadItemId, userId]);

  return null;
}

function SignedInAssistant({ user, onLogout }: { user: AuthUser; onLogout: () => void }) {
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const chatAdapter = useMemo(() => createAgentChatAdapter(user.user_id), [user.user_id]);
  const threadAdapter = useMemo(() => createLocalThreadListAdapter(user.user_id), [user.user_id]);
  const runtime = useRemoteThreadListRuntime({
    runtimeHook: () => useLocalRuntime(chatAdapter),
    adapter: threadAdapter,
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ActiveThreadSession userId={user.user_id} />
      <div className="relative flex h-dvh overflow-hidden bg-[#f7f5f0] text-[#30302e]">
        {mobileSidebarOpen && (
          <button
            type="button"
            aria-label="关闭会话菜单"
            className="absolute inset-0 z-20 bg-[#292722]/25 backdrop-blur-[1px] md:hidden"
            onClick={() => setMobileSidebarOpen(false)}
          />
        )}
        <ThreadListSidebar
          user={user}
          mobileOpen={mobileSidebarOpen}
          onNavigate={() => setMobileSidebarOpen(false)}
          onLogout={onLogout}
        />
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-14 shrink-0 items-center justify-between px-4 sm:px-8">
            <div className="flex items-center gap-3">
              <button
                type="button"
                aria-label="打开会话菜单"
                aria-expanded={mobileSidebarOpen}
                className="flex size-9 items-center justify-center rounded-xl text-[#625d54] transition-colors hover:bg-[#ece8df] focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none md:hidden"
                onClick={() => setMobileSidebarOpen(true)}
              >
                <MenuIcon className="size-4" aria-hidden="true" />
              </button>
              <div className="flex items-center gap-2 rounded-full border border-[#e7e2d8] bg-[#fbfaf7] px-3 py-1.5 text-xs text-[#77736b]">
                <SparklesIcon className="size-3.5 text-[#c57650]" aria-hidden="true" />
                AskDB 智能助手
              </div>
            </div>
          </header>
          <main className="min-h-0 flex-1">
            <Thread user={user} />
          </main>
        </div>
      </div>
    </AssistantRuntimeProvider>
  );
}
