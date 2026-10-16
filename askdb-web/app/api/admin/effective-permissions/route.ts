import { proxyAgentJson } from "@/lib/agent-proxy";
import { effectivePermissionAgentPath } from "@/lib/effective-permissions";

export const runtime = "nodejs";

export async function GET(request: Request) {
  try {
    return proxyAgentJson(request, effectivePermissionAgentPath(new URL(request.url)), {
      method: "GET",
    });
  } catch {
    return Response.json(
      { detail: { code: "REQUEST_INVALID", message: "查询参数无效。" } },
      { status: 400, headers: { "cache-control": "no-store, max-age=0", pragma: "no-cache" } },
    );
  }
}
