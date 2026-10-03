import { agentUrl, clearSessionCookie, requireAgentSession } from "@/lib/agent-proxy";

export const runtime = "nodejs";

const NO_STORE = {
  "cache-control": "no-store, max-age=0",
  pragma: "no-cache",
};

export async function POST(request: Request) {
  const access = await requireAgentSession(request, true);
  if ("response" in access) return access.response;

  let upstream: Response;
  try {
    upstream = await fetch(agentUrl("/v1/auth/logout"), {
      method: "POST",
      headers: { authorization: `Bearer ${access.token}`, accept: "application/json" },
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return Response.json(
      { code: "AUTH_UNAVAILABLE", message: "退出服务暂时不可用，请稍后重试。" },
      { status: 503, headers: NO_STORE },
    );
  }

  if (upstream.ok || upstream.status === 401) {
    await clearSessionCookie();
    return Response.json({ ok: true }, { status: 200, headers: NO_STORE });
  }
  return Response.json(
    { code: "LOGOUT_FAILED", message: "退出登录失败，请稍后重试。" },
    { status: upstream.status, headers: NO_STORE },
  );
}
