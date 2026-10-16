import { proxyAgentJson } from "@/lib/agent-proxy";

export const runtime = "nodejs";

type Context = { params: Promise<{ userId: string }> };

export async function POST(request: Request, context: Context) {
  const { userId } = await context.params;
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(userId)) {
    return Response.json(
      { code: "ACCOUNT_NOT_FOUND", message: "找不到此账号。" },
      { status: 404, headers: { "cache-control": "no-store, max-age=0" } },
    );
  }
  return proxyAgentJson(
    request,
    `/v1/admin/users/${encodeURIComponent(userId)}/data-source-roles/preview`,
    { method: "POST", bodyLimit: 16 * 1024 },
  );
}
