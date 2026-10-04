"use client";

import {
  ExportedMessageRepository,
  RuntimeAdapterProvider,
  useAui,
  type RemoteThreadListAdapter,
  type ThreadHistoryAdapter,
} from "@assistant-ui/react";
import { useMemo, type PropsWithChildren } from "react";
import { authMutation, requestCsrfToken } from "@/lib/auth-api";
import { fetchDataSourceCatalog } from "@/lib/data-sources";
import { reconcileThreadModelSelection } from "@/lib/model-selection";
import type { ModelSelectionCatalog } from "@/lib/model-selection";
import { formatQueryResults } from "@/lib/chat-output";
import {
  archiveThread as archiveRemoteThread,
  fetchThreadStates,
  listThreads as fetchThreadPage,
  patchThreadMetadata,
  restoreThread as restoreRemoteThread,
  ThreadApiError,
  type ThreadMetadata,
} from "@/lib/thread-api";
import { getThreadGroupPageState } from "@/lib/thread-grouping.mjs";

const THREADS_KEY = (userId: string) => `askdb:user:${encodeURIComponent(userId)}:chat:threads`;
const THREAD_CACHE_VERSION_KEY = (userId: string) =>
  `askdb:user:${encodeURIComponent(userId)}:chat-cache-version`;
const THREAD_CACHE_VERSION = "2";
const MESSAGES_KEY = (userId: string, threadId: string) =>
  `askdb:user:${encodeURIComponent(userId)}:chat:messages:${threadId}`;
const RESULT_ARTIFACTS_KEY = (userId: string, threadId: string) =>
  `askdb:user:${encodeURIComponent(userId)}:chat:results:${threadId}`;
const HISTORY_IMPORT_KEY = (userId: string, threadId: string) =>
  `askdb:user:${encodeURIComponent(userId)}:chat:history-import:${threadId}`;
const SERVER_THREAD_ID = /^[a-f0-9]{32}$/;
const legacyThreadRedirects = new Map<string, string>();
const MAX_IMPORTED_HISTORY_BYTES = 2 * 1024 * 1024;
const MAX_IMPORTED_TURNS = 500;
const MAX_THREAD_REQUEST_BYTES = 64 * 1024;
const MAX_RESULT_ARTIFACT_BYTES = 2 * 1024 * 1024;

type StoredThread = {
  remoteId: string;
  status: "regular" | "archived";
  title?: string;
  modelProfileId?: string;
  dataSourceId?: string;
  dataSourceNameSnapshot?: string;
  expiresAt?: string;
  historyImportPending?: boolean;
  isPinned?: boolean;
  archivedAt?: string;
  lastUserTurnAt?: string;
  retentionPaused?: boolean;
  retentionRemainingSeconds?: number;
  metadataRevision?: number;
};

type ImportedTurn = { user_content: string; assistant_content: string };
type PreparedHistory = {
  turns: ImportedTurn[];
  assistantMessageIds: string[];
  sanitizedTurns: number;
  omittedTurns: number;
};
type HistoryImportPlan = {
  importId: string;
  chunks: ImportedTurn[][];
  chunkHashes: string[];
  turnCount: number;
  contentBytes: number;
};

type DraftThreadPreferences = Pick<
  StoredThread,
  "modelProfileId" | "dataSourceId" | "dataSourceNameSnapshot"
>;

const draftThreadPreferences = new Map<string, DraftThreadPreferences>();
const threadSearchQueries = new Map<string, string>();
const loadedRecentIdsByUser = new Map<string, Set<string>>();
const recentThreadPageStates = new Map<string, { query: string; hasMore: boolean }>();
const cacheVersionCheckedByUser = new Set<string>();
const recentGroupByUser = new Map<string, string>();

export function setThreadSearchQuery(userId: string, query: string) {
  threadSearchQueries.set(userId, query);
}

export function getRecentThreadPageState(userId: string, query: string) {
  const state = recentThreadPageStates.get(userId);
  return state?.query === query.trim() ? state.hasMore : false;
}

function publishRecentThreadPageState(userId: string, query: string, hasMore: boolean) {
  const normalizedQuery = query.trim();
  recentThreadPageStates.set(userId, { query: normalizedQuery, hasMore });
  window.dispatchEvent(new CustomEvent("askdb:thread-list-page-state", {
    detail: { userId, query: normalizedQuery, hasMore },
  }));
}

function threadSearchQuery(userId: string) {
  return threadSearchQueries.get(userId) ?? "";
}

function initializeThreadCache(userId: string) {
  if (cacheVersionCheckedByUser.has(userId)) return;
  cacheVersionCheckedByUser.add(userId);
  const versionKey = THREAD_CACHE_VERSION_KEY(userId);
  if (window.localStorage.getItem(versionKey) === THREAD_CACHE_VERSION) return;
  const oldPrefix = `askdb:user:${encodeURIComponent(userId)}:chat:`;
  for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
    const key = window.localStorage.key(index);
    if (key?.startsWith(oldPrefix)) window.localStorage.removeItem(key);
  }
  const turnPrefix = `askdb:user:${encodeURIComponent(userId)}:chat:turn:`;
  for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
    const key = window.localStorage.key(index);
    if (key?.startsWith(turnPrefix)) window.localStorage.removeItem(key);
  }
  window.localStorage.setItem(versionKey, THREAD_CACHE_VERSION);
}

function draftThreadKey(userId: string, threadItemId: string) {
  return `${userId}\u0000${threadItemId}`;
}

function updateDraftThreadPreferences(
  userId: string,
  threadItemId: string,
  preferences: DraftThreadPreferences,
) {
  const key = draftThreadKey(userId, threadItemId);
  draftThreadPreferences.set(key, {
    ...draftThreadPreferences.get(key),
    ...preferences,
  });
}

export function getDraftThreadModelProfileId(userId: string, threadItemId: string): string | undefined {
  return draftThreadPreferences.get(draftThreadKey(userId, threadItemId))?.modelProfileId;
}

export function setDraftThreadModelProfileId(
  userId: string,
  threadItemId: string,
  profileId: string,
) {
  updateDraftThreadPreferences(userId, threadItemId, { modelProfileId: profileId });
}

export function getDraftThreadDataSource(userId: string, threadItemId: string) {
  const preferences = draftThreadPreferences.get(draftThreadKey(userId, threadItemId));
  return preferences?.dataSourceId && preferences.dataSourceNameSnapshot
    ? { id: preferences.dataSourceId, name: preferences.dataSourceNameSnapshot }
    : undefined;
}

export function setDraftThreadDataSource(
  userId: string,
  threadItemId: string,
  sourceId: string,
  sourceName: string,
) {
  updateDraftThreadPreferences(userId, threadItemId, {
    dataSourceId: sourceId,
    dataSourceNameSnapshot: sourceName,
  });
}

type StoredRepository = ExportedMessageRepository;

function readThreads(userId: string) {
  const raw = window.localStorage.getItem(THREADS_KEY(userId));
  if (!raw) return [] as StoredThread[];
  try {
    const value: unknown = JSON.parse(raw);
    if (!Array.isArray(value)) return [];
    const valid = value.filter(
      (thread): thread is StoredThread =>
        typeof thread === "object" &&
        thread !== null &&
        "remoteId" in thread &&
        typeof thread.remoteId === "string" &&
        "status" in thread &&
        (thread.status === "regular" || thread.status === "archived"),
    );
    return valid.map((thread) => ({
      remoteId: thread.remoteId,
      status: thread.status,
      ...(typeof thread.title === "string" ? { title: thread.title } : {}),
      ...(typeof thread.modelProfileId === "string"
        ? { modelProfileId: thread.modelProfileId }
        : {}),
      ...(typeof thread.dataSourceId === "string" ? { dataSourceId: thread.dataSourceId } : {}),
      ...(typeof thread.dataSourceNameSnapshot === "string"
        ? { dataSourceNameSnapshot: thread.dataSourceNameSnapshot }
        : {}),
      ...(typeof thread.expiresAt === "string" ? { expiresAt: thread.expiresAt } : {}),
      ...(thread.historyImportPending === true ? { historyImportPending: true } : {}),
      ...(typeof thread.isPinned === "boolean" ? { isPinned: thread.isPinned } : {}),
      ...(typeof thread.archivedAt === "string" ? { archivedAt: thread.archivedAt } : {}),
      ...(typeof thread.lastUserTurnAt === "string" ? { lastUserTurnAt: thread.lastUserTurnAt } : {}),
      ...(thread.retentionPaused === true ? { retentionPaused: true } : {}),
      ...(Number.isSafeInteger(thread.retentionRemainingSeconds)
        ? { retentionRemainingSeconds: thread.retentionRemainingSeconds as number }
        : {}),
      ...(Number.isSafeInteger(thread.metadataRevision)
        ? { metadataRevision: thread.metadataRevision as number }
        : {}),
    }));
  } catch {
    return [];
  }
}

function writeThreads(userId: string, threads: StoredThread[]) {
  window.localStorage.setItem(THREADS_KEY(userId), JSON.stringify(threads));
}

function storedThreadFromMetadata(metadata: ThreadMetadata, existing?: StoredThread): StoredThread {
  return {
    remoteId: metadata.thread_id,
    status: metadata.archived_at ? "archived" : "regular",
    ...(metadata.title ? { title: metadata.title } : {}),
    ...(existing?.modelProfileId ? { modelProfileId: existing.modelProfileId } : {}),
    dataSourceId: metadata.data_source_id,
    ...(metadata.data_source_name ? { dataSourceNameSnapshot: metadata.data_source_name } : {}),
    expiresAt: metadata.expires_at,
    historyImportPending: metadata.history_import_pending,
    isPinned: metadata.is_pinned,
    ...(metadata.archived_at ? { archivedAt: metadata.archived_at } : {}),
    lastUserTurnAt: metadata.last_user_turn_at,
    retentionPaused: metadata.retention_paused,
    ...(metadata.retention_remaining_seconds !== null
      ? { retentionRemainingSeconds: metadata.retention_remaining_seconds }
      : {}),
    metadataRevision: metadata.metadata_revision,
  };
}

export function getCachedThread(userId: string, threadId: string): StoredThread | undefined {
  const canonicalId = canonicalThreadId(threadId);
  return readThreads(userId).find(
    (thread) => thread.remoteId === canonicalId || thread.remoteId === threadId,
  );
}

export function saveServerThreadMetadata(userId: string, metadata: ThreadMetadata) {
  const threads = readThreads(userId);
  const existing = threads.find((thread) => thread.remoteId === metadata.thread_id);
  const updated = storedThreadFromMetadata(metadata, existing);
  writeThreads(
    userId,
    [updated, ...threads.filter((thread) => thread.remoteId !== metadata.thread_id)],
  );
  window.dispatchEvent(new Event("askdb:thread-metadata-updated"));
}

function toRemoteThread(
  thread: StoredThread,
  groupState: { showHeading: boolean; showDivider: boolean } = {
    showHeading: false,
    showDivider: false,
  },
) {
  return {
    remoteId: thread.remoteId,
    status: thread.status,
    ...(thread.title ? { title: thread.title } : {}),
    custom: {
      dataSourceId: thread.dataSourceId,
      dataSourceName: thread.dataSourceNameSnapshot,
      isPinned: thread.isPinned === true,
      expiresAt: thread.expiresAt,
      lastUserTurnAt: thread.lastUserTurnAt,
      archivedAt: thread.archivedAt,
      retentionPaused: thread.retentionPaused === true,
      retentionRemainingSeconds: thread.retentionRemainingSeconds,
      metadataRevision: thread.metadataRevision,
      historyImportPending: thread.historyImportPending === true,
      showDataSourceHeading: groupState.showHeading,
      showDataSourceDivider: groupState.showDivider,
    },
  };
}

function canonicalThreadId(threadId: string): string {
  let current = threadId;
  const seen = new Set<string>();
  while (legacyThreadRedirects.has(current) && !seen.has(current)) {
    seen.add(current);
    current = legacyThreadRedirects.get(current)!;
  }
  return current;
}

export function isServerThreadId(threadId: string | undefined): threadId is string {
  return Boolean(threadId && SERVER_THREAD_ID.test(threadId));
}

type ThreadDeleteRequestDetail = {
  userId: string;
  threadId: string;
  originalId: string;
  resolve: (deleted: boolean) => void;
};

export function requestThreadDeletionFromUI(
  userId: string,
  threadId: string,
  originalId = threadId,
): Promise<boolean> {
  const canonicalId = canonicalThreadId(threadId);
  if (!isServerThreadId(canonicalId)) {
    removeLocalThread(userId, canonicalId, originalId);
    return Promise.resolve(true);
  }
  return new Promise((resolve) => {
    const event = new CustomEvent<ThreadDeleteRequestDetail>("askdb:thread-delete-requested", {
      cancelable: true,
      detail: { userId, threadId: canonicalId, originalId, resolve },
    });
    if (window.dispatchEvent(event)) resolve(false);
  });
}

export function removeDeletedThreadCache(
  userId: string,
  threadId: string,
  originalId = threadId,
) {
  removeLocalThread(userId, canonicalThreadId(threadId), originalId);
}

export function isThreadHistoryImportPending(userId: string, threadId: string | undefined) {
  if (!threadId) return false;
  return readThreads(userId).some(
    (thread) => thread.remoteId === canonicalThreadId(threadId) && thread.historyImportPending,
  );
}

export function saveThreadResultArtifact(
  userId: string,
  threadId: string,
  turnId: string,
  output: unknown,
) {
  const key = RESULT_ARTIFACTS_KEY(userId, canonicalThreadId(threadId));
  let artifacts: Record<string, unknown[]> = {};
  try {
    const stored: unknown = JSON.parse(window.localStorage.getItem(key) ?? "{}");
    if (typeof stored === "object" && stored !== null && !Array.isArray(stored)) {
      artifacts = Object.fromEntries(
        Object.entries(stored).filter(([, value]) => Array.isArray(value)),
      ) as Record<string, unknown[]>;
    }
  } catch {
    // Rebuild a corrupt local artifact index from the current result.
  }
  artifacts[turnId] = [...(artifacts[turnId] ?? []), output];
  let serialized = JSON.stringify(artifacts);
  while (
    new TextEncoder().encode(serialized).byteLength > MAX_RESULT_ARTIFACT_BYTES &&
    Object.keys(artifacts).length > 1
  ) {
    delete artifacts[Object.keys(artifacts)[0]];
    serialized = JSON.stringify(artifacts);
  }
  try {
    if (new TextEncoder().encode(serialized).byteLength <= MAX_RESULT_ARTIFACT_BYTES) {
      window.localStorage.setItem(key, serialized);
    }
  } catch {
    // Quota limits must not interrupt the live assistant stream.
  }
}

export function getThreadResultArtifacts(
  userId: string,
  threadId: string,
  turnId: string,
  legacyTurnId?: string,
) {
  const artifacts = readThreadResultArtifacts(userId, canonicalThreadId(threadId));
  return artifacts[turnId] ?? (legacyTurnId ? artifacts[legacyTurnId] : undefined) ?? [];
}

function setThreadHistoryImportPending(userId: string, threadId: string, pending: boolean) {
  const threads = readThreads(userId).map((thread) =>
    thread.remoteId === threadId
      ? { ...thread, ...(pending ? { historyImportPending: true } : { historyImportPending: false }) }
      : thread,
  );
  writeThreads(userId, threads);
  window.dispatchEvent(new Event("askdb:thread-data-source-updated"));
}

export async function ensureServerThread(userId: string, threadId: string): Promise<string> {
  initializeThreadCache(userId);
  const canonicalId = canonicalThreadId(threadId);
  if (isServerThreadId(canonicalId)) return canonicalId;
  if (readThreads(userId).some((thread) => thread.remoteId === threadId)) {
    throw new Error("此版本不导入仅保存在浏览器中的旧会话，请新建对话继续使用。");
  }
  const preferences = draftThreadPreferences.get(draftThreadKey(userId, threadId));
  if (!preferences?.dataSourceId) throw new Error("请先为当前会话选择一个可用数据源。");
  const key = await creationKey(userId, "new:" + threadId);
  const created = await postThread(preferences.dataSourceId, key, []);
  if (created.metadata) {
    saveServerThreadMetadata(userId, created.metadata);
    if (preferences.modelProfileId) {
      setThreadModelProfileId(userId, created.threadId, preferences.modelProfileId);
    }
  } else {
    writeThreads(userId, [
      { remoteId: created.threadId, status: "regular", ...preferences },
      ...readThreads(userId),
    ]);
  }
  return created.threadId;
}

async function creationKey(userId: string, stableSourceId: string): Promise<string> {
  const input = new TextEncoder().encode(`${userId}\u0000${stableSourceId}`);
  const digest = await crypto.subtle.digest("SHA-256", input);
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}

async function buildHistoryImportPlan(
  userId: string,
  legacyId: string,
  dataSourceId: string,
  creationId: string,
  turns: ImportedTurn[],
): Promise<HistoryImportPlan | undefined> {
  if (turns.length === 0) return undefined;
  const inlinePayload = {
    data_source_id: dataSourceId,
    creation_key: creationId,
    initial_history: turns,
  };
  if (new TextEncoder().encode(JSON.stringify(inlinePayload)).byteLength <= MAX_THREAD_REQUEST_BYTES) {
    return undefined;
  }
  const contentBytes = turns.reduce(
    (size, turn) => size + new TextEncoder().encode(turn.user_content).byteLength +
      new TextEncoder().encode(turn.assistant_content).byteLength,
    0,
  );
  if (turns.length > MAX_IMPORTED_TURNS || contentBytes > MAX_IMPORTED_HISTORY_BYTES) {
    throw new Error("旧会话超过 500 轮或 2 MiB 的安全导入上限；原始本地记录已保留。请先清理后重试。");
  }
  const importId = `import_${await creationKey(userId, `history-import:${legacyId}`)}`;
  const chunks: ImportedTurn[][] = [];
  let current: ImportedTurn[] = [];
  for (const turn of turns) {
    const candidate = [...current, turn];
    const candidateBody = JSON.stringify({ import_id: importId, chunk_index: chunks.length, turns: candidate });
    if (new TextEncoder().encode(candidateBody).byteLength <= MAX_THREAD_REQUEST_BYTES) {
      current = candidate;
      continue;
    }
    if (current.length === 0) {
      throw new Error("有一轮旧会话超过单次 64 KiB 安全导入上限；原始本地记录已保留，请先缩短该轮内容。");
    }
    chunks.push(current);
    current = [turn];
    const singleBody = JSON.stringify({ import_id: importId, chunk_index: chunks.length, turns: current });
    if (new TextEncoder().encode(singleBody).byteLength > MAX_THREAD_REQUEST_BYTES) {
      throw new Error("有一轮旧会话超过单次 64 KiB 安全导入上限；原始本地记录已保留，请先缩短该轮内容。");
    }
  }
  if (current.length) chunks.push(current);
  if (chunks.length > 64) {
    throw new Error("旧会话需要超过 64 个导入分块；原始本地记录已保留，请先清理后重试。");
  }
  const chunkHashes = await Promise.all(chunks.map(hashImportChunk));
  return {
    importId,
    chunks,
    chunkHashes,
    turnCount: turns.length,
    contentBytes,
  };
}

async function hashImportChunk(turns: ImportedTurn[]) {
  const payload = JSON.stringify(turns);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(payload));
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}

async function sha256Hex(value: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function inlineImportTurnIds(creationKey: string, turnCount: number) {
  const creationKeyHash = await sha256Hex(creationKey);
  return Promise.all(
    Array.from({ length: turnCount }, (_, index) =>
      sha256Hex(`legacy:${creationKeyHash}:${index}`),
    ),
  );
}

async function chunkedImportTurnIds(plan: HistoryImportPlan) {
  const ids: string[] = [];
  for (let chunkIndex = 0; chunkIndex < plan.chunks.length; chunkIndex += 1) {
    for (let turnIndex = 0; turnIndex < plan.chunks[chunkIndex].length; turnIndex += 1) {
      ids.push(await sha256Hex(`legacy-import:${plan.importId}:${chunkIndex}:${turnIndex}`));
    }
  }
  return ids;
}

function transferLegacyResultArtifacts(
  userId: string,
  serverThreadId: string,
  repository: StoredRepository,
  assistantMessageIds: string[],
  destinationTurnIds: string[],
  turns: ImportedTurn[],
) {
  const messageById = new Map(repository.messages.map((entry) => [entry.message.id, entry]));
  for (let index = 0; index < assistantMessageIds.length; index += 1) {
    const entry = messageById.get(assistantMessageIds[index]);
    const assistantContent = turns[index]?.assistant_content;
    if (!entry || !assistantContent || !destinationTurnIds[index]) continue;
    const artifact = extractLegacyResultArtifact(entry, assistantContent);
    if (artifact) {
      saveThreadResultArtifact(userId, serverThreadId, destinationTurnIds[index], {
        legacy_formatted: artifact,
      });
    }
  }
}

async function postThread(
  dataSourceId: string,
  creationId: string,
  initialHistory: ImportedTurn[],
  importPlan?: HistoryImportPlan,
) {
  const payload = {
    data_source_id: dataSourceId,
    creation_key: creationId,
    ...(importPlan
      ? {
        history_import: {
          import_id: importPlan.importId,
          chunk_hashes: importPlan.chunkHashes,
          turn_count: importPlan.turnCount,
          content_bytes: importPlan.contentBytes,
        },
      }
      : initialHistory.length > 0 ? { initial_history: initialHistory } : {}),
  };
  const body = JSON.stringify(payload);
  if (new TextEncoder().encode(body).byteLength > MAX_THREAD_REQUEST_BYTES) {
    throw new Error("服务端会话创建请求超过 64 KiB 限制；原始本地记录已保留。");
  }
  const csrfToken = await requestCsrfToken();
  const response = await fetch("/api/threads", {
    method: "POST",
    headers: { "content-type": "application/json", "x-csrf-token": csrfToken },
    body,
    cache: "no-store",
    credentials: "same-origin",
  });
  const value = await response.json().catch(() => null) as
    | {
      thread_id?: unknown;
      history_import_pending?: unknown;
      title?: unknown;
      data_source_id?: unknown;
      data_source_name?: unknown;
      is_pinned?: unknown;
      archived_at?: unknown;
      last_user_turn_at?: unknown;
      expires_at?: unknown;
      retention_paused?: unknown;
      retention_remaining_seconds?: unknown;
      metadata_revision?: unknown;
      detail?: { message?: unknown };
      message?: unknown;
    }
    | null;
  if (!response.ok || !value || typeof value.thread_id !== "string" || !SERVER_THREAD_ID.test(value.thread_id)) {
    const message = typeof value?.message === "string"
      ? value.message
      : typeof value?.detail?.message === "string"
        ? value.detail.message
        : "无法创建服务端会话，请重试。";
    throw new Error(message);
  }
  return {
    threadId: value.thread_id,
    historyImportPending: value.history_import_pending === true,
    ...(typeof value.data_source_id === "string" &&
      typeof value.last_user_turn_at === "string" &&
      typeof value.expires_at === "string" &&
      typeof value.is_pinned === "boolean" &&
      typeof value.retention_paused === "boolean" &&
      typeof value.metadata_revision === "number"
      ? {
        metadata: {
          thread_id: value.thread_id,
          title: typeof value.title === "string" ? value.title : null,
          data_source_id: value.data_source_id,
          data_source_name: typeof value.data_source_name === "string" ? value.data_source_name : null,
          is_pinned: value.is_pinned,
          archived_at: typeof value.archived_at === "string" ? value.archived_at : null,
          last_user_turn_at: value.last_user_turn_at,
          expires_at: value.expires_at,
          retention_paused: value.retention_paused,
          retention_remaining_seconds: Number.isSafeInteger(value.retention_remaining_seconds)
            ? value.retention_remaining_seconds as number
            : null,
          metadata_revision: value.metadata_revision,
          history_import_pending: value.history_import_pending === true,
        } satisfies ThreadMetadata,
      }
      : {}),
  };
}

function saveHistoryPlan(userId: string, threadId: string, plan: HistoryImportPlan) {
  try {
    window.localStorage.setItem(HISTORY_IMPORT_KEY(userId, threadId), JSON.stringify(plan));
  } catch {
    throw new Error("无法在浏览器中暂存旧会话导入进度；本地记录未迁移，请释放浏览器存储空间后重试。");
  }
}

function readHistoryPlan(userId: string, threadId: string): HistoryImportPlan | null {
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(HISTORY_IMPORT_KEY(userId, threadId)) ?? "null");
    if (!value || typeof value !== "object") return null;
    const plan = value as Partial<HistoryImportPlan>;
    if (
      typeof plan.importId !== "string" || !/^import_[A-Za-z0-9_-]{16,128}$/.test(plan.importId) ||
      !Array.isArray(plan.chunks) || plan.chunks.length < 1 || plan.chunks.length > 64 ||
      !Array.isArray(plan.chunkHashes) || plan.chunks.length !== plan.chunkHashes.length ||
      plan.chunkHashes.some((hash) => typeof hash !== "string" || !/^[a-f0-9]{64}$/.test(hash)) ||
      !Number.isSafeInteger(plan.turnCount) || (plan.turnCount as number) < 1 ||
      (plan.turnCount as number) > MAX_IMPORTED_TURNS ||
      !Number.isSafeInteger(plan.contentBytes) || (plan.contentBytes as number) < 1 ||
      (plan.contentBytes as number) > MAX_IMPORTED_HISTORY_BYTES ||
      plan.chunks.some((chunk) => !Array.isArray(chunk) || chunk.length === 0) ||
      plan.chunks.reduce((count, chunk) => count + chunk.length, 0) !== plan.turnCount
    ) return null;
    return plan as HistoryImportPlan;
  } catch {
    return null;
  }
}

function moveHistoryPlan(userId: string, oldThreadId: string, newThreadId: string) {
  const oldKey = HISTORY_IMPORT_KEY(userId, oldThreadId);
  const newKey = HISTORY_IMPORT_KEY(userId, newThreadId);
  const raw = window.localStorage.getItem(oldKey);
  if (!raw) return;
  window.localStorage.removeItem(oldKey);
  try {
    window.localStorage.setItem(newKey, raw);
  } catch {
    try { window.localStorage.setItem(oldKey, raw); } catch { /* Keep the original cache untouched. */ }
    throw new Error("无法保存旧会话续传进度；本地记录仍保留，请释放浏览器存储空间后重试。");
  }
}

async function resumeHistoryImportIfNeeded(userId: string, threadId: string) {
  const thread = readThreads(userId).find((item) => item.remoteId === threadId);
  const plan = readHistoryPlan(userId, threadId);
  if (!thread?.historyImportPending && !plan) return;
  if (!plan) {
    throw new Error("此会话的旧记录导入仍未完成，但浏览器中没有续传清单。请删除此待导入会话后，从本地记录重新迁移。");
  }
  const csrfToken = await requestCsrfToken();
  for (let chunkIndex = 0; chunkIndex < plan.chunks.length; chunkIndex += 1) {
    const payload = {
      import_id: plan.importId,
      chunk_index: chunkIndex,
      turns: plan.chunks[chunkIndex],
    };
    if (await hashImportChunk(payload.turns) !== plan.chunkHashes[chunkIndex]) {
      throw new Error("浏览器中的旧会话续传清单校验失败；原始本地记录仍保留，请从本地记录重新迁移。");
    }
    const body = JSON.stringify(payload);
    if (new TextEncoder().encode(body).byteLength > MAX_THREAD_REQUEST_BYTES) {
      throw new Error("保存的导入分块超过 64 KiB，无法安全续传。原始本地记录仍保留。");
    }
    const response = await fetch(`/api/threads/${encodeURIComponent(threadId)}/history-import`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-csrf-token": csrfToken },
      body,
      cache: "no-store",
      credentials: "same-origin",
    });
    if (response.status === 401) {
      clearLocalThreadCache(userId);
      window.location.assign("/login");
      throw new Error("登录状态已失效，请重新登录后继续导入。");
    }
    if (!response.ok) {
      const bodyValue = await response.json().catch(() => null) as {
        detail?: { message?: unknown };
        message?: unknown;
      } | null;
      throw new Error(
        typeof bodyValue?.message === "string" ? bodyValue.message :
          typeof bodyValue?.detail?.message === "string" ? bodyValue.detail.message :
            "旧会话导入中断；打开会话可安全续传，原始本地记录仍保留。",
        );
    }
    const result = await response.json().catch(() => null) as {
      import_id?: unknown;
      completed?: unknown;
    } | null;
    if (result?.import_id !== plan.importId ||
      (chunkIndex === plan.chunks.length - 1 && result.completed !== true)) {
      throw new Error("服务端尚未确认旧会话导入完成；进度已保留，打开会话可继续续传。");
    }
  }
  window.localStorage.removeItem(HISTORY_IMPORT_KEY(userId, threadId));
  if (thread) thread.historyImportPending = false;
  setThreadHistoryImportPending(userId, threadId, false);
}

function branchMessages(repository: StoredRepository) {
  const byId = new Map(repository.messages.map((entry) => [entry.message.id, entry]));
  const path: StoredRepository["messages"] = [];
  let messageId = repository.headId ?? repository.messages.at(-1)?.message.id ?? null;
  const seen = new Set<string>();
  while (messageId && !seen.has(messageId)) {
    seen.add(messageId);
    const entry = byId.get(messageId);
    if (!entry) break;
    path.push(entry);
    messageId = entry.parentId;
  }
  return path.reverse();
}

const IMPORT_FENCED_BLOCK = /```[\s\S]*?```|~~~[\s\S]*?~~~/g;
const IMPORT_RESULT_BLOCK = /<(result|tool_result|tool|observation|think|analysis)[^>]*>[\s\S]*?<\/\1\s*>/gi;
const IMPORT_RESULT_LABEL = /^\s*(?:\*\*)?(?:SQL\s*(?:语句|statement)|查询结果(?:\s*[·|｜].*)?|query\s+results?)(?:\*\*)?\s*$/gim;
const IMPORT_SECRET = /\b(?:api[_ -]?key|password|passwd|token|secret|access[_ -]?key)\b\s*[:=]\s*[^\s,;]+/gi;
const IMPORT_EMAIL = /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi;
const IMPORT_PHONE = /(?<![\w])(?:\+?86[- ]?)?1[3-9]\d{9}(?![\w])|(?<![\w])(?:\+?\d{1,3}[- ])?(?:\(?\d{2,4}\)?[- ])?\d{3,4}[- ]\d{4}(?![\w])/g;
const IMPORT_RECORD_ID = /(?:(?:customer|client|user|member|account|order)[_ -]?(?:id|no|number)|(?:客户|用户|会员|账号|订单)(?:id|ID|编号|号码|号))\s*[:=：#]?\s*["']?[A-Z0-9][A-Z0-9_-]{2,}["']?/gi;
const IMPORT_SQL_LINE = /^\s*(?:SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|EXPLAIN)\b/i;
const IMPORT_SQL_CONTINUATION = /^\s*(?:FROM\b|JOIN\b|LEFT\b|RIGHT\b|INNER\b|FULL\b|CROSS\b|ON\b|WHERE\b|GROUP\s+BY\b|ORDER\s+BY\b|HAVING\b|LIMIT\b|OFFSET\b|UNION\b|INTERSECT\b|EXCEPT\b|AND\b|OR\b|AS\b|ASC\b|DESC\b|,|\)|;|\.)/i;

function sanitizeImportedText(value: string, maxChars = 8192) {
  let text = value.slice(0, maxChars * 4)
    .replace(IMPORT_FENCED_BLOCK, " ")
    .replace(IMPORT_RESULT_BLOCK, " ")
    .replace(IMPORT_RESULT_LABEL, " ");
  const keptLines: string[] = [];
  let insideSql = false;
  // Match Python's str.splitlines() so Web-side sanitization and the Agent's
  // sanitizer agree for uncommon Unicode and control-character line breaks.
  // eslint-disable-next-line no-control-regex
  for (const line of text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/)) {
    const stripped = line.trim();
    if (stripped.startsWith("|")) continue;
    if (IMPORT_SQL_LINE.test(line)) {
      insideSql = !line.includes(";");
      continue;
    }
    if (insideSql) {
      if (!stripped || IMPORT_SQL_CONTINUATION.test(line)) {
        if (line.includes(";")) insideSql = false;
        continue;
      }
      insideSql = false;
    }
    keptLines.push(line);
  }
  text = keptLines.join("\n")
    .replace(IMPORT_SECRET, "[已脱敏]")
    .replace(IMPORT_EMAIL, "[已脱敏标识]")
    .replace(IMPORT_PHONE, "[已脱敏标识]")
    .replace(IMPORT_RECORD_ID, "[已脱敏记录标识]")
    .split("\n")
    .map((part) => part.replace(/\s+$/, ""))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return text.slice(0, maxChars);
}

function importableHistory(repository: StoredRepository): PreparedHistory {
  const result: ImportedTurn[] = [];
  const assistantMessageIds: string[] = [];
  let sanitizedTurns = 0;
  let omittedTurns = 0;
  let pendingUser: string | null = null;
  for (const entry of branchMessages(repository)) {
    const { message } = entry;
    const text = message.content
      .flatMap((part) => part.type === "text" ? [part.text] : [])
      .join("\n")
      .trim();
    if (message.role === "user") {
      if (pendingUser !== null) omittedTurns += 1;
      pendingUser = text || null;
      continue;
    }
    if (message.role === "assistant" && pendingUser !== null) {
      const safeUser = sanitizeImportedText(pendingUser);
      const safeAssistant = sanitizeImportedText(text);
      if (safeUser !== pendingUser || safeAssistant !== text) sanitizedTurns += 1;
      if (safeUser && safeAssistant) {
        result.push({ user_content: safeUser, assistant_content: safeAssistant });
        assistantMessageIds.push(message.id);
      } else {
        omittedTurns += 1;
      }
      pendingUser = null;
    }
  }
  if (pendingUser !== null) omittedTurns += 1;
  let contentBytes = result.reduce(
    (size, turn) => size + new TextEncoder().encode(turn.user_content).byteLength +
      new TextEncoder().encode(turn.assistant_content).byteLength,
    0,
  );
  while (result.length > MAX_IMPORTED_TURNS || contentBytes > MAX_IMPORTED_HISTORY_BYTES) {
    const removed = result.shift();
    if (!removed) break;
    assistantMessageIds.shift();
    contentBytes -= new TextEncoder().encode(removed.user_content).byteLength +
      new TextEncoder().encode(removed.assistant_content).byteLength;
    omittedTurns += 1;
  }
  return { turns: result, assistantMessageIds, sanitizedTurns, omittedTurns };
}

function readThreadResultArtifacts(userId: string, threadId: string): Record<string, unknown[]> {
  try {
    const value: unknown = JSON.parse(
      window.localStorage.getItem(RESULT_ARTIFACTS_KEY(userId, threadId)) ?? "{}",
    );
    if (typeof value !== "object" || value === null || Array.isArray(value)) return {};
    return Object.fromEntries(
      Object.entries(value).filter(([, outputs]) => Array.isArray(outputs)),
    ) as Record<string, unknown[]>;
  } catch {
    return {};
  }
}

function extractLegacyResultArtifact(
  entry: StoredRepository["messages"][number],
  assistantContent: string,
) {
  if (!assistantContent) return "";
  if (entry.message.role !== "assistant") return "";
  const cachedText = entry.message.content
    .flatMap((part) => part.type === "text" ? [part.text] : [])
    .join("\n")
    .trim();
  const separator = `\n\n${assistantContent}`;
  if (!cachedText.endsWith(separator)) return "";
  const artifact = cachedText.slice(0, -separator.length).trim();
  return /\*\*SQL 语句\*\*|查询结果|```sql/i.test(artifact) ? artifact : "";
}

export function getThreadModelProfileId(userId: string, threadId: string): string | undefined {
  const canonicalId = canonicalThreadId(threadId);
  const profileId = readThreads(userId).find(
    (thread) => thread.remoteId === canonicalId || thread.remoteId === threadId,
  )?.modelProfileId;
  return typeof profileId === "string" ? profileId : undefined;
}

export function setThreadModelProfileId(userId: string, threadId: string, profileId: string) {
  const canonicalId = canonicalThreadId(threadId);
  const threads = readThreads(userId);
  let thread = threads.find((item) => item.remoteId === canonicalId || item.remoteId === threadId);
  if (!thread) {
    thread = { remoteId: canonicalId, status: "regular" };
    threads.unshift(thread);
  }
  thread.modelProfileId = profileId;
  writeThreads(userId, threads);
}

export function clearThreadModelProfileId(userId: string, threadId: string) {
  const canonicalId = canonicalThreadId(threadId);
  const threads = readThreads(userId);
  const thread = threads.find((item) => item.remoteId === canonicalId || item.remoteId === threadId);
  if (!thread || typeof thread.modelProfileId !== "string") return;
  delete thread.modelProfileId;
  writeThreads(userId, threads);
}

export function getThreadDataSourceId(userId: string, threadId: string): string | undefined {
  const canonicalId = canonicalThreadId(threadId);
  const sourceId = readThreads(userId).find(
    (thread) => thread.remoteId === canonicalId || thread.remoteId === threadId,
  )?.dataSourceId;
  return typeof sourceId === "string" ? sourceId : undefined;
}

export function getThreadDataSourceName(userId: string, threadId: string): string | undefined {
  const canonicalId = canonicalThreadId(threadId);
  const name = readThreads(userId).find(
    (thread) => thread.remoteId === canonicalId || thread.remoteId === threadId,
  )?.dataSourceNameSnapshot;
  return typeof name === "string" ? name : undefined;
}

export function getThreadExpiresAt(userId: string, threadId: string): string | undefined {
  const canonicalId = canonicalThreadId(threadId);
  const expiresAt = readThreads(userId).find(
    (thread) => thread.remoteId === canonicalId || thread.remoteId === threadId,
  )?.expiresAt;
  return typeof expiresAt === "string" ? expiresAt : undefined;
}

export function setThreadDataSource(userId: string, threadId: string, sourceId: string, sourceName: string) {
  const canonicalId = canonicalThreadId(threadId);
  const threads = readThreads(userId);
  let thread = threads.find((item) => item.remoteId === canonicalId || item.remoteId === threadId);
  if (!thread) {
    thread = { remoteId: canonicalId, status: "regular" };
    threads.unshift(thread);
  }
  thread.dataSourceId = sourceId;
  thread.dataSourceNameSnapshot = sourceName;
  writeThreads(userId, threads);
  window.dispatchEvent(new Event("askdb:thread-data-source-updated"));
}

export function migrateLegacyThreadSources(userId: string, defaultSourceId: string, defaultSourceName: string) {
  if (!defaultSourceId || !defaultSourceName) return 0;
  const threads = readThreads(userId);
  let migrated = 0;
  for (const thread of threads) {
    if (!thread.dataSourceId) {
      thread.dataSourceId = defaultSourceId;
      thread.dataSourceNameSnapshot = defaultSourceName;
      migrated += 1;
    }
  }
  if (migrated) {
    writeThreads(userId, threads);
    window.dispatchEvent(new Event("askdb:thread-data-source-updated"));
  }
  return migrated;
}

export function clearLocalThreadCache(userId: string) {
  const prefix = `askdb:user:${encodeURIComponent(userId)}:chat:`;
  for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
    const key = window.localStorage.key(index);
    if (key?.startsWith(prefix)) window.localStorage.removeItem(key);
  }
  const draftPrefix = `${userId}\u0000`;
  for (const key of draftThreadPreferences.keys()) {
    if (key.startsWith(draftPrefix)) draftThreadPreferences.delete(key);
  }
  for (const [legacyId] of legacyThreadRedirects) {
    if (legacyId.startsWith(`${userId}:`)) legacyThreadRedirects.delete(legacyId);
  }
}

export function reconcileLocalThreadModelSelection(
  userId: string,
  threadId: string | undefined,
  catalog: ModelSelectionCatalog,
) {
  return reconcileThreadModelSelection(threadId, catalog, {
    get: (id) => getThreadModelProfileId(userId, id),
    set: (id, profileId) => setThreadModelProfileId(userId, id, profileId),
    clear: (id) => clearThreadModelProfileId(userId, id),
  });
}

function readRepository(userId: string, threadId: string): StoredRepository {
  const raw = window.localStorage.getItem(MESSAGES_KEY(userId, canonicalThreadId(threadId))) ??
    window.localStorage.getItem(MESSAGES_KEY(userId, threadId));
  if (!raw) return { messages: [] };
  try {
    const repository = JSON.parse(raw) as StoredRepository;
    if (!Array.isArray(repository.messages)) return { messages: [] };
    return {
      ...repository,
      messages: repository.messages.map((entry) => ({
        ...entry,
        message: {
          ...entry.message,
          createdAt: new Date(entry.message.createdAt),
        },
      })),
    };
  } catch {
    return { messages: [] };
  }
}

function upsertMessage(userId: string, threadId: string, item: StoredRepository["messages"][number]) {
  const canonicalId = canonicalThreadId(threadId);
  const repository = readRepository(userId, canonicalId);
  const index = repository.messages.findIndex(({ message }) => message.id === item.message.id);
  if (index < 0) repository.messages.push(item);
  else repository.messages[index] = item;
  repository.headId = item.message.id;
  window.localStorage.setItem(MESSAGES_KEY(userId, canonicalId), JSON.stringify(repository));

  if (item.message.role === "user") {
    const threads = readThreads(userId);
    const thread = threads.find(({ remoteId }) => remoteId === canonicalId || remoteId === threadId);
    if (thread && !thread.title) {
      const title = item.message.content
        .flatMap((part) => (part.type === "text" ? [part.text] : []))
        .join(" ")
        .trim();
      if (title) {
        thread.title = title.length > 32 ? `${title.slice(0, 32)}…` : title;
        writeThreads(userId, threads);
      }
    }
  }
}

function LocalHistoryProvider({ children, userId }: PropsWithChildren<{ userId: string }>) {
  const aui = useAui();
  const history = useMemo<ThreadHistoryAdapter>(
    () => ({
      async load() {
        const { remoteId } = aui.threadListItem.getState();
        if (!remoteId) return { messages: [] };
        const canonicalId = canonicalThreadId(remoteId);
        if (!isServerThreadId(canonicalId)) {
          throw new Error("此本地会话未迁入新版本；请从服务端会话列表选择记录或新建对话。");
        }
        try {
          const response = await fetch(
            `/api/threads/${encodeURIComponent(canonicalId)}/history`,
            { cache: "no-store", credentials: "same-origin" },
          );
          if (response.status === 401) {
            clearLocalThreadCache(userId);
            window.location.assign("/login");
            return { messages: [] };
          }
          if (response.status === 404) {
            throw new Error("当前账号无法读取此数据源的会话历史；会话仍可在设置中管理或删除。");
          }
          if (response.status === 409) {
            const problem = await response.json().catch(() => null) as {
              code?: unknown;
              detail?: { code?: unknown };
            } | null;
            if ((problem?.code ?? problem?.detail?.code) === "THREAD_HISTORY_IMPORT_INCOMPLETE") {
              throw new Error("此会话的旧历史导入尚未完成，无法读取对话内容。请联系管理员处理。 ");
            }
          }
          if (!response.ok) {
            throw new Error("服务端会话暂不可用；为避免展示过期记录，已隐藏本地会话缓存。请重试读取。");
          }
          const payload = await response.json() as {
            turns?: Array<{
              turn_id?: unknown;
              sequence?: unknown;
              role?: unknown;
              content?: unknown;
              created_at?: unknown;
            }>;
          };
          const resultArtifacts = readThreadResultArtifacts(userId, canonicalId);
          const turns = (payload.turns ?? []).flatMap((turn) => {
            if (
              typeof turn.turn_id !== "string" || !Number.isSafeInteger(turn.sequence) ||
              (turn.role !== "user" && turn.role !== "assistant") ||
              typeof turn.content !== "string"
            ) return [];
            const artifact = turn.role === "assistant"
              ? formatQueryResults(resultArtifacts[turn.turn_id] ?? [])
              : "";
            return [{
              id: `server-${turn.turn_id}-${turn.sequence}`,
              role: turn.role as "user" | "assistant",
              content: [artifact, turn.content].filter(Boolean).join("\n\n"),
              createdAt: typeof turn.created_at === "string"
                ? new Date(turn.created_at)
                : new Date(),
            }];
          });
          const repository = ExportedMessageRepository.fromArray(turns);
          // Keep the local repository intact as a display/result-artifact cache, while
          // returning the server transcript as the authoritative natural-language history.
          return repository;
        } catch (error) {
          if (error instanceof Error && /本地会话未迁入|无法读取此数据源|旧历史导入/.test(error.message)) throw error;
          throw new Error("服务端会话暂不可用；为避免展示过期记录，已隐藏本地会话缓存。请重试读取。");
        }
      },
      async append(item) {
        const { remoteId } = await aui.threadListItem.initialize();
        upsertMessage(userId, canonicalThreadId(remoteId), item);
        renameFromFirstUserMessage(aui, item);
      },
      async update(item) {
        const { remoteId } = await aui.threadListItem.initialize();
        upsertMessage(userId, canonicalThreadId(remoteId), item);
        renameFromFirstUserMessage(aui, item);
      },
      async delete(items) {
        const { remoteId } = aui.threadListItem.getState();
        if (!remoteId) return;
        const canonicalId = canonicalThreadId(remoteId);
        const deletedIds = new Set(items.map(({ message }) => message.id));
        const repository = readRepository(userId, canonicalId);
        repository.messages = repository.messages.filter(
          ({ message }) => !deletedIds.has(message.id),
        );
        if (repository.headId && deletedIds.has(repository.headId)) {
          repository.headId = repository.messages.at(-1)?.message.id ?? null;
        }
        window.localStorage.setItem(MESSAGES_KEY(userId, canonicalId), JSON.stringify(repository));
      },
    }),
    [aui, userId],
  );

  return <RuntimeAdapterProvider adapters={{ history }}>{children}</RuntimeAdapterProvider>;
}

export function createLocalThreadListAdapter(userId: string): RemoteThreadListAdapter {
  const localToRemoteId = new Map<string, string>();
  function Provider({ children }: PropsWithChildren) {
    return <LocalHistoryProvider userId={userId}>{children}</LocalHistoryProvider>;
  }

  async function reconcileMissingThreadCaches(cachedThreads: StoredThread[], visibleIds: Set<string>) {
    const staleIds = cachedThreads
      .filter((thread) => isServerThreadId(thread.remoteId) && !visibleIds.has(thread.remoteId))
      .map((thread) => thread.remoteId);
    for (let offset = 0; offset < staleIds.length; offset += 200) {
      try {
        const states = await fetchThreadStates(staleIds.slice(offset, offset + 200));
        for (const state of states) {
          if (state.status === "deleted" || state.status === "expired") {
            removeLocalThread(userId, state.thread_id, state.thread_id);
          } else if (state.status === "archived") {
            updateThreadStatus(userId, state.thread_id, "archived");
          }
        }
      } catch {
        window.dispatchEvent(new CustomEvent("askdb:thread-list-error", {
          detail: "会话状态核对失败，已保留本地缓存。请稍后重试。",
        }));
        return;
      }
    }
  }

  return {
  async list(params?: { after?: string }) {
    initializeThreadCache(userId);
    const query = threadSearchQuery(userId).trim();
    if (!params?.after) {
      recentGroupByUser.delete(userId);
      publishRecentThreadPageState(userId, query, false);
    }
    let page: Awaited<ReturnType<typeof fetchThreadPage>>;
    try {
      page = await fetchThreadPage({
        view: "recent",
        q: query,
        limit: 50,
        ...(params?.after ? { cursor: params.after } : {}),
      });
    } catch (error) {
      if (error instanceof ThreadApiError && error.status === 401) {
        publishRecentThreadPageState(userId, query, false);
        clearLocalThreadCache(userId);
        window.location.assign("/login");
        return { threads: [] };
      }
      if (error instanceof ThreadApiError && error.code === "THREAD_CURSOR_STALE") {
        publishRecentThreadPageState(userId, query, false);
        window.dispatchEvent(new CustomEvent("askdb:thread-list-error", {
          detail: "会话列表已变化，请刷新后重新加载。",
        }));
        throw error;
      }
      publishRecentThreadPageState(userId, query, false);
      window.dispatchEvent(new CustomEvent("askdb:thread-list-error", {
        detail: "读取会话列表失败。请检查连接后重试刷新。",
      }));
      return { threads: [] };
    }

    publishRecentThreadPageState(userId, query, Boolean(page.next_cursor));

    const cachedThreads = readThreads(userId);
    const cachedById = new Map(cachedThreads.map((thread) => [thread.remoteId, thread]));
    const serverThreads = page.threads.map((metadata) =>
      storedThreadFromMetadata(metadata, cachedById.get(metadata.thread_id)),
    );
    const groupStates = getThreadGroupPageState(
      page.threads,
      params?.after ? recentGroupByUser.get(userId) : undefined,
    );
    const lastGroupState = groupStates.at(-1);
    if (lastGroupState) recentGroupByUser.set(userId, lastGroupState.groupKey);
    const mergedById = new Map(cachedThreads.map((thread) => [thread.remoteId, thread]));
    for (const thread of serverThreads) mergedById.set(thread.remoteId, thread);
    writeThreads(userId, [...mergedById.values()]);

    if (!params?.after) loadedRecentIdsByUser.set(userId, new Set());
    const visibleIds = loadedRecentIdsByUser.get(userId) ?? new Set<string>();
    for (const thread of serverThreads) visibleIds.add(thread.remoteId);
    if (page.next_cursor) {
      loadedRecentIdsByUser.set(userId, visibleIds);
    } else {
      loadedRecentIdsByUser.delete(userId);
      if (!query) void reconcileMissingThreadCaches(cachedThreads, visibleIds);
    }
    window.dispatchEvent(new Event("askdb:thread-metadata-updated"));
    return {
      threads: serverThreads.map((thread, index) =>
        toRemoteThread(thread, {
          showHeading: groupStates[index]?.showHeading === true,
          showDivider: groupStates[index]?.showDivider === true,
        }),
      ),
      ...(page.next_cursor ? { nextCursor: page.next_cursor } : {}),
    };
  },
  async initialize(threadId) {
    initializeThreadCache(userId);
    const threads = readThreads(userId);
    const persisted = threads.find(({ remoteId }) => remoteId === threadId);
    if (persisted) {
      if (!isServerThreadId(persisted.remoteId)) {
        throw new Error("此版本不导入仅保存在浏览器中的旧会话，请新建对话继续使用。");
      }
      return { remoteId: persisted.remoteId };
    }
    const knownRemoteId = localToRemoteId.get(threadId);
    if (knownRemoteId) return { remoteId: knownRemoteId };
    const key = draftThreadKey(userId, threadId);
    const preferences = draftThreadPreferences.get(key);
    const sourceId = preferences?.dataSourceId;
    if (!sourceId) throw new Error("请先为当前会话选择一个可用数据源。");
    const creationId = await creationKey(userId, `new:${threadId}`);
    const created = await postThread(sourceId, creationId, []);
    const remoteId = created.threadId;
    localToRemoteId.set(threadId, remoteId);
    if (created.metadata) {
      saveServerThreadMetadata(userId, created.metadata);
      if (preferences?.modelProfileId) {
        setThreadModelProfileId(userId, remoteId, preferences.modelProfileId);
      }
    }
    else {
      threads.unshift({ remoteId, status: "regular", ...preferences });
      writeThreads(userId, threads);
    }
    draftThreadPreferences.delete(key);
    return { remoteId };
  },
  async fetch(threadId) {
    const thread = getCachedThread(userId, threadId);
    if (!thread) throw new Error("找不到这条会话，请刷新后重试。");
    return toRemoteThread(thread);
  },
  async rename(remoteId, title) {
    const canonicalId = canonicalThreadId(remoteId);
    const thread = getCachedThread(userId, canonicalId);
    if (!thread || !thread.metadataRevision) {
      throw new Error("会话信息尚未同步到服务端，请刷新后重试。");
    }
    try {
      const updated = await patchThreadMetadata(canonicalId, thread.metadataRevision, {
        title: title.trim() || null,
      });
      saveServerThreadMetadata(userId, updated);
      window.dispatchEvent(new Event("askdb:thread-list-refresh-requested"));
    } catch (error) {
      if (error instanceof ThreadApiError && error.current) {
        saveServerThreadMetadata(userId, error.current);
      }
      throw error;
    }
  },
  async archive(remoteId) {
    const canonicalId = canonicalThreadId(remoteId);
    const thread = getCachedThread(userId, canonicalId);
    if (!thread || !thread.metadataRevision) {
      throw new Error("会话信息尚未同步到服务端，请刷新后重试。");
    }
    try {
      const updated = await archiveRemoteThread(canonicalId, thread.metadataRevision);
      saveServerThreadMetadata(userId, updated);
    } catch (error) {
      if (error instanceof ThreadApiError && error.current) {
        saveServerThreadMetadata(userId, error.current);
      }
      throw error;
    }
  },
  async unarchive(remoteId) {
    const canonicalId = canonicalThreadId(remoteId);
    const thread = getCachedThread(userId, canonicalId);
    if (!thread || !thread.metadataRevision) {
      throw new Error("会话信息尚未同步到服务端，请刷新后重试。");
    }
    try {
      const updated = await restoreRemoteThread(canonicalId, thread.metadataRevision);
      saveServerThreadMetadata(userId, updated);
    } catch (error) {
      if (error instanceof ThreadApiError && error.current) {
        saveServerThreadMetadata(userId, error.current);
      }
      throw error;
    }
  },
  async delete(remoteId) {
    const canonicalId = canonicalThreadId(remoteId);
    await requestThreadDeletionFromUI(userId, canonicalId, remoteId);
  },
  async generateTitle() {
    return new ReadableStream({
      start(controller) {
        controller.close();
      },
    });
  },
  unstable_Provider: Provider,
  };
}

function updateThreadStatus(userId: string, remoteId: string, status: StoredThread["status"]) {
  const canonicalId = canonicalThreadId(remoteId);
  const threads = readThreads(userId);
  const thread = threads.find((item) => item.remoteId === canonicalId || item.remoteId === remoteId);
  if (thread) {
    thread.status = status;
    writeThreads(userId, threads);
  }
}

function removeLocalThread(userId: string, canonicalId: string, originalId: string) {
  writeThreads(
    userId,
    readThreads(userId).filter(
      ({ remoteId }) => remoteId !== canonicalId && remoteId !== originalId,
    ),
  );
  window.localStorage.removeItem(MESSAGES_KEY(userId, canonicalId));
  window.localStorage.removeItem(RESULT_ARTIFACTS_KEY(userId, canonicalId));
  window.localStorage.removeItem(HISTORY_IMPORT_KEY(userId, canonicalId));
  if (originalId !== canonicalId) window.localStorage.removeItem(MESSAGES_KEY(userId, originalId));
  if (originalId !== canonicalId) window.localStorage.removeItem(RESULT_ARTIFACTS_KEY(userId, originalId));
  if (originalId !== canonicalId) window.localStorage.removeItem(HISTORY_IMPORT_KEY(userId, originalId));
  legacyThreadRedirects.delete(originalId);
}

function renameFromFirstUserMessage(
  aui: ReturnType<typeof useAui>,
  item: StoredRepository["messages"][number],
) {
  if (item.message.role !== "user" || aui.threadListItem.getState().title) return;
  const title = item.message.content
    .flatMap((part) => (part.type === "text" ? [part.text] : []))
    .join(" ")
    .trim();
  if (title) aui.threadListItem.rename(title.length > 32 ? `${title.slice(0, 32)}…` : title);
}
