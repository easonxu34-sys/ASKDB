import { proxyAgentJson } from "@/lib/agent-proxy";

export const runtime = "nodejs";

export async function POST(request: Request) {
  return proxyAgentJson(request, "/v1/threads/states", {
    method: "POST",
    bodyLimit: 8 * 1024,
    allowedBodyKeys: ["thread_ids"],
  });
}
