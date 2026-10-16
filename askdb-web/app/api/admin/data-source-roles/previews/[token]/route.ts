import { proxyAgentJson } from "@/lib/agent-proxy";

export const runtime = "nodejs";

type Context = { params: Promise<{ token: string }> };

export async function GET(request: Request, context: Context) {
  const { token } = await context.params;
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(token)) {
    return Response.json(
      { code: "DATA_SOURCE_ROLE_PREVIEW_STALE", message: "影响预览已过期。" },
      { status: 409, headers: { "cache-control": "no-store, max-age=0" } },
    );
  }
  const incoming = new URL(request.url).searchParams;
  const page = incoming.get("page") ?? "1";
  const pageSize = incoming.get("page_size") ?? "50";
  if (!/^\d{1,5}$/.test(page) || !/^\d{1,3}$/.test(pageSize)) {
    return Response.json(
      { code: "REQUEST_INVALID", message: "分页参数无效。" },
      { status: 422, headers: { "cache-control": "no-store, max-age=0" } },
    );
  }
  return proxyAgentJson(
    request,
    `/v1/admin/data-source-roles/previews/${encodeURIComponent(token)}?page=${page}&page_size=${pageSize}`,
    { method: "GET" },
  );
}
