import { authMutation, responseError } from "@/lib/auth-api";

export type QueryExampleCandidate = {
  query_example_id: string;
  data_source_id: string;
  normalized_question: string | null;
  sql_template: string | null;
  parameter_specs: Array<{ name: string; value_type: string; nullable: boolean }>;
  submitted_by: string;
  review_status: string;
  publication_status: string;
  review_reason_code: string | null;
  version: number;
  created_at: string;
  expires_at: string;
};

export type BusinessRuleCandidate = {
  business_rule_id: string;
  data_source_id: string;
  term: string | null;
  definition: string | null;
  mdl_references: string[];
  review_status: string;
  publication_status: string;
  has_exact_term_conflict: boolean;
  clarification_question: string | null;
  submitted_by: string;
  review_reason_code: string | null;
  version: number;
  created_at: string;
  expires_at: string;
};

export type QueryCorpusRevision = {
  data_source_id: string;
  corpus_revision: number;
  status: string;
  content_hash: string;
  record_ids: string[];
  created_at: string;
  activated_at: string | null;
};

async function requestJson<T>(path: string, method: "GET" | "POST", body?: unknown): Promise<T> {
  const response = method === "GET"
    ? await fetch(path, { cache: "no-store", credentials: "same-origin" })
    : await authMutation(path, "POST", body);
  if (!response.ok) throw new Error(await responseError(response, "记忆服务暂时不可用，请稍后重试。"));
  return await response.json() as T;
}

function sourceQuery(sourceId: string, extra: Record<string, string> = {}) {
  const params = new URLSearchParams({ data_source_id: sourceId, ...extra });
  return params.toString();
}

export function listQueryExamples(sourceId: string, cursor?: string) {
  const extra = { limit: "100", ...(cursor ? { cursor } : {}) };
  return requestJson<{ candidates: QueryExampleCandidate[]; next_cursor: string | null }>(
    `/api/memories/query-examples/candidates?${sourceQuery(sourceId, extra)}`,
    "GET",
  );
}

export function listBusinessRules(sourceId: string) {
  return requestJson<{ candidates: BusinessRuleCandidate[] }>(
    `/api/memories/business-rules/candidates?${sourceQuery(sourceId, { limit: "100" })}`,
    "GET",
  );
}

export function listQueryCorpusRevisions(sourceId: string) {
  return requestJson<{ revisions: QueryCorpusRevision[] }>(
    `/api/memories/query-examples/corpus-revisions?${sourceQuery(sourceId, { limit: "30" })}`,
    "GET",
  );
}

export function submitQueryExample(input: {
  thread_id: string;
  source_turn_key: string;
  idempotency_key: string;
  question: string;
  sql_template: string;
  parameter_specs: Array<{ name: string; value_type: string; nullable: boolean }>;
}) {
  return requestJson<{ candidate: QueryExampleCandidate }>(
    "/api/memories/query-examples/candidates", "POST", input,
  );
}

export function submitBusinessRule(input: {
  data_source_id: string;
  thread_id: string;
  idempotency_key: string;
  term: string;
  definition: string;
  mdl_references: string[];
}) {
  return requestJson<{ candidate: BusinessRuleCandidate }>(
    "/api/memories/business-rules/candidates", "POST", input,
  );
}

export function queryExampleAction(
  id: string,
  action: "approve" | "revalidate" | "reject" | "withdraw" | "revoke",
  body: Record<string, unknown>,
) {
  return requestJson<{ candidate?: QueryExampleCandidate; prepared_corpus_revision?: QueryCorpusRevision }>(
    `/api/memories/query-examples/${encodeURIComponent(id)}/${action}`, "POST", body,
  );
}

export function businessRuleAction(
  id: string,
  action:
    | "clarification-request"
    | "clarification-response"
    | "approve"
    | "publish"
    | "reject"
    | "withdraw"
    | "revoke",
  body: Record<string, unknown>,
) {
  return requestJson<{ candidate?: BusinessRuleCandidate; operation?: unknown }>(
    `/api/memories/business-rules/candidates/${encodeURIComponent(id)}/${action}`, "POST", body,
  );
}

export function activateQueryCorpus(sourceId: string, revision: number) {
  return requestJson<{ revision: QueryCorpusRevision }>(
    `/api/memories/query-corpus/${revision}/activate?${sourceQuery(sourceId)}`, "POST", {},
  );
}

export function getMemoryOperation(id: string) {
  return requestJson<Record<string, unknown>>(
    `/api/memories/operations/${encodeURIComponent(id)}`, "GET",
  );
}

export function createIdempotencyKey() {
  return crypto.randomUUID().replaceAll("-", "");
}
