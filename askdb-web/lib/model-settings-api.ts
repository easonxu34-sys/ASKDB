import { agentUrl, clearSessionCookie, requireAgentSession } from "@/lib/agent-proxy";

const allowedCodes = new Set([
  "MODEL_SETTINGS_UNAVAILABLE",
  "MODEL_NOT_CONFIGURED",
  "MODEL_CONFIGURATION_INVALID",
  "MODEL_AUTH_FAILED",
  "MODEL_CONNECTION_FAILED",
  "MODEL_REJECTED",
  "MODEL_PROFILE_NOT_FOUND",
]);

const safeMessages: Record<string, string> = {
  MODEL_SETTINGS_UNAVAILABLE: "模型设置服务暂不可用。",
  MODEL_NOT_CONFIGURED: "请先完成模型设置。",
  MODEL_CONFIGURATION_INVALID: "模型设置字段无效，请检查后重试。",
  MODEL_AUTH_FAILED: "模型认证失败，请检查 API Key。",
  MODEL_CONNECTION_FAILED: "无法连接模型服务，请检查 API 地址后重试。",
  MODEL_REJECTED: "模型服务拒绝了探测请求，请检查模型名称和配置。",
  MODEL_PROFILE_NOT_FOUND: "找不到所选模型配置，请重新选择。",
};

type PublicSettings = {
  provider: "openai" | "deepseek" | "custom";
  model: string;
  base_url: string;
  api_key_configured: boolean;
  context_window_tokens?: number | null;
  max_output_tokens?: number | null;
  tokenizer_id?: "tiktoken:cl100k_base" | "tiktoken:o200k_base" | null;
};

type PublicProfile = PublicSettings & {
  id: string;
  name: string;
  available: boolean;
};

function optionalBudgetFields(candidate: Record<string, unknown>) {
  const validTokenizer = candidate.tokenizer_id === "tiktoken:cl100k_base" ||
    candidate.tokenizer_id === "tiktoken:o200k_base" || candidate.tokenizer_id === null;
  const fields: {
    context_window_tokens?: number | null;
    max_output_tokens?: number | null;
    tokenizer_id?: "tiktoken:cl100k_base" | "tiktoken:o200k_base" | null;
  } = {
    ...(typeof candidate.context_window_tokens === "number" || candidate.context_window_tokens === null
      ? { context_window_tokens: candidate.context_window_tokens }
      : {}),
    ...(typeof candidate.max_output_tokens === "number" || candidate.max_output_tokens === null
      ? { max_output_tokens: candidate.max_output_tokens }
      : {}),
    ...(validTokenizer ? { tokenizer_id: candidate.tokenizer_id as PublicSettings["tokenizer_id"] } : {}),
  };
  return fields;
}

function noStoreHeaders(contentType = "application/json") {
  return {
    "content-type": contentType,
    "cache-control": "no-store, max-age=0",
  };
}

function extractCode(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const object = value as Record<string, unknown>;
  const detail = object.detail;
  if (typeof object.code === "string") return object.code;
  if (detail && typeof detail === "object" && "code" in detail) {
    const code = (detail as Record<string, unknown>).code;
    if (typeof code === "string") return code;
  }
  return null;
}

function isProvider(value: unknown): value is PublicSettings["provider"] {
  return value === "openai" || value === "deepseek" || value === "custom";
}

function publicProjection(value: unknown): unknown {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  if (
    Array.isArray(item.profiles) &&
    (typeof item.default_profile_id === "string" || item.default_profile_id === null)
  ) {
    const profiles = item.profiles.flatMap((profile): PublicProfile[] => {
      if (!profile || typeof profile !== "object") return [];
      const candidate = profile as Record<string, unknown>;
      if (
        typeof candidate.id !== "string" || typeof candidate.name !== "string" ||
        !isProvider(candidate.provider) || typeof candidate.model !== "string" ||
        typeof candidate.base_url !== "string" ||
        typeof candidate.api_key_configured !== "boolean" ||
        typeof candidate.available !== "boolean"
      ) return [];
      return [{
        id: candidate.id,
        name: candidate.name,
        provider: candidate.provider,
        model: candidate.model,
        base_url: candidate.base_url,
        api_key_configured: candidate.api_key_configured,
        available: candidate.available,
        ...optionalBudgetFields(candidate),
      }];
    });
    return { default_profile_id: item.default_profile_id, profiles };
  }
  if (
    typeof item.default_profile_id === "string" &&
    Object.keys(item).length === 1
  ) return { default_profile_id: item.default_profile_id };
  if (typeof item.id === "string" && typeof item.name === "string") {
    if (
      !isProvider(item.provider) || typeof item.model !== "string" ||
      typeof item.base_url !== "string" || typeof item.api_key_configured !== "boolean" ||
      typeof item.available !== "boolean"
    ) return null;
    return {
      id: item.id,
      name: item.name,
      provider: item.provider,
      model: item.model,
      base_url: item.base_url,
      api_key_configured: item.api_key_configured,
      available: item.available,
      ...optionalBudgetFields(item),
    } satisfies PublicProfile;
  }
  if (
    !isProvider(item.provider) ||
    typeof item.model !== "string" ||
    typeof item.base_url !== "string" ||
    typeof item.api_key_configured !== "boolean"
  ) {
    return null;
  }
  return {
    provider: item.provider,
    model: item.model,
    base_url: item.base_url,
    api_key_configured: item.api_key_configured,
    ...optionalBudgetFields(item),
  };
}

export async function proxyModelSettings(request: Request, agentPath: string) {
  const access = await requireAgentSession(request, !["GET", "HEAD"].includes(request.method));
  if ("response" in access) return access.response;
  let body: string | undefined;
  const hasJsonBody = request.method === "POST" || request.method === "PUT" ||
    (request.method === "DELETE" && request.headers.get("content-type")?.includes("application/json"));
  if (hasJsonBody) {
    const contentLength = Number(request.headers.get("content-length") ?? 0);
    if (contentLength > 16 * 1024) {
      return Response.json(
        { code: "MODEL_CONFIGURATION_INVALID", message: safeMessages.MODEL_CONFIGURATION_INVALID },
        { status: 413, headers: noStoreHeaders() },
      );
    }
    try {
      const raw = await request.text();
      if (new TextEncoder().encode(raw).byteLength > 16 * 1024) {
        return Response.json(
          { code: "MODEL_CONFIGURATION_INVALID", message: safeMessages.MODEL_CONFIGURATION_INVALID },
          { status: 413, headers: noStoreHeaders() },
        );
      }
      const value: unknown = JSON.parse(raw);
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        return Response.json(
          { code: "MODEL_CONFIGURATION_INVALID", message: safeMessages.MODEL_CONFIGURATION_INVALID },
          { status: 400, headers: noStoreHeaders() },
        );
      }
      body = JSON.stringify(value);
    } catch {
      return Response.json(
        { code: "MODEL_CONFIGURATION_INVALID", message: safeMessages.MODEL_CONFIGURATION_INVALID },
        { status: 400, headers: noStoreHeaders() },
      );
    }
  }

  let upstream: Response;
  try {
    upstream = await fetch(agentUrl(agentPath), {
      method: request.method,
      headers: {
        authorization: `Bearer ${access.token}`,
        accept: "application/json",
        ...(body ? { "content-type": "application/json" } : {}),
      },
      body,
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return Response.json(
      { code: "MODEL_SETTINGS_UNAVAILABLE", message: safeMessages.MODEL_SETTINGS_UNAVAILABLE },
      { status: 503, headers: noStoreHeaders() },
    );
  }

  let upstreamBody: unknown;
  try {
    upstreamBody = await upstream.json();
  } catch {
    upstreamBody = null;
  }
  if (upstream.status === 401) {
    await clearSessionCookie();
    return Response.json(
      { code: "AUTH_REQUIRED", message: "登录状态已失效，请重新登录。" },
      { status: 401, headers: noStoreHeaders() },
    );
  }
  if (upstream.status === 403) {
    return Response.json(
      { code: "ADMIN_REQUIRED", message: "此操作仅限管理员。" },
      { status: 403, headers: noStoreHeaders() },
    );
  }
  if (!upstream.ok) {
    const requestedCode = extractCode(upstreamBody);
    const code = requestedCode && allowedCodes.has(requestedCode)
      ? requestedCode
      : upstream.status === 422
        ? "MODEL_CONFIGURATION_INVALID"
        : "MODEL_SETTINGS_UNAVAILABLE";
    return Response.json(
      { code, message: safeMessages[code] },
      { status: upstream.status, headers: noStoreHeaders() },
    );
  }

  if (agentPath.endsWith("/test")) {
    const ok = Boolean(upstreamBody && typeof upstreamBody === "object" &&
      (upstreamBody as Record<string, unknown>).ok === true);
    return Response.json({ ok }, { status: upstream.status, headers: noStoreHeaders() });
  }
  const projection = publicProjection(upstreamBody);
  if (!projection) {
    return Response.json(
      { code: "MODEL_SETTINGS_UNAVAILABLE", message: safeMessages.MODEL_SETTINGS_UNAVAILABLE },
      { status: 502, headers: noStoreHeaders() },
    );
  }
  return Response.json(projection, { status: upstream.status, headers: noStoreHeaders() });
}
