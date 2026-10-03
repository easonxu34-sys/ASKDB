import { proxyAgentJson } from "@/lib/agent-proxy";

export const runtime = "nodejs";

const THREAD_CREATE_LIMIT = 64 * 1024;

export async function GET(request: Request) {
  return proxyAgentJson(request, "/v1/threads", { method: "GET" });
}

export async function POST(request: Request) {
  return proxyAgentJson(request, "/v1/threads", {
    method: "POST",
    bodyLimit: THREAD_CREATE_LIMIT,
    allowedBodyKeys: ["data_source_id", "creation_key", "initial_history", "history_import"],
  });
}
