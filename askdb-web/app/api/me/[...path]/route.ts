import { agentUrl, clearSessionCookie, requireAgentSession } from "@/lib/agent-proxy";

export const runtime = "nodejs";
const headers = { "cache-control": "no-store, max-age=0" };
const fields = new Set([
  "enabled",
  "revision",
  "write_epoch",
  "memories",
  "next_cursor",
  "id",
  "kind",
  "source_id",
  "scenario",
  "content",
  "payload",
  "version",
  "status",
  "expires_at",
  "created_at",
  "updated_at",
  "request_id",
  "choices",
  "choice_id",
  "label",
  "message",
  "memory_ids",
  "replaced_ids",
]);
function projection(value: unknown): unknown {
  if (Array.isArray(value)) return value.slice(0, 100).map(projection);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => fields.has(key))
      .map(([key, v]) => [key, key === "payload" ? v : projection(v)]),
  );
}
async function proxy(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (
    !(
      (path.length === 1 && ["memory-settings", "memories"].includes(path[0])) ||
      (path.length === 2 &&
        path[0] === "memories" &&
        /^(pm_[a-f0-9]{32}|clear-preview|clear)$/.test(path[1]))
    )
  )
    return Response.json({ message: "接口不存在。" }, { status: 404, headers });
  const access = await requireAgentSession(request, request.method !== "GET");
  if ("response" in access) return access.response;
  let body: string | undefined;
  if (request.headers.get("content-type")?.includes("application/json")) {
    const raw = await request.text();
    if (new TextEncoder().encode(raw).length > 16384)
      return Response.json({ message: "请求过大。" }, { status: 413, headers });
    try {
      body = JSON.stringify(JSON.parse(raw));
    } catch {
      return Response.json({ message: "请求格式无效。" }, { status: 400, headers });
    }
  }
  const query = new URLSearchParams();
  const input = new URL(request.url).searchParams;
  for (const key of ["cursor", "limit", "expected_version"])
    if (input.has(key)) query.set(key, input.get(key)!);
  try {
    const upstream = await fetch(
      agentUrl(`/v1/me/${path.map(encodeURIComponent).join("/")}${query.size ? `?${query}` : ""}`),
      {
        method: request.method,
        headers: {
          authorization: `Bearer ${access.token}`,
          ...(body ? { "content-type": "application/json" } : {}),
        },
        body,
        cache: "no-store",
        signal: request.signal,
      },
    );
    if (upstream.status === 401) await clearSessionCookie();
    const value: unknown = await upstream.json();
    if (!upstream.ok) {
      const detail =
        value && typeof value === "object" && "detail" in value
          ? (value.detail as Record<string, unknown>)
          : {};
      const code =
        typeof detail.code === "string" && /^PERSONAL_MEMORY_[A-Z_]+$/.test(detail.code)
          ? detail.code
          : "PERSONAL_MEMORY_UNAVAILABLE";
      return Response.json(
        {
          code,
          message:
            code.includes("CONFLICT") || code.includes("STALE")
              ? "状态已变化，请刷新重试；草稿已保留。"
              : "个人记忆暂不可用，请重试。",
        },
        { status: upstream.status, headers },
      );
    }
    return Response.json(projection(value), { status: upstream.status, headers });
  } catch {
    return Response.json(
      { code: "PERSONAL_MEMORY_UNAVAILABLE", message: "个人记忆暂不可用，请重试。" },
      { status: 503, headers },
    );
  }
}
export const GET = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const POST = proxy;
