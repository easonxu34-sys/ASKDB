import { proxyAgentJson } from "@/lib/agent-proxy";

export const runtime = "nodejs";

type Context = { params: Promise<{ roleId: string }> };

async function proxy(request: Request, context: Context) {
  const { roleId } = await context.params;
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(roleId)) {
    return Response.json(
      { code: "DATA_SOURCE_ROLE_NOT_FOUND", message: "找不到数据源角色。" },
      { status: 404, headers: { "cache-control": "no-store, max-age=0" } },
    );
  }
  return proxyAgentJson(request, `/v1/admin/data-source-roles/${encodeURIComponent(roleId)}`, {
    method: request.method,
    bodyLimit: 16 * 1024,
  });
}

export const GET = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
