import { agentUrl, clearSessionCookie, requireAgentSession, setSessionCookie } from "@/lib/agent-proxy";
import { isAuthUser } from "@/lib/auth-api";

export const runtime = "nodejs";

const NO_STORE = { "cache-control": "no-store, max-age=0", pragma: "no-cache" };

function errorResponse(code: string, message: string, status: number) {
  return Response.json({ code, message }, { status, headers: NO_STORE });
}

export async function POST(request: Request) {
  const access = await requireAgentSession(request, true);
  if ("response" in access) return access.response;

  const declared = Number(request.headers.get("content-length") ?? 0);
  if (declared > 16 * 1024) return errorResponse("PASSWORD_CHANGE_INVALID", "密码信息格式无效。", 413);
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > 16 * 1024) {
    return errorResponse("PASSWORD_CHANGE_INVALID", "密码信息格式无效。", 413);
  }

  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return errorResponse("PASSWORD_CHANGE_INVALID", "密码信息格式无效。", 400);
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return errorResponse("PASSWORD_CHANGE_INVALID", "密码信息格式无效。", 400);
  }
  const input = value as Record<string, unknown>;
  if (
    Object.keys(input).some((key) => key !== "current_password" && key !== "new_password") ||
    typeof input.current_password !== "string" || typeof input.new_password !== "string" ||
    input.current_password.length > 1024 || input.new_password.length > 1024
  ) return errorResponse("PASSWORD_CHANGE_INVALID", "密码信息格式无效。", 400);

  let upstream: Response;
  try {
    upstream = await fetch(agentUrl("/v1/auth/change-password"), {
      method: "POST",
      headers: {
        authorization: `Bearer ${access.token}`,
        "content-type": "application/json",
        accept: "application/json",
      },
      body: JSON.stringify(input),
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return errorResponse("AUTH_UNAVAILABLE", "密码修改服务暂时不可用，请稍后重试。", 503);
  }

  let payload: unknown;
  try {
    const text = await upstream.text();
    if (new TextEncoder().encode(text).byteLength > 16 * 1024) throw new Error("too large");
    payload = JSON.parse(text);
  } catch {
    return errorResponse("AUTH_RESPONSE_INVALID", "密码修改服务返回了无效响应。", 502);
  }
  if (!upstream.ok) {
    if (upstream.status === 401) await clearSessionCookie();
    const body = payload as Record<string, unknown>;
    const detail = body?.detail;
    const nested = detail && typeof detail === "object" ? detail as Record<string, unknown> : null;
    return errorResponse(
      typeof nested?.code === "string" ? nested.code : "PASSWORD_CHANGE_FAILED",
      typeof nested?.message === "string" ? nested.message : "密码修改失败，请检查当前密码。",
      upstream.status,
    );
  }
  if (!payload || typeof payload !== "object") {
    return errorResponse("AUTH_RESPONSE_INVALID", "密码修改服务返回了无效响应。", 502);
  }
  const result = payload as Record<string, unknown>;
  if (typeof result.session_token !== "string" || result.session_token.length < 40 ||
    result.session_token.length > 512 || !isAuthUser(result.user)) {
    return errorResponse("AUTH_RESPONSE_INVALID", "密码修改服务返回了无效响应。", 502);
  }
  await setSessionCookie(result.session_token);
  return Response.json({ user: result.user }, { status: upstream.status, headers: NO_STORE });
}
