import { proxyAgentDownload } from "@/lib/agent-proxy";
import { effectivePermissionAgentPath } from "@/lib/effective-permissions";

export const runtime = "nodejs";

export async function GET(request: Request) {
  try {
    return proxyAgentDownload(request, effectivePermissionAgentPath(new URL(request.url), true));
  } catch {
    return Response.json(
      { detail: { code: "REQUEST_INVALID", message: "导出参数无效。" } },
      { status: 400, headers: { "cache-control": "no-store, max-age=0", pragma: "no-cache" } },
    );
  }
}
