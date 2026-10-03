import { proxyAgentJson } from "@/lib/agent-proxy";

export const runtime = "nodejs";

export async function GET(request: Request) {
  return proxyAgentJson(request, "/v1/admin/users", { method: "GET" });
}

export async function POST(request: Request) {
  return proxyAgentJson(request, "/v1/admin/users", { method: "POST", bodyLimit: 16 * 1024 });
}
