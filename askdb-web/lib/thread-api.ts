import { authMutation } from "@/lib/auth-api";

export type ThreadView = "recent" | "archived";

export type ThreadMetadata = {
  thread_id: string;
  title: string | null;
  data_source_id: string;
  data_source_name: string | null;
  is_pinned: boolean;
  archived_at: string | null;
  last_user_turn_at: string;
  expires_at: string;
  retention_paused: boolean;
  retention_remaining_seconds: number | null;
  metadata_revision: number;
  history_import_pending: boolean;
};

export type ThreadPage = {
  threads: ThreadMetadata[];
  next_cursor: string | null;
  thread_list_revision: number;
};

export type ThreadState = {
  thread_id: string;
  status: "active" | "archived" | "deleted" | "expired" | "unavailable";
};

type ApiProblem = {
  code?: string;
  message?: string;
  detail?: { code?: string; message?: string; current?: ThreadMetadata };
  current?: ThreadMetadata;
};

export class ThreadApiError extends Error {
  readonly status: number;
  readonly code: string | undefined;
  readonly current: ThreadMetadata | undefined;

  constructor(message: string, status: number, code?: string, current?: ThreadMetadata) {
    super(message);
    this.name = "ThreadApiError";
    this.status = status;
    this.code = code;
    this.current = current;
  }
}

async function throwResponseError(response: Response, fallback: string): Promise<never> {
  const body = await response.json().catch(() => null) as ApiProblem | null;
  throw new ThreadApiError(
    body?.message ?? body?.detail?.message ?? fallback,
    response.status,
    body?.code ?? body?.detail?.code,
    body?.current ?? body?.detail?.current,
  );
}

export async function listThreads(options: {
  view: ThreadView;
  q?: string;
  limit?: number;
  cursor?: string;
}): Promise<ThreadPage> {
  const query = new URLSearchParams({
    view: options.view,
    limit: String(options.limit ?? 50),
  });
  if (options.q?.trim()) query.set("q", options.q.trim());
  if (options.cursor) query.set("cursor", options.cursor);
  const response = await fetch(`/api/threads?${query}`, {
    cache: "no-store",
    credentials: "same-origin",
  });
  if (!response.ok) await throwResponseError(response, "读取会话列表失败，请稍后重试。");
  const body = await response.json() as Partial<ThreadPage>;
  if (!Array.isArray(body.threads) || typeof body.thread_list_revision !== "number") {
    throw new Error("会话列表响应格式无效，请刷新后重试。");
  }
  return {
    threads: body.threads as ThreadMetadata[],
    next_cursor: typeof body.next_cursor === "string" ? body.next_cursor : null,
    thread_list_revision: body.thread_list_revision,
  };
}

async function mutateThread(
  path: string,
  method: "PATCH" | "POST",
  body: Record<string, unknown>,
): Promise<ThreadMetadata> {
  const response = await authMutation(path, method, body);
  if (!response.ok) await throwResponseError(response, "保存会话信息失败，请重试。");
  const value = await response.json() as ThreadMetadata;
  if (!value || typeof value.thread_id !== "string" || !Number.isSafeInteger(value.metadata_revision)) {
    throw new Error("会话信息响应格式无效，请刷新后重试。");
  }
  return value;
}

export function patchThreadMetadata(
  threadId: string,
  expectedMetadataRevision: number,
  patch: { title?: string | null; is_pinned?: boolean },
) {
  return mutateThread(`/api/threads/${encodeURIComponent(threadId)}`, "PATCH", {
    ...patch,
    expected_metadata_revision: expectedMetadataRevision,
  });
}

export function archiveThread(threadId: string, expectedMetadataRevision: number) {
  return mutateThread(
    `/api/threads/${encodeURIComponent(threadId)}/archive`,
    "POST",
    { expected_metadata_revision: expectedMetadataRevision },
  );
}

export function restoreThread(threadId: string, expectedMetadataRevision: number) {
  return mutateThread(
    `/api/threads/${encodeURIComponent(threadId)}/restore`,
    "POST",
    { expected_metadata_revision: expectedMetadataRevision },
  );
}

export async function fetchThreadStates(threadIds: string[]): Promise<ThreadState[]> {
  if (threadIds.length === 0) return [];
  const response = await authMutation("/api/threads/states", "POST", { thread_ids: threadIds });
  if (!response.ok) await throwResponseError(response, "核对会话状态失败，请重试。");
  const body = await response.json() as { states?: unknown };
  if (!Array.isArray(body.states)) throw new Error("会话状态响应格式无效。");
  return body.states.filter((item): item is ThreadState =>
    Boolean(item && typeof item === "object" &&
      typeof (item as ThreadState).thread_id === "string" &&
      ["active", "archived", "deleted", "expired", "unavailable"].includes((item as ThreadState).status)),
  );
}

export type ThreadDeletionImpact = {
  impact_version: string;
  linked_business_rules: { count: number; labels: string[] };
  linked_query_example_candidates: { count: number };
  published_query_examples_retained: boolean;
};

export async function getThreadDeletionImpact(
  threadId: string,
): Promise<ThreadDeletionImpact | null> {
  const response = await fetch(
    `/api/threads/${encodeURIComponent(threadId)}/deletion-impact`,
    { cache: "no-store", credentials: "same-origin" },
  );
  if (response.status === 404) return null;
  if (!response.ok) await throwResponseError(response, "读取删除影响失败，请稍后重试。");
  const value = await response.json() as Partial<ThreadDeletionImpact>;
  const linkedRules = value.linked_business_rules;
  const linkedExamples = value.linked_query_example_candidates;
  if (
    typeof value.impact_version !== "string" ||
    !linkedRules ||
    typeof linkedRules.count !== "number" ||
    !Array.isArray(linkedRules.labels) ||
    !linkedExamples ||
    typeof linkedExamples.count !== "number"
  ) {
    throw new Error("删除影响响应格式无效，请刷新后重试。");
  }
  return {
    impact_version: value.impact_version,
    linked_business_rules: {
      count: linkedRules.count,
      labels: linkedRules.labels.filter(
        (item): item is string => typeof item === "string",
      ),
    },
    linked_query_example_candidates: {
      count: linkedExamples.count,
    },
    published_query_examples_retained: value.published_query_examples_retained === true,
  };
}

export async function deleteThreadWithImpact(
  threadId: string,
  impactVersion: string,
  idempotencyKey: string,
): Promise<void> {
  const response = await authMutation(`/api/threads/${encodeURIComponent(threadId)}`, "DELETE", {
    impact_version: impactVersion,
    confirmed: true,
    idempotency_key: idempotencyKey,
  });
  if (response.ok || response.status === 404) return;
  await throwResponseError(response, "删除会话失败，请重试。");
}
