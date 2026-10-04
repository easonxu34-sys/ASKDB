import { proxyAgentJson } from "@/lib/agent-proxy";

export const runtime = "nodejs";

type RouteContext = { params: Promise<{ threadId: string }> };

export async function POST(request: Request, { params }: RouteContext) {
  const { threadId } = await params;
  return proxyAgentJson(
    request,
    `/v1/threads/${encodeURIComponent(threadId)}/restore`,
    {
      method: "POST",
      bodyLimit: 1024,
      allowedBodyKeys: ["expected_metadata_revision"],
    },
  );
}
