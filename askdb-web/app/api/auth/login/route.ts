import { agentUrl, requireCsrf, setSessionCookie } from "@/lib/agent-proxy";
import { isAuthUser } from "@/lib/auth-api";

export const runtime = "nodejs";

const NO_STORE = {
  "cache-control": "no-store, max-age=0",
  pragma: "no-cache",
};

function errorResponse(code: string, message: string, status: number) {
  return Response.json({ code, message }, { status, headers: NO_STORE });
}

export async function POST(request: Request) {
  const csrfError = requireCsrf(request);
  if (csrfError) return csrfError;

  const declared = Number(request.headers.get("content-length") ?? 0);
  if (declared > 16 * 1024) return errorResponse("LOGIN_INVALID", "登录信息格式无效。", 413);
  const raw = await request.text();
  if (new TextEncoder().encode(raw).byteLength > 16 * 1024) {
    return errorResponse("LOGIN_INVALID", "登录信息格式无效。", 413);
  }

  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return errorResponse("LOGIN_INVALID", "登录信息格式无效。", 400);
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return errorResponse("LOGIN_INVALID", "登录信息格式无效。", 400);
  }
  const input = value as Record<string, unknown>;
  if (
    Object.keys(input).some((key) => key !== "username" && key !== "password") ||
    typeof input.username !== "string" || typeof input.password !== "string" ||
    input.username.length < 1 || input.username.length > 128 ||
    input.password.length < 1 || input.password.length > 1024
  ) {
    return errorResponse("LOGIN_INVALID", "登录信息格式无效。", 400);
  }

  let upstream: Response;
  try {
    upstream = await fetch(agentUrl("/v1/auth/login"), {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({ username: input.username, password: input.password }),
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return errorResponse("AUTH_UNAVAILABLE", "登录服务暂时不可用，请稍后重试。", 503);
  }

  let payload: unknown;
  try {
    const text = await upstream.text();
    if (new TextEncoder().encode(text).byteLength > 16 * 1024) throw new Error("too large");
    payload = JSON.parse(text);
  } catch {
    return errorResponse("AUTH_RESPONSE_INVALID", "登录服务返回了无效响应。", 502);
  }
  if (!upstream.ok) {
    const body = payload as Record<string, unknown>;
    const detail = body?.detail;
    const nested = detail && typeof detail === "object" ? detail as Record<string, unknown> : null;
    const code = typeof body?.code === "string" ? body.code :
      typeof nested?.code === "string" ? nested.code : "INVALID_CREDENTIALS";
    const message = typeof body?.message === "string" ? body.message :
      typeof nested?.message === "string" ? nested.message : "用户名或密码错误。";
    return errorResponse(code, message, upstream.status);
  }

  if (!payload || typeof payload !== "object") {
    return errorResponse("AUTH_RESPONSE_INVALID", "登录服务返回了无效响应。", 502);
  }
  const result = payload as Record<string, unknown>;
  if (
    typeof result.session_token !== "string" || result.session_token.length < 40 ||
    result.session_token.length > 512 || !isAuthUser(result.user)
  ) {
    return errorResponse("AUTH_RESPONSE_INVALID", "登录服务返回了无效响应。", 502);
  }

  await setSessionCookie(result.session_token);
  return Response.json({ user: result.user }, { status: upstream.status, headers: NO_STORE });
}
