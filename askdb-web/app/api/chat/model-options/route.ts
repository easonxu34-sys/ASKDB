import { agentUrl, clearSessionCookie, requireAgentSession } from "@/lib/agent-proxy";

export const runtime = "nodejs";

const NO_STORE = { "cache-control": "no-store, max-age=0", pragma: "no-cache" };

export async function GET(request: Request) {
  const access = await requireAgentSession(request);
  if ("response" in access) return access.response;

  let upstream: Response;
  try {
    upstream = await fetch(agentUrl("/v1/chat/model-options"), {
      headers: { authorization: `Bearer ${access.token}`, accept: "application/json" },
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return Response.json({ code: "MODEL_OPTIONS_UNAVAILABLE", message: "模型列表暂时不可用。" }, {
      status: 503,
      headers: NO_STORE,
    });
  }
  if (upstream.status === 401) await clearSessionCookie();
  let value: unknown;
  try {
    value = await upstream.json();
  } catch {
    return Response.json({ code: "MODEL_OPTIONS_UNAVAILABLE", message: "模型列表响应无效。" }, {
      status: 502,
      headers: NO_STORE,
    });
  }
  if (upstream.status === 401) {
    return Response.json({ code: "AUTH_REQUIRED", message: "登录状态已失效，请重新登录。" }, {
      status: 401,
      headers: NO_STORE,
    });
  }
  if (upstream.status === 403) {
    const detail = value && typeof value === "object" ? (value as Record<string, unknown>).detail : null;
    const body = detail && typeof detail === "object" ? detail as Record<string, unknown> : {};
    return Response.json({
      code: typeof body.code === "string" ? body.code : "PASSWORD_CHANGE_REQUIRED",
      message: typeof body.message === "string" ? body.message : "请先修改临时密码。",
    }, { status: 403, headers: NO_STORE });
  }
  if (!upstream.ok || !value || typeof value !== "object") {
    return Response.json({ code: "MODEL_OPTIONS_UNAVAILABLE", message: "模型列表暂时不可用。" }, {
      status: upstream.ok ? 502 : upstream.status,
      headers: NO_STORE,
    });
  }
  const catalog = value as Record<string, unknown>;
  if (!(catalog.default_profile_id === null || typeof catalog.default_profile_id === "string") ||
    !Array.isArray(catalog.profiles)) {
    return Response.json({ code: "MODEL_OPTIONS_UNAVAILABLE", message: "模型列表响应无效。" }, {
      status: 502,
      headers: NO_STORE,
    });
  }
  const profiles = catalog.profiles.flatMap((profile) => {
    if (!profile || typeof profile !== "object") return [];
    const item = profile as Record<string, unknown>;
    return typeof item.id === "string" && typeof item.name === "string" &&
      typeof item.model === "string" && typeof item.available === "boolean"
      ? [{ id: item.id, name: item.name, model: item.model, available: item.available }]
      : [];
  });
  return Response.json({ default_profile_id: catalog.default_profile_id, profiles }, {
    status: upstream.status,
    headers: NO_STORE,
  });
}
