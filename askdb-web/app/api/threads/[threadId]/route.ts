import { proxyAgentJson } from "@/lib/agent-proxy";

export const runtime = "nodejs";

type RouteContext = { params: Promise<{ threadId: string }> };

export async function PATCH(request: Request, { params }: RouteContext) {
  const { threadId } = await params;
  return proxyAgentJson(
    request,
    `/v1/threads/${encodeURIComponent(threadId)}`,
    {
      method: "PATCH",
      bodyLimit: 8 * 1024,
      allowedBodyKeys: ["title", "is_pinned", "expected_metadata_revision"],
    },
  );
}

export async function DELETE(request: Request, { params }: RouteContext) {
  const { threadId } = await params;
  return proxyAgentJson(
    request,
    `/v1/threads/${encodeURIComponent(threadId)}`,
    {
      method: "DELETE",
      bodyLimit: 8 * 1024,
      allowedBodyKeys: ["impact_version", "confirmed", "idempotency_key"],
    },
  );
}
