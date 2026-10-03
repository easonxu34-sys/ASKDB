import { sanitizeChatRequest } from "@/lib/chat-request";
import { agentUrl, clearSessionCookie, requireAgentSession } from "@/lib/agent-proxy";

export const runtime = "nodejs";

const MAX_REQUEST_BYTES = 64 * 1024;
const NO_STORE = { "cache-control": "no-store, max-age=0" };

const safeAgentMessages: Record<string, string> = {
  AUTH_REQUIRED: "登录状态已失效，请重新登录。",
  INVALID_SESSION: "登录状态已失效，请重新登录。",
  PASSWORD_CHANGE_REQUIRED: "请先修改临时密码。",
  CHAT_THREAD_LEGACY_REQUIRES_NEW_THREAD: "旧匿名会话不能继续使用，请新建会话。",
  CHAT_THREAD_NOT_FOUND: "找不到此会话。",
  DATA_SOURCE_REQUIRED: "请选择当前账号可用的数据源。",
  DATA_SOURCE_NOT_FOUND: "找不到所选数据源。",
  DATA_SOURCE_UNAVAILABLE: "所选数据源当前不可用。",
  CHAT_DATA_SOURCE_MISMATCH: "此会话已绑定其他数据源，请新建会话后切换。",
  THREAD_MEMORY_DISABLED: "服务端会话记忆尚未完成启动恢复。",
  THREAD_MEMORY_UNAVAILABLE: "会话记忆服务当前不可用，请稍后重试。",
  THREAD_TURN_CONFLICT: "会话状态已变化，请刷新后重试。",
  THREAD_TURN_RUNNING: "此轮回答仍在生成，请稍后重连获取结果。",
  THREAD_TURN_FAILED: "此轮回答未完成，请使用新消息重试。",
  MODEL_CONTEXT_BUDGET_REQUIRED: "请在模型配置中补充上下文窗口、输出预留和 tokenizer。",
  MODEL_TOKENIZER_UNAVAILABLE: "模型 tokenizer 当前不可用，请稍后重试。",
  CHAT_CONTEXT_TOO_LARGE: "当前问题和系统上下文超过此模型的输入预算。",
  MODEL_NOT_CONFIGURED: "请先完成模型设置。",
  MODEL_PROFILE_NOT_FOUND: "找不到所选模型配置，请重新选择。",
};

function safeErrorResponse(status: number, value: unknown) {
  const body = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const detail = body.detail && typeof body.detail === "object"
    ? body.detail as Record<string, unknown>
    : {};
  const code = typeof body.code === "string" ? body.code :
    typeof detail.code === "string" ? detail.code : "CHAT_REQUEST_FAILED";
  return Response.json({
    code,
    message: safeAgentMessages[code] ?? "AskDB 智能助手请求失败，请稍后重试。",
  }, { status, headers: { ...NO_STORE, "content-type": "application/json; charset=utf-8" } });
}

export async function POST(request: Request) {
  const access = await requireAgentSession(request, true);
  if ("response" in access) return access.response;

  const reader = request.body?.getReader();
  if (!reader) return Response.json({ code: "CHAT_REQUEST_INVALID", message: "请求内容不能为空。" }, { status: 400, headers: NO_STORE });

  const decoder = new TextDecoder();
  let body = "";
  let receivedBytes = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    receivedBytes += value.byteLength;
    if (receivedBytes > MAX_REQUEST_BYTES) {
      await reader.cancel();
      return Response.json({ code: "CHAT_REQUEST_INVALID", message: "请求内容过大。" }, { status: 413, headers: NO_STORE });
    }
    body += decoder.decode(value, { stream: true });
  }
  body += decoder.decode();

  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return Response.json({ code: "CHAT_REQUEST_INVALID", message: "请求格式无效。" }, { status: 400, headers: NO_STORE });
  }

  const chatRequest = sanitizeChatRequest(parsed);
  if (!chatRequest) {
    return Response.json({ code: "CHAT_REQUEST_INVALID", message: "聊天请求格式无效。" }, { status: 400, headers: NO_STORE });
  }

  let upstream: Response;
  try {
    upstream = await fetch(agentUrl("/v1/chat"), {
      method: "POST",
      headers: {
        authorization: `Bearer ${access.token}`,
        "content-type": "application/json",
        accept: "text/event-stream",
      },
      body: JSON.stringify(chatRequest),
      signal: request.signal,
      cache: "no-store",
    });
  } catch {
    return Response.json({ code: "AGENT_UNAVAILABLE", message: "AskDB 智能助手暂时不可用。" }, { status: 503, headers: NO_STORE });
  }

  if (!upstream.ok || !upstream.body) {
    const detail = await upstream.text();
    if (upstream.status === 401) await clearSessionCookie();
    let payload: unknown = null;
    try { payload = detail ? JSON.parse(detail) as unknown : null; } catch { /* Use safe fallback. */ }
    return safeErrorResponse(upstream.ok ? 502 : upstream.status, payload);
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-store, no-cache, no-transform",
      connection: "keep-alive",
    },
  });
}
