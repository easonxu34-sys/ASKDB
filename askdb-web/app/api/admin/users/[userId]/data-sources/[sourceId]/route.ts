import { proxyAgentJson } from "@/lib/agent-proxy";

export const runtime = "nodejs";

type RouteContext = { params: Promise<{ userId: string; sourceId: string }> };

async function proxy(request: Request, context: RouteContext) {
  const { userId, sourceId } = await context.params;
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(userId) || !/^[A-Za-z0-9_-]{1,128}$/.test(sourceId)) {
    return Response.json({ code: "DATA_SOURCE_NOT_FOUND", message: "找不到此数据源。" }, {
      status: 404,
      headers: { "cache-control": "no-store, max-age=0" },
    });
  }
  return proxyAgentJson(
    request,
    `/v1/admin/users/${encodeURIComponent(userId)}/data-sources/${encodeURIComponent(sourceId)}`,
    { method: request.method, bodyLimit: 1024 },
  );
}

export const PUT = proxy;
export const DELETE = proxy;
