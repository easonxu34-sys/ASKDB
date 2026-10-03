import { agentUrl, clearSessionCookie, requireAgentSession } from "@/lib/agent-proxy";
import { isAuthUser } from "@/lib/auth-api";

export const runtime = "nodejs";

const NO_STORE = {
  "cache-control": "no-store, max-age=0",
  pragma: "no-cache",
};

export async function GET(request: Request) {
  const access = await requireAgentSession(request);
  if ("response" in access) return access.response;

  let upstream: Response;
  try {
    upstream = await fetch(agentUrl("/v1/auth/me"), {
      method: "GET",
      headers: { authorization: `Bearer ${access.token}`, accept: "application/json" },
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return Response.json(
      { code: "AUTH_UNAVAILABLE", message: "登录服务暂时不可用，请稍后重试。" },
      { status: 503, headers: NO_STORE },
    );
  }

  let payload: unknown;
  try {
    payload = await upstream.json();
  } catch {
    return Response.json(
      { code: "AUTH_RESPONSE_INVALID", message: "登录服务返回了无效响应。" },
      { status: 502, headers: NO_STORE },
    );
  }
  if (!upstream.ok) {
    if (upstream.status === 401) await clearSessionCookie();
    const body = payload as Record<string, unknown>;
    const detail = body?.detail;
    const nested = detail && typeof detail === "object" ? detail as Record<string, unknown> : null;
    return Response.json(
      {
        code: typeof nested?.code === "string" ? nested.code : "AUTH_REQUIRED",
        message: typeof nested?.message === "string" ? nested.message : "登录状态已失效，请重新登录。",
      },
      { status: upstream.status, headers: NO_STORE },
    );
  }
  if (!isAuthUser(payload)) {
    return Response.json(
      { code: "AUTH_RESPONSE_INVALID", message: "登录服务返回了无效响应。" },
      { status: 502, headers: NO_STORE },
    );
  }
  return Response.json(payload, { status: upstream.status, headers: NO_STORE });
}
