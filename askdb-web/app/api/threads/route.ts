import { proxyAgentJson } from "@/lib/agent-proxy";

export const runtime = "nodejs";

const THREAD_CREATE_LIMIT = 64 * 1024;

export async function GET(request: Request) {
  const source = new URL(request.url).searchParams;
  const forwarded = new URLSearchParams();
  for (const key of ["view", "q", "limit", "cursor"] as const) {
    const value = source.get(key);
    if (value !== null) forwarded.set(key, value);
  }
  const suffix = forwarded.size > 0 ? `?${forwarded.toString()}` : "";
  return proxyAgentJson(request, `/v1/threads${suffix}`, { method: "GET" });
}

export async function POST(request: Request) {
  return proxyAgentJson(request, "/v1/threads", {
    method: "POST",
    bodyLimit: THREAD_CREATE_LIMIT,
    allowedBodyKeys: ["data_source_id", "creation_key", "initial_history", "history_import"],
  });
}
