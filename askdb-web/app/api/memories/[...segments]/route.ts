import { proxyAgentJson } from "@/lib/agent-proxy";

export const runtime = "nodejs";

const ID = /^(?:[a-f0-9]{32}|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})$/i;
const SOURCE_ID = /^[A-Za-z0-9_-]{1,128}$/;
const OPERATION_ID = /^[A-Za-z0-9_-]{8,128}$/;
const NO_BODY: readonly string[] = [];

type RouteContext = { params: Promise<{ segments: string[] }> };

export async function GET(request: Request, context: RouteContext) {
  return handle(request, context, "GET");
}

export async function POST(request: Request, context: RouteContext) {
  return handle(request, context, "POST");
}

async function handle(request: Request, context: RouteContext, method: "GET" | "POST") {
  const { segments } = await context.params;
  const parsed = parseRoute(segments, method, new URL(request.url).searchParams);
  if (!parsed) return Response.json({ code: "NOT_FOUND", message: "找不到此记忆操作。" }, { status: 404 });
  return proxyAgentJson(request, parsed.path, {
    method,
    ...(parsed.bodyKeys ? { allowedBodyKeys: parsed.bodyKeys, bodyLimit: 64 * 1024 } : {}),
  });
}

function parseRoute(
  segments: string[],
  method: "GET" | "POST",
  input: URLSearchParams,
): { path: string; bodyKeys?: readonly string[] } | undefined {
  const query = readQuery(input, ["data_source_id", "review_status", "status", "limit", "cursor"]);
  if (!query) return undefined;

  if (segments[0] === "query-examples" && segments[1] === "candidates") {
    if (segments.length === 2 && method === "GET") {
      if (!query.get("data_source_id") || !only(query, ["data_source_id", "review_status", "limit", "cursor"])) return undefined;
      return { path: `/v1/query-examples/candidates${toSearch(query)}` };
    }
    if (segments.length === 2 && method === "POST") {
      if (query.toString()) return undefined;
      return {
        path: "/v1/query-examples/candidates",
        bodyKeys: ["thread_id", "source_turn_key", "idempotency_key", "question", "sql_template", "parameter_specs"],
      };
    }
  }

  if (segments[0] === "query-examples" && segments[1] === "corpus-revisions" && segments.length === 2 && method === "GET") {
    if (!query.get("data_source_id") || !only(query, ["data_source_id", "limit"])) return undefined;
    return { path: `/v1/query-examples/corpus-revisions${toSearch(query)}` };
  }

  if (segments[0] === "business-rules" && segments[1] === "candidates") {
    if (segments.length === 2 && method === "GET") {
      if (!query.get("data_source_id") || !only(query, ["data_source_id", "status", "limit"])) return undefined;
      return { path: `/v1/business-rules/candidates${toSearch(query)}` };
    }
    if (segments.length === 2 && method === "POST") {
      if (query.toString()) return undefined;
      return {
        path: "/v1/business-rules/candidates",
        bodyKeys: ["data_source_id", "thread_id", "idempotency_key", "term", "definition", "mdl_references"],
      };
    }
    const candidateId = segments[2];
    const action = segments[3];
    if (segments.length === 4 && candidateId && ID.test(candidateId) && method === "POST") {
      const actions: Record<string, readonly string[]> = {
        "clarification-request": ["expected_version", "question"],
        "clarification-response": ["expected_version", "term", "definition", "mdl_references"],
        approve: ["expected_version"],
        publish: ["expected_version"],
        reject: ["expected_version", "reason_code"],
        withdraw: ["expected_version"],
        revoke: ["idempotency_key"],
      };
      const bodyKeys = actions[action ?? ""];
      if (!bodyKeys || query.toString()) return undefined;
      return { path: `/v1/business-rules/candidates/${candidateId}/${action}`, bodyKeys };
    }
  }

  if (segments[0] === "query-examples" && segments[1] && ID.test(segments[1]) && segments.length === 3 && method === "POST") {
    const action = segments[2];
    const actions: Record<string, readonly string[]> = {
      approve: ["expected_version"],
      revalidate: ["expected_version"],
      reject: ["expected_version", "reason_code"],
      withdraw: ["expected_version"],
      revoke: ["idempotency_key"],
    };
    const bodyKeys = actions[action];
    if (!bodyKeys || query.toString()) return undefined;
    return { path: `/v1/query-examples/${segments[1]}/${action}`, bodyKeys };
  }

  if (segments[0] === "query-corpus" && segments.length === 3 && segments[2] === "activate" && method === "POST") {
    const revision = segments[1];
    const sourceId = query.get("data_source_id");
    if (!revision || !/^\d{1,12}$/.test(revision) || !sourceId || !SOURCE_ID.test(sourceId) || !only(query, ["data_source_id"])) return undefined;
    return { path: `/v1/data-sources/${encodeURIComponent(sourceId)}/query-corpus-revisions/${revision}/activate`, bodyKeys: NO_BODY };
  }

  if (segments[0] === "operations" && segments.length === 2 && method === "GET" && OPERATION_ID.test(segments[1])) {
    if (query.toString()) return undefined;
    return { path: `/v1/settings/wren/operations/${segments[1]}` };
  }

  return undefined;
}

function readQuery(input: URLSearchParams, allowedKeys: readonly string[]): URLSearchParams | undefined {
  const result = new URLSearchParams();
  for (const key of new Set(input.keys())) {
    if (!allowedKeys.includes(key) || input.getAll(key).length !== 1) return undefined;
    const value = input.get(key);
    if (value === null || value.length > 1024) return undefined;
    result.set(key, value);
  }
  const source = result.get("data_source_id");
  if (source && !SOURCE_ID.test(source)) return undefined;
  const limit = result.get("limit");
  if (limit && (!/^\d{1,3}$/.test(limit) || Number(limit) < 1 || Number(limit) > 100)) return undefined;
  return result;
}

function only(query: URLSearchParams, allowed: readonly string[]) {
  return [...query.keys()].every((key) => allowed.includes(key));
}

function toSearch(query: URLSearchParams) {
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}
