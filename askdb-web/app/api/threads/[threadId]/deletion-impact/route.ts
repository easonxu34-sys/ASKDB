import { proxyAgentJson } from "@/lib/agent-proxy";

export const runtime = "nodejs";

type RouteContext = { params: Promise<{ threadId: string }> };

export async function GET(request: Request, { params }: RouteContext) {
  const { threadId } = await params;
  return proxyAgentJson(
    request,
    `/v1/threads/${encodeURIComponent(threadId)}/deletion-impact`,
    { method: "GET" },
  );
}
