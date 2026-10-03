import {
  ComposerAddAttachment,
  ComposerAttachments,
  UserMessageAttachments,
} from "@/components/assistant-ui/elements/attachment.aui";
import { MarkdownText } from "@/components/assistant-ui/elements/markdown-text";
import { ToolFallback } from "@/components/assistant-ui/elements/tool-fallback.aui";
import { TooltipIconButton } from "@/components/assistant-ui/elements/tooltip-icon-button";
import { Button } from "@/components/ui/button";
import { ComposerSelect } from "@/components/ui/composer-select";
import { DataSourceSelector } from "@/components/assistant-ui/elements/data-source-selector";
import { MemorySubmissionDialog, type MemorySubmissionContext } from "@/components/memory/memory-submission-dialog";
import { cn } from "@/lib/utils";
import type { AuthUser } from "@/lib/auth-api";
import { deriveSourceTurnKey } from "@/lib/agent-chat-adapter";
import { getSuccessfulQueryArtifacts } from "@/lib/chat-output";
import { fetchChatModelOptions, type ChatModelCatalog } from "@/lib/model-profiles";
import {
  fetchDataSourceCatalog,
  isChatAvailableDataSource,
  type DataSourceCatalog,
} from "@/lib/data-sources";
import {
  getThreadDataSourceId,
  getThreadResultArtifacts,
  getDraftThreadDataSource,
  getDraftThreadModelProfileId,
  migrateLegacyThreadSources,
  reconcileLocalThreadModelSelection,
  setDraftThreadDataSource,
  setDraftThreadModelProfileId,
  setThreadDataSource,
  setThreadModelProfileId,
} from "@/lib/local-thread-adapter";
import { isSameActiveThread } from "@/lib/model-selection";
import {
  ActionBarMorePrimitive,
  ActionBarPrimitive,
  AuiIf,
  type AssistantState,
  BranchPickerPrimitive,
  ComposerPrimitive,
  ErrorPrimitive,
  MessagePrimitive,
  SuggestionPrimitive,
  ThreadPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  CheckIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CopyIcon,
  DownloadIcon,
  MicIcon,
  MoreHorizontalIcon,
  PencilIcon,
  RefreshCwIcon,
  SquareIcon,
  SparklesIcon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FC,
  type FormEvent,
  type ReactNode,
} from "react";

// Startup exposes a loading placeholder thread; treat it as a new chat so
// the composer mounts centered. Loads after startup keep the docked layout.
const isNewChatView = (s: AssistantState) =>
  s.thread.messages.length === 0 && (!s.thread.isLoading || s.threads.isLoading);

// A switched thread that is still fetching its history: skeleton, not welcome.
const isHistoryLoadingView = (s: AssistantState) =>
  s.thread.messages.length === 0 &&
  s.thread.isLoading &&
  !s.thread.isDisabled &&
  !s.threads.isLoading;

const ThreadHistorySkeleton: FC = () => (
  <div
    data-slot="aui_thread-history-skeleton"
    role="status"
    className="animate-in fade-in fill-mode-both flex flex-col [animation-delay:150ms] [animation-duration:200ms]"
  >
    <span className="sr-only">正在加载对话</span>
    <div className="flex animate-pulse flex-col gap-y-6 motion-reduce:animate-none">
      <div className="bg-muted ml-auto h-9 w-2/5 rounded-xl" />
      <div className="flex flex-col gap-y-2">
        <div className="bg-muted h-4 w-11/12 rounded-md" />
        <div className="bg-muted h-4 w-4/5 rounded-md" />
        <div className="bg-muted h-4 w-3/5 rounded-md" />
      </div>
      <div className="bg-muted ml-auto h-9 w-1/3 rounded-xl" />
      <div className="flex flex-col gap-y-2">
        <div className="bg-muted h-4 w-10/12 rounded-md" />
        <div className="bg-muted h-4 w-2/3 rounded-md" />
      </div>
    </div>
  </div>
);

export const Thread: FC<{ user: AuthUser }> = ({ user }) => {
  const isEmpty = useAuiState(isNewChatView);

  return (
    <ThreadPrimitive.Root
      className="aui-root aui-thread-root bg-background @container flex h-full flex-col font-serif"
      style={{
        ["--thread-max-width" as string]: "46rem",
        ["--composer-bg" as string]: "#fbfaf7",
        ["--composer-radius" as string]: "1.35rem",
        ["--composer-padding" as string]: "10px",
      }}
    >
      <ThreadPrimitive.Viewport
        turnAnchor="top"
        data-slot="aui_thread-viewport"
        className="relative flex flex-1 flex-col overflow-x-auto overflow-y-scroll scroll-smooth"
      >
        <div
          className={cn(
            "mx-auto flex w-full max-w-(--thread-max-width) flex-1 flex-col px-5 pt-4 sm:px-8",
            isEmpty && "justify-center",
          )}
        >
          <AuiIf condition={isNewChatView}>
            <ThreadWelcome />
          </AuiIf>
          <AuiIf condition={isHistoryLoadingView}>
            <ThreadHistorySkeleton />
          </AuiIf>

          <div data-slot="aui_message-group" className="mb-14 flex flex-col gap-y-6 empty:hidden">
            <ThreadPrimitive.Messages>{() => <ThreadMessage user={user} />}</ThreadPrimitive.Messages>
          </div>

          <ThreadPrimitive.ViewportFooter
            className={cn(
              "aui-thread-viewport-footer bg-background flex flex-col gap-4 overflow-visible pb-4 md:pb-6",
              !isEmpty && "sticky bottom-0 mt-auto rounded-t-(--composer-radius)",
            )}
          >
            <ThreadScrollToBottom />
            <Composer user={user} />
            <AuiIf condition={(s) => isNewChatView(s) && s.composer.isEmpty}>
              <ThreadSuggestions />
            </AuiIf>
          </ThreadPrimitive.ViewportFooter>
        </div>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
};

const ThreadMessage: FC<{ user: AuthUser }> = ({ user }) => {
  const role = useAuiState((s) => s.message.role);
  const isEditing = useAuiState((s) => s.message.composer.isEditing);

  if (isEditing) return <EditComposer />;
  if (role === "user") return <UserMessage />;
  return <AssistantMessage user={user} />;
};

const ThreadScrollToBottom: FC = () => {
  return (
    <ThreadPrimitive.ScrollToBottom asChild>
      <TooltipIconButton
        tooltip="滚动到底部"
        variant="outline"
        className="aui-thread-scroll-to-bottom dark:border-border dark:bg-background dark:hover:bg-accent absolute -top-12 z-10 self-center rounded-full p-4 disabled:invisible"
      >
        <ArrowDownIcon />
      </TooltipIconButton>
    </ThreadPrimitive.ScrollToBottom>
  );
};

const ThreadWelcome: FC = () => {
  return (
    <div className="aui-thread-welcome-root mb-10 flex flex-col items-center px-2 text-center">
      <div className="mb-5 flex size-11 items-center justify-center rounded-full bg-[#eee8dc] text-[#c57650]">
        <SparklesIcon className="size-5" aria-hidden="true" />
      </div>
      <p className="aui-thread-welcome-message-inner fade-in slide-in-from-bottom-1 animate-in fill-mode-both text-3xl leading-tight tracking-[-0.035em] text-[#393630] duration-200 sm:text-[2.65rem]">
        你好，我是 AskDB
      </p>
      <p className="mt-3 max-w-md text-sm leading-6 text-[#77736b] sm:text-base">
        用自然语言提问，探索你的数据。
      </p>
      <div className="mt-7 flex flex-wrap justify-center gap-2 font-sans text-xs text-[#77736b]">
        <span className="rounded-full border border-[#e7e2d8] px-3 py-1.5">查询数据</span>
        <span className="rounded-full border border-[#e7e2d8] px-3 py-1.5">分析趋势</span>
        <span className="rounded-full border border-[#e7e2d8] px-3 py-1.5">解释指标</span>
      </div>
    </div>
  );
};

const ThreadSuggestions: FC = () => {
  return (
    <div className="aui-thread-welcome-suggestions flex w-full flex-col">
      <ThreadPrimitive.Suggestions>{() => <ThreadSuggestionItem />}</ThreadPrimitive.Suggestions>
    </div>
  );
};

const ThreadSuggestionItem: FC = () => {
  return (
    <div className="aui-thread-welcome-suggestion-display fade-in slide-in-from-bottom-2 animate-in fill-mode-both duration-200 font-sans">
      <SuggestionPrimitive.Trigger send asChild>
        <button
          type="button"
          className="aui-thread-welcome-suggestion group hover:bg-foreground/[0.03] focus-visible:ring-ring/50 flex w-full items-baseline gap-2.5 rounded-xl px-3 py-2.5 text-start text-sm transition-colors outline-none focus-visible:ring-1 motion-reduce:transition-none"
        >
          <span
            aria-hidden
            className="text-muted-foreground/60 group-hover:text-foreground font-mono text-xs transition-colors motion-reduce:transition-none"
          >
            {">"}
          </span>
          <span className="min-w-0 flex-1 truncate">
            <SuggestionPrimitive.Title className="aui-thread-welcome-suggestion-text-1 text-foreground" />{" "}
            <SuggestionPrimitive.Description className="aui-thread-welcome-suggestion-text-2 text-muted-foreground empty:hidden" />
          </span>
        </button>
      </SuggestionPrimitive.Trigger>
    </div>
  );
};

const Composer: FC<{ user: AuthUser }> = ({ user }) => {
  const aui = useAui();
  const canSend = useAuiState((state) => state.composer.canSend);
  const [dataSourceReady, setDataSourceReady] = useState(false);
  const [threadInitializing, setThreadInitializing] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const submissionInProgress = useRef(false);

  const handleSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (submissionInProgress.current || !canSend || !dataSourceReady) return;

      const initialState = aui.threadListItem.getState();
      if (!initialState.id) return;

      submissionInProgress.current = true;
      setThreadInitializing(true);
      setSubmitError("");
      try {
        if (!initialState.remoteId) await aui.threadListItem.initialize();
        if (!isSameActiveThread(initialState.id, aui.threadListItem.getState().id)) return;
        aui.composer.send();
      } catch {
        if (isSameActiveThread(initialState.id, aui.threadListItem.getState().id)) {
          setSubmitError("无法创建当前会话，请重试。");
        }
      } finally {
        submissionInProgress.current = false;
        setThreadInitializing(false);
      }
    },
    [aui, canSend, dataSourceReady],
  );

  return (
    <ComposerPrimitive.Root
      className="aui-composer-root relative flex w-full flex-col"
      onSubmit={(event) => void handleSubmit(event)}
    >
      <ComposerPrimitive.AttachmentDropzone asChild>
        <div
          data-slot="aui_composer-shell"
          className="border-[#e7e2d8] focus-within:border-[#d8cbb9] data-[dragging=true]:border-ring flex w-full cursor-text flex-col gap-2 rounded-(--composer-radius) border bg-(--composer-bg) p-(--composer-padding) transition-[border-color] data-[dragging=true]:border-dashed data-[dragging=true]:bg-[color-mix(in_oklab,var(--color-accent)_50%,var(--color-background))]"
        >
          <ComposerAttachments />
          <ComposerPrimitive.Input
            placeholder="向 AskDB 提问…"
            className="aui-composer-input placeholder:text-muted-foreground/65 max-h-48 min-h-12 w-full resize-none bg-transparent px-3 py-2 text-[15px] leading-6 outline-none"
            rows={1}
            autoFocus
            aria-label="消息输入框"
          />
          <ComposerAction
            threadInitializing={threadInitializing}
            dataSourceReady={dataSourceReady}
          >
            <ModelProfileSelector
              userId={user.user_id}
              isAdmin={user.role === "admin"}
            />
            <DataSourceSelection
              userId={user.user_id}
              isAdmin={user.role === "admin"}
              onReadinessChange={setDataSourceReady}
            />
          </ComposerAction>
        </div>
      </ComposerPrimitive.AttachmentDropzone>
      {submitError && (
        <p role="alert" className="px-2 pt-1 font-sans text-xs text-[#9c6046]">
          {submitError}
        </p>
      )}
    </ComposerPrimitive.Root>
  );
};

const DataSourceSelection: FC<{
  userId: string;
  isAdmin: boolean;
  onReadinessChange: (ready: boolean) => void;
}> = ({ userId, isAdmin, onReadinessChange }) => {
  const aui = useAui();
  const threadId = useAuiState((state) => state.optional.threadListItem?.remoteId);
  const threadItemId = useAuiState((state) => state.optional.threadListItem?.id);
  const boundSourceId = threadId ? getThreadDataSourceId(userId, threadId) : undefined;
  const hasUserMessages = useAuiState((state) =>
    state.thread.messages.some((message) => message.role === "user"),
  );
  const [catalog, setCatalog] = useState<DataSourceCatalog | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [notice, setNotice] = useState("");
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const nextCatalog = await fetchDataSourceCatalog();
        if (!active) return;
        const defaultSource = nextCatalog.data_sources.find(
          (source) => source.id === nextCatalog.default_data_source_id,
        );
        if (defaultSource) {
          migrateLegacyThreadSources(userId, defaultSource.id, defaultSource.display_name);
        }
        setCatalog(nextCatalog);
        setLoadError(false);
      } catch {
        if (active) setLoadError(true);
      }
    };
    void load();
    window.addEventListener("askdb:data-source-catalog-updated", load);
    return () => {
      active = false;
      window.removeEventListener("askdb:data-source-catalog-updated", load);
    };
  }, [userId]);

  const availableSources = useMemo(
    () => catalog?.data_sources.filter(isChatAvailableDataSource) ?? [],
    [catalog],
  );

  useEffect(() => {
    if (!catalog) return;
    const savedId = threadId
      ? getThreadDataSourceId(userId, threadId)
      : threadItemId
        ? getDraftThreadDataSource(userId, threadItemId)?.id
        : undefined;
    const sourceId = savedId ?? catalog.default_data_source_id ?? "";
    const source = catalog.data_sources.find((item) => item.id === sourceId);
    setSelectedId(sourceId);
    setNotice(
      sourceId && !availableSources.some((item) => item.id === sourceId)
        ? "此会话的数据源当前不可用。"
        : "",
    );
    const ready = Boolean(source && source.enabled && source.runtime_status === "ready");
    if (threadId && source && !savedId) {
      // Legacy conversations without an explicit binding remain on the old default.
      setThreadDataSource(userId, threadId, source.id, source.display_name);
    }

    if (!threadId && threadItemId && source && !hasUserMessages) {
      setDraftThreadDataSource(userId, threadItemId, source.id, source.display_name);
      onReadinessChange(ready);
    } else {
      onReadinessChange(ready);
    }
  }, [
    availableSources,
    catalog,
    hasUserMessages,
    onReadinessChange,
    threadId,
    threadItemId,
    userId,
  ]);

  function changeSource(sourceId: string) {
    const source = availableSources.find((item) => item.id === sourceId);
    if (!source || !threadItemId || (hasUserMessages && boundSourceId)) return;
    const activeState = aui.threadListItem.getState();
    try {
      if (activeState.remoteId) {
        setThreadDataSource(userId, activeState.remoteId, source.id, source.display_name);
      } else {
        setDraftThreadDataSource(userId, activeState.id, source.id, source.display_name);
      }
      setSelectedId(source.id);
      setNotice("");
      onReadinessChange(true);
    } catch {
      if (isSameActiveThread(activeState.id, aui.threadListItem.getState().id)) {
        setNotice("无法保存当前会话的数据源，请重试。");
        onReadinessChange(false);
      }
    }
  }

  const selectedSourceName = catalog?.data_sources.find(
    (source) => source.id === selectedId,
  )?.display_name;

  return (
    <div className="min-w-0 max-w-full">
      <DataSourceSelector
        availableSources={availableSources}
        selectedId={selectedId}
        selectedSourceName={selectedSourceName}
        disabled={
          !catalog || (hasUserMessages && Boolean(boundSourceId)) || availableSources.length === 0
        }
        notice={notice || (loadError ? "数据源列表加载失败" : "")}
        onChange={(sourceId) => void changeSource(sourceId)}
      />
      {isAdmin && catalog && availableSources.length === 0 && (
        <div className="flex min-h-7 flex-wrap items-center gap-2 px-1 font-sans">
          <span role="status" className="min-w-0 text-[11px] text-[#89847a]">
            {catalog.migration_status === "failed"
              ? "旧 Wren 配置导入失败；原配置仍保留，请检查 Agent 设置。"
              : "配置并应用一个 MySQL 数据源后即可开始查询。"}
          </span>
          <a
            href="/settings/wren"
            className="rounded-md px-1.5 py-1 text-xs text-[#9c6046] underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none"
          >
            打开数据源设置
          </a>
        </div>
      )}
      {!isAdmin && catalog && availableSources.length === 0 && (
        <p role="status" className="px-1 pb-1 font-sans text-[11px] leading-5 text-[#89847a]">
          当前没有可用数据源，请联系管理员分配。
        </p>
      )}
    </div>
  );
};

const ModelProfileSelector: FC<{
  userId: string;
  isAdmin: boolean;
}> = ({ userId, isAdmin }) => {
  const aui = useAui();
  const threadId = useAuiState((state) => state.optional.threadListItem?.remoteId);
  const threadItemId = useAuiState((state) => state.optional.threadListItem?.id);
  const [catalog, setCatalog] = useState<ChatModelCatalog | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [notice, setNotice] = useState("");
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const nextCatalog = await fetchChatModelOptions();
        if (active) {
          setCatalog(nextCatalog);
          setLoadError(false);
        }
      } catch {
        if (active) setLoadError(true);
      }
    };
    void load();
    window.addEventListener("askdb:model-catalog-updated", load);
    return () => {
      active = false;
      window.removeEventListener("askdb:model-catalog-updated", load);
    };
  }, []);

  const availableProfiles = catalog?.profiles.filter((profile) => profile.available) ?? [];
  const profileOptions = availableProfiles.map((profile) => ({
    value: profile.id,
    label: profile.name,
    description: profile.model,
    badge: profile.id === catalog?.default_profile_id ? "默认" : undefined,
  }));

  useEffect(() => {
    if (!catalog) return;
    const result = reconcileLocalThreadModelSelection(userId, threadId, catalog);
    const draftProfileId = !threadId && threadItemId
      ? getDraftThreadModelProfileId(userId, threadItemId)
      : undefined;
    const draftProfileAvailable = catalog.profiles.some(
      (profile) => profile.id === draftProfileId && profile.available,
    );
    const selectedProfileId = draftProfileAvailable ? draftProfileId : result.selectedId;
    setSelectedId(selectedProfileId);
    setNotice(result.notice);

    // Keep a usable fallback with the draft until the first message initializes it.
    if (
      !threadId &&
      threadItemId &&
      selectedProfileId &&
      (selectedProfileId !== catalog.default_profile_id ||
        (draftProfileId !== undefined && !draftProfileAvailable))
    ) {
      setDraftThreadModelProfileId(userId, threadItemId, selectedProfileId);
    }
  }, [catalog, threadId, threadItemId, userId]);

  function changeProfile(profileId: string) {
    const initialState = aui.threadListItem.getState();
    if (!initialState.id) return;
    try {
      if (initialState.remoteId) {
        setThreadModelProfileId(userId, initialState.remoteId, profileId);
      } else {
        setDraftThreadModelProfileId(userId, initialState.id, profileId);
      }
      setSelectedId(profileId);
      setNotice("");
    } catch {
      setNotice("无法保存当前会话的模型选择，请重试。");
    }
  }

  return (
    <div className="flex min-h-8 min-w-0 max-w-full flex-wrap items-center gap-2 px-1 font-sans">
      <label htmlFor="askdb-model-profile" className="text-[11px] text-[#89847a]">
        模型
      </label>
      <ComposerSelect
        id="askdb-model-profile"
        aria-label="选择当前会话使用的模型"
        value={selectedId}
        disabled={!catalog || availableProfiles.length === 0}
        options={profileOptions}
        placeholder={loadError ? "模型配置加载失败" : "请先配置模型"}
        onValueChange={changeProfile}
      />
      {notice && (
        <span role="status" className="text-[11px] text-[#9c6046]">
          {notice}
        </span>
      )}
      {catalog &&
        availableProfiles.length === 0 &&
        (isAdmin ? (
          <a
            href="/settings/models"
            className="rounded-md px-1.5 py-1 text-xs text-[#9c6046] underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none"
          >
            打开模型设置
          </a>
        ) : (
          <span className="text-[11px] text-[#89847a]">管理员尚未配置可用模型</span>
        ))}
    </div>
  );
};

const ComposerAction: FC<{
  threadInitializing: boolean;
  dataSourceReady: boolean;
  children: ReactNode;
}> = ({ threadInitializing, dataSourceReady, children }) => {
  // The stop control only cancels the send while no run it could stop is going.
  const isSending = useAuiState(
    (s) =>
      s.composer.submission !== undefined && !(s.thread.isRunning && s.thread.capabilities.cancel),
  );

  return (
    <div className="aui-composer-action-wrapper relative flex w-full flex-wrap items-end justify-between gap-2">
      <ComposerAddAttachment />
      <div className="flex max-w-full flex-wrap items-end justify-end gap-1.5">
        <div className="flex max-w-full flex-wrap items-center justify-end gap-1.5">{children}</div>
        <div className="flex items-center gap-1.5">
          <AuiIf condition={(s) => s.thread.capabilities.dictation}>
            <AuiIf condition={(s) => s.composer.dictation == null}>
              <ComposerPrimitive.Dictate asChild>
                <TooltipIconButton
                  tooltip="语音输入"
                  side="bottom"
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="aui-composer-dictate text-muted-foreground hover:text-foreground size-7 rounded-full"
                  aria-label="开始语音输入"
                >
                  <MicIcon className="aui-composer-dictate-icon size-4" />
                </TooltipIconButton>
              </ComposerPrimitive.Dictate>
            </AuiIf>
            <AuiIf condition={(s) => s.composer.dictation != null}>
              <ComposerPrimitive.StopDictation asChild>
                <TooltipIconButton
                  tooltip="停止语音输入"
                  side="bottom"
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="aui-composer-stop-dictation text-destructive size-7 rounded-full"
                  aria-label="停止语音输入"
                >
                  <SquareIcon className="aui-composer-stop-dictation-icon size-3.5 animate-pulse fill-current" />
                </TooltipIconButton>
              </ComposerPrimitive.StopDictation>
            </AuiIf>
          </AuiIf>
          <AuiIf
            condition={(s) =>
              !s.composer.canCancel ||
              (s.thread.voice !== undefined && s.composer.submission === undefined)
            }
          >
            <ComposerPrimitive.Send asChild>
              <TooltipIconButton
                tooltip="发送消息"
                side="bottom"
                type="button"
                variant="default"
                size="icon"
                className="aui-composer-send size-7 rounded-full"
                aria-label="发送消息"
                disabled={threadInitializing || !dataSourceReady}
              >
                <ArrowUpIcon className="aui-composer-send-icon size-4" />
              </TooltipIconButton>
            </ComposerPrimitive.Send>
          </AuiIf>
          <AuiIf
            condition={(s) =>
              s.composer.canCancel &&
              (s.thread.voice === undefined || s.composer.submission !== undefined)
            }
          >
            <ComposerPrimitive.Cancel asChild>
              <Button
                type="button"
                variant="default"
                size="icon"
                className="aui-composer-cancel size-7 rounded-full"
                aria-label={isSending ? "取消发送" : "停止生成"}
              >
                <SquareIcon className="aui-composer-cancel-icon size-3.5 fill-current" />
              </Button>
            </ComposerPrimitive.Cancel>
          </AuiIf>
        </div>
      </div>
    </div>
  );
};

const MessageError: FC = () => {
  return (
    <MessagePrimitive.Error>
      <ErrorPrimitive.Root className="aui-message-error-root border-destructive bg-destructive/10 text-destructive dark:bg-destructive/5 mt-2 rounded-md border p-3 text-sm dark:text-red-200">
        <ErrorPrimitive.Message className="aui-message-error-message line-clamp-2" />
      </ErrorPrimitive.Root>
    </MessagePrimitive.Error>
  );
};

const AssistantMessage: FC<{ user: AuthUser }> = ({ user }) => {
  const ACTION_BAR_PT = "pt-1.5";
  const ACTION_BAR_HEIGHT = `min-h-7.5 ${ACTION_BAR_PT}`;

  return (
    <MessagePrimitive.Root
      data-slot="aui_assistant-message-root"
      data-role="assistant"
      className="fade-in slide-in-from-bottom-1 animate-in relative -mb-7.5 pb-7.5 duration-150 [contain-intrinsic-size:auto_200px] [content-visibility:auto]"
    >
      <div
        data-slot="aui_assistant-message-content"
        className="text-foreground px-2 leading-[1.8] wrap-break-word"
      >
        <MessagePrimitive.Parts>
          {({ part }) => {
            if (part.type === "text") return <MarkdownText />;
            if (part.type === "tool-call") return part.toolUI ?? <ToolFallback {...part} />;
            return null;
          }}
        </MessagePrimitive.Parts>
        <AuiIf
          condition={(s) => s.message.status?.type === "running" && s.message.parts.length === 0}
        >
          <span
            data-slot="aui_assistant-message-indicator"
            className="animate-pulse font-sans"
            aria-label="助手正在处理"
          >
            {"●"}
          </span>
        </AuiIf>
        <MessageError />
      </div>

      <div
        data-slot="aui_assistant-message-footer"
        className={cn("ms-2 flex items-center", ACTION_BAR_HEIGHT)}
      >
        <BranchPicker />
        <AssistantActionBar user={user} />
      </div>
    </MessagePrimitive.Root>
  );
};

const AssistantActionBar: FC<{ user: AuthUser }> = ({ user }) => {
  const message = useAuiState((state) => state.message);
  const messages = useAuiState((state) => state.thread.messages);
  const threadId = useAuiState((state) => state.optional.threadListItem?.remoteId);
  const [queryContext, setQueryContext] = useState<MemorySubmissionContext | null>(null);
  const [ruleContext, setRuleContext] = useState<MemorySubmissionContext | null>(null);
  const [queryReady, setQueryReady] = useState(false);
  const [memoryMode, setMemoryMode] = useState<"query-example" | "business-rule" | null>(null);
  const [memoryError, setMemoryError] = useState("");
  const messageIndex = messages.findIndex((item) => item.id === message.id);
  const sourceMessage = messageIndex > 0
    ? [...messages.slice(0, messageIndex)].reverse().find((item) => item.role === "user")
    : undefined;
  const sourceQuestion = sourceMessage?.content
    .flatMap((part) => part.type === "text" ? [part.text] : [])
    .join("")
    .trim() ?? "";
  const completed = message.status?.type === "complete";
  const personalMemoryTurn = /(你.{0,6}记得.{0,6}我|我.{0,6}(?:叫什么|的名字|的偏好)|记住我)/u.test(sourceQuestion);

  useEffect(() => {
    let active = true;
    setMemoryError("");
    setQueryReady(false);
    setQueryContext(null);
    setRuleContext(null);
    if (!threadId || !sourceMessage || !sourceQuestion || !completed || personalMemoryTurn) return;
    const dataSourceId = getThreadDataSourceId(user.user_id, threadId);
    if (!dataSourceId) return;
    void deriveSourceTurnKey(threadId, sourceMessage.id).then(({ turnId, sourceTurnKey }) => {
      if (!active) return;
      const baseContext: MemorySubmissionContext = {
        threadId,
        dataSourceId,
        sourceTurnKey,
        question: sourceQuestion,
        sqlTemplate: "",
      };
      setRuleContext(baseContext);
      const artifacts = getThreadResultArtifacts(user.user_id, threadId, sourceTurnKey, turnId);
      const query = getSuccessfulQueryArtifacts(artifacts)[0];
      if (query) {
        setQueryContext({ ...baseContext, sqlTemplate: query.sql });
        setQueryReady(true);
      }
    }).catch(() => {
      if (active) setMemoryError("暂时无法关联这条回答的查询来源，请刷新页面后重试。");
    });
    return () => { active = false; };
  }, [completed, personalMemoryTurn, sourceMessage?.id, sourceQuestion, threadId, user.user_id]);

  function openMemory(mode: "query-example" | "business-rule") {
    setMemoryError("");
    const context = mode === "query-example" ? queryContext : ruleContext;
    if (!context) {
      setMemoryError("这条回答的来源暂不可用，请刷新页面后重试。");
      return;
    }
    setMemoryMode(mode);
  }

  return (
    <>
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="not-last"
      className="aui-assistant-action-bar-root text-muted-foreground animate-in fade-in col-start-3 row-start-2 -ms-1 flex gap-1 duration-200"
    >
      <ActionBarPrimitive.Copy asChild>
        <TooltipIconButton tooltip="复制">
          <AuiIf condition={(s) => s.message.isCopied}>
            <CheckIcon className="animate-in zoom-in-50 fade-in duration-200 ease-out" />
          </AuiIf>
          <AuiIf condition={(s) => !s.message.isCopied}>
            <CopyIcon className="animate-in zoom-in-75 fade-in duration-150" />
          </AuiIf>
        </TooltipIconButton>
      </ActionBarPrimitive.Copy>
      <ActionBarPrimitive.Reload asChild>
        <TooltipIconButton tooltip="重新生成">
          <RefreshCwIcon />
        </TooltipIconButton>
      </ActionBarPrimitive.Reload>
      <ActionBarMorePrimitive.Root>
        <ActionBarMorePrimitive.Trigger asChild>
          <TooltipIconButton tooltip="更多操作" className="data-[state=open]:bg-accent">
            <MoreHorizontalIcon />
          </TooltipIconButton>
        </ActionBarMorePrimitive.Trigger>
        <ActionBarMorePrimitive.Content
          side="bottom"
          align="start"
          sideOffset={6}
          className="aui-action-bar-more-content bg-popover/95 text-popover-foreground data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=open]:animate-in data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 data-[state=closed]:animate-out data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 z-50 min-w-[8rem] overflow-hidden rounded-xl border p-1.5 shadow-lg backdrop-blur-sm"
        >
          <ActionBarPrimitive.ExportMarkdown asChild>
            <ActionBarMorePrimitive.Item className="aui-action-bar-more-item hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm outline-none select-none">
              <DownloadIcon className="size-4" />
              导出为 Markdown
            </ActionBarMorePrimitive.Item>
          </ActionBarPrimitive.ExportMarkdown>
          {queryReady && <ActionBarMorePrimitive.Item onClick={() => openMemory("query-example")} className="aui-action-bar-more-item hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm outline-none select-none">提交为查询示例</ActionBarMorePrimitive.Item>}
          {ruleContext && <ActionBarMorePrimitive.Item onClick={() => openMemory("business-rule")} className="aui-action-bar-more-item hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm outline-none select-none">提交为业务规则</ActionBarMorePrimitive.Item>}
        </ActionBarMorePrimitive.Content>
      </ActionBarMorePrimitive.Root>
    </ActionBarPrimitive.Root>
    {memoryError && <p role="status" className="ml-2 text-[11px] text-[#9c6046]">{memoryError}</p>}
    <MemorySubmissionDialog
      open={memoryMode !== null}
      onOpenChange={(open) => { if (!open) setMemoryMode(null); }}
      mode={memoryMode ?? "business-rule"}
      context={memoryMode === "query-example" ? queryContext : ruleContext}
      user={user}
      onSubmitted={() => window.dispatchEvent(new Event("askdb:memory-candidates-updated"))}
    />
    </>
  );
};

const UserMessage: FC = () => {
  return (
    <MessagePrimitive.Root
      data-slot="aui_user-message-root"
      className="fade-in slide-in-from-bottom-1 animate-in grid auto-rows-auto grid-cols-[minmax(72px,1fr)_auto] content-start gap-y-2 px-2 duration-150 [contain-intrinsic-size:auto_200px] [content-visibility:auto] [&:where(>*)]:col-start-2"
      data-role="user"
    >
      <UserMessageAttachments />

      <div className="aui-user-message-content-wrapper relative col-start-2 min-w-0">
        <div className="aui-user-message-content peer bg-[#eeeae2] text-foreground rounded-2xl px-4 py-2.5 wrap-break-word empty:hidden">
          <MessagePrimitive.Parts />
        </div>
        <div className="aui-user-action-bar-wrapper absolute start-0 top-1/2 -translate-x-full -translate-y-1/2 pe-2 peer-empty:hidden rtl:translate-x-full">
          <UserActionBar />
        </div>
      </div>

      <BranchPicker
        data-slot="aui_user-branch-picker"
        className="col-span-full col-start-1 -me-1 justify-end"
      />
    </MessagePrimitive.Root>
  );
};

const UserActionBar: FC = () => {
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="not-last"
      className="aui-user-action-bar-root flex flex-col items-end"
    >
      <ActionBarPrimitive.Edit asChild>
        <TooltipIconButton tooltip="编辑" className="aui-user-action-edit">
          <PencilIcon />
        </TooltipIconButton>
      </ActionBarPrimitive.Edit>
    </ActionBarPrimitive.Root>
  );
};

const EditComposer: FC = () => {
  return (
    <MessagePrimitive.Root
      data-slot="aui_edit-composer-wrapper"
      className="flex flex-col px-2 [contain-intrinsic-size:auto_200px] [content-visibility:auto]"
    >
      <ComposerPrimitive.Root className="aui-edit-composer-root border-foreground/10 focus-within:border-foreground/25 ms-auto flex w-full max-w-[85%] cursor-text flex-col rounded-(--composer-radius) border bg-(--composer-bg) shadow-[0_4px_16px_-8px_rgba(0,0,0,0.08),0_1px_2px_rgba(0,0,0,0.04)] transition-[border-color] dark:shadow-none">
        <ComposerPrimitive.Input
          className="aui-edit-composer-input text-foreground min-h-14 w-full resize-none bg-transparent px-4 pt-3 pb-1 text-base outline-none"
          autoFocus
        />
        <div className="aui-edit-composer-footer mx-2.5 mb-2.5 flex items-center gap-1.5 self-end">
          <ComposerPrimitive.Cancel asChild>
            <Button variant="ghost" size="sm" className="h-8 px-3">
              取消
            </Button>
          </ComposerPrimitive.Cancel>
          <ComposerPrimitive.Send asChild>
            <Button size="sm" className="h-8 px-3">
              保存修改
            </Button>
          </ComposerPrimitive.Send>
        </div>
      </ComposerPrimitive.Root>
    </MessagePrimitive.Root>
  );
};

const BranchPicker: FC<BranchPickerPrimitive.Root.Props> = ({ className, ...rest }) => {
  return (
    <BranchPickerPrimitive.Root
      hideWhenSingleBranch
      className={cn(
        "aui-branch-picker-root text-muted-foreground -ms-2 me-2 inline-flex items-center text-xs",
        className,
      )}
      {...rest}
    >
      <BranchPickerPrimitive.Previous asChild>
        <TooltipIconButton tooltip="上一条">
          <ChevronLeftIcon />
        </TooltipIconButton>
      </BranchPickerPrimitive.Previous>
      <span className="aui-branch-picker-state font-medium">
        <BranchPickerPrimitive.Number /> / <BranchPickerPrimitive.Count />
      </span>
      <BranchPickerPrimitive.Next asChild>
        <TooltipIconButton tooltip="下一条">
          <ChevronRightIcon />
        </TooltipIconButton>
      </BranchPickerPrimitive.Next>
    </BranchPickerPrimitive.Root>
  );
};
