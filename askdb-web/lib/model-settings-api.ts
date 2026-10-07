import { agentUrl, clearSessionCookie, requireAgentSession } from "@/lib/agent-proxy";

const allowedCodes = new Set([
  "MODEL_SETTINGS_UNAVAILABLE",
  "MODEL_NOT_CONFIGURED",
  "MODEL_CONFIGURATION_INVALID",
  "MODEL_AUTH_FAILED",
  "MODEL_CONNECTION_FAILED",
  "MODEL_REJECTED",
  "MODEL_RESPONSE_INVALID",
  "MODEL_PROFILE_NOT_FOUND",
]);

const safeMessages: Record<string, string> = {
  MODEL_SETTINGS_UNAVAILABLE: "模型设置服务暂不可用。",
  MODEL_NOT_CONFIGURED: "请先完成模型设置。",
  MODEL_CONFIGURATION_INVALID: "模型设置字段无效，请检查后重试。",
  MODEL_AUTH_FAILED: "模型鉴权失败，请检查 API Key、地域和模型访问权限。",
  MODEL_CONNECTION_FAILED: "无法连接模型服务或服务暂不可用，请检查 API 地址、网络和服务状态。",
  MODEL_REJECTED: "模型服务拒绝了探测请求，请检查模型名称、请求参数或调用额度。",
  MODEL_RESPONSE_INVALID: "服务已响应，但返回格式不符合预期；请确认 API URL 和服务类型。",
  MODEL_PROFILE_NOT_FOUND: "找不到所选模型配置，请重新选择。",
};

const safeConfigurationMessages = new Set([
  "模型服务参数包含不支持的字段。",
  "Chat 不接受向量或排序参数。",
  "Embedding 协议或维度无效。",
  "Rerank 协议或候选数无效。",
  "模型类型无效。",
  "模型供应商不受支持。",
  "模型名称不能为空且不能超过 200 个字符。",
  "API 地址必须是有效的 HTTP(S) 绝对地址。",
  "非 Chat 配置不接受聊天预算。",
  "记忆上下文预算必须同时配置窗口、输出预留和 tokenizer。",
  "上下文窗口必须介于 1024 和 2000000 token。",
  "输出预留必须大于 0 且小于上下文窗口。",
  "tokenizer 必须选择已支持的显式编码。",
  "配置名称不能为空且不能超过 100 个字符。",
  "新增模型配置必须提供 API Key。",
  "创建后不能更改模型类型。",
  "模型配置缺少 API Key。",
]);

const validationFieldMessages: Record<string, string> = {
  model_kind: "模型类型字段格式无效。",
  service_options: "Embedding/Rerank 服务参数格式无效。",
  name: "配置名称字段格式无效。",
  provider: "模型供应商字段格式无效。",
  model: "Model 字段格式无效。",
  base_url: "完整接口 URL 字段格式无效。",
  api_key: "API Key 字段格式无效。",
  context_window_tokens: "Chat 上下文预算字段格式无效。",
  max_output_tokens: "Chat 输出预算字段格式无效。",
  tokenizer_id: "Chat Tokenizer 字段格式无效。",
  profile_id: "模型配置 ID 格式无效。",
};

const safeResponseDiagnostics: Record<string, string> = {
  response_too_large: "服务响应过大，无法安全解析。",
  invalid_json: "服务返回的内容不是有效 JSON；请确认填写的是完整 API URL。",
  response_not_object: "服务返回的 JSON 顶层不是对象；请确认 API URL 和服务类型。",
  embedding_output_missing:
    "响应中没有 output.embeddings；请确认使用 DashScope 原生 Embedding 接口。",
  embedding_count_mismatch: "返回的向量条数与测试输入不一致。",
  embedding_item_invalid: "Embedding 返回项不是有效对象。",
  embedding_index_missing: "Embedding 结果缺少索引；多条输入时必须返回 text_index。",
  embedding_index_invalid: "Embedding 返回的 text_index 无效或重复。",
  embedding_vector_missing: "Embedding 返回项缺少 embedding 字段。",
  embedding_vector_invalid: "Embedding 的 embedding 必须是数值数组。",
  embedding_dimension_mismatch: "返回向量维度与请求维度不一致；请确认模型支持该维度。",
  embedding_value_invalid: "Embedding 向量包含非数值内容。",
  rerank_output_missing:
    "响应中没有 output.results；请确认使用 DashScope 原生 Rerank 接口。",
  rerank_count_mismatch: "返回的排序条数与测试文档数不一致。",
  rerank_item_invalid: "Rerank 结果缺少有效的 index 或 relevance_score。",
};

type PublicSettings = {
  model_kind?: "chat" | "embedding" | "rerank";
  service_options?: { protocol?: string; dimensions?: number; max_candidates?: number };
  provider: "openai" | "deepseek" | "custom" | "bailian";
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
  const validTokenizer =
    candidate.tokenizer_id === "tiktoken:cl100k_base" ||
    candidate.tokenizer_id === "tiktoken:o200k_base" ||
    candidate.tokenizer_id === null;
  const fields: {
    context_window_tokens?: number | null;
    max_output_tokens?: number | null;
    tokenizer_id?: "tiktoken:cl100k_base" | "tiktoken:o200k_base" | null;
  } = {
    ...(typeof candidate.context_window_tokens === "number" ||
    candidate.context_window_tokens === null
      ? { context_window_tokens: candidate.context_window_tokens }
      : {}),
    ...(typeof candidate.max_output_tokens === "number" || candidate.max_output_tokens === null
      ? { max_output_tokens: candidate.max_output_tokens }
      : {}),
    ...(validTokenizer
      ? { tokenizer_id: candidate.tokenizer_id as PublicSettings["tokenizer_id"] }
      : {}),
  };
  return {
    ...fields,
    ...(candidate.model_kind === "chat" ||
    candidate.model_kind === "embedding" ||
    candidate.model_kind === "rerank"
      ? { model_kind: candidate.model_kind }
      : {}),
    ...(candidate.service_options && typeof candidate.service_options === "object"
      ? {
          service_options: Object.fromEntries(
            Object.entries(candidate.service_options as Record<string, unknown>).filter(
              ([k, v]) =>
                (k === "protocol" && typeof v === "string") ||
                (["dimensions", "max_candidates"].includes(k) &&
                  typeof v === "number" &&
                  Number.isInteger(v)),
            ),
          ),
        }
      : {}),
  } as Pick<
    PublicSettings,
    | "context_window_tokens"
    | "max_output_tokens"
    | "tokenizer_id"
    | "model_kind"
    | "service_options"
  >;
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

function extractDiagnostic(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const detail = (value as Record<string, unknown>).detail;
  if (!detail || typeof detail !== "object") return null;
  const diagnostic = (detail as Record<string, unknown>).diagnostic_code;
  return typeof diagnostic === "string" ? diagnostic : null;
}

function extractConfigurationMessage(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const detail = (value as Record<string, unknown>).detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const message = (detail as Record<string, unknown>).message;
    if (typeof message === "string" && safeConfigurationMessages.has(message)) return message;
  }
  if (Array.isArray(detail)) {
    for (const item of detail) {
      if (!item || typeof item !== "object") continue;
      const location = (item as Record<string, unknown>).loc;
      if (!Array.isArray(location)) continue;
      for (const part of location) {
        if (typeof part === "string" && validationFieldMessages[part]) {
          return validationFieldMessages[part];
        }
      }
    }
  }
  return null;
}

function isProvider(value: unknown): value is PublicSettings["provider"] {
  return value === "openai" || value === "deepseek" || value === "custom" || value === "bailian";
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
        typeof candidate.id !== "string" ||
        typeof candidate.name !== "string" ||
        !isProvider(candidate.provider) ||
        typeof candidate.model !== "string" ||
        typeof candidate.base_url !== "string" ||
        typeof candidate.api_key_configured !== "boolean" ||
        typeof candidate.available !== "boolean"
      )
        return [];
      return [
        {
          id: candidate.id,
          name: candidate.name,
          provider: candidate.provider,
          model: candidate.model,
          base_url: candidate.base_url,
          api_key_configured: candidate.api_key_configured,
          available: candidate.available,
          ...optionalBudgetFields(candidate),
        },
      ];
    });
    const refs = item.service_references;
    const service_references =
      refs && typeof refs === "object"
        ? Object.fromEntries(
            Object.entries(refs).filter(
              ([k, v]) =>
                ["embedding", "rerank", "memory_chat"].includes(k) && typeof v === "string",
            ),
          )
        : {};
    const rawIndex = item.personal_index_status as Record<string, unknown> | undefined;
    const personal_index_status =
      rawIndex &&
      Array.isArray(rawIndex.generations) &&
      typeof rawIndex.pending_jobs === "number" &&
      typeof rawIndex.failed_jobs === "number"
        ? {
            generations: rawIndex.generations
              .filter(
                (g): g is { id: string; state: string; dimensions: number } =>
                  Boolean(g) &&
                  typeof g.id === "string" &&
                  ["active", "building"].includes(g.state) &&
                  typeof g.dimensions === "number",
              )
              .map((g) => ({ id: g.id, state: g.state, dimensions: g.dimensions })),
            pending_jobs: rawIndex.pending_jobs,
            failed_jobs: rawIndex.failed_jobs,
          }
        : undefined;
    return {
      default_profile_id: item.default_profile_id,
      profiles,
      service_references,
      ...(personal_index_status ? { personal_index_status } : {}),
    };
  }
  if (typeof item.default_profile_id === "string" && Object.keys(item).length === 1)
    return { default_profile_id: item.default_profile_id };
  if (typeof item.id === "string" && typeof item.name === "string") {
    if (
      !isProvider(item.provider) ||
      typeof item.model !== "string" ||
      typeof item.base_url !== "string" ||
      typeof item.api_key_configured !== "boolean" ||
      typeof item.available !== "boolean"
    )
      return null;
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
  const hasJsonBody =
    request.method === "POST" ||
    request.method === "PUT" ||
    (request.method === "DELETE" &&
      request.headers.get("content-type")?.includes("application/json"));
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
          {
            code: "MODEL_CONFIGURATION_INVALID",
            message: safeMessages.MODEL_CONFIGURATION_INVALID,
          },
          { status: 413, headers: noStoreHeaders() },
        );
      }
      const value: unknown = JSON.parse(raw);
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        return Response.json(
          {
            code: "MODEL_CONFIGURATION_INVALID",
            message: safeMessages.MODEL_CONFIGURATION_INVALID,
          },
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
    const diagnostic = extractDiagnostic(upstreamBody);
    const code =
      requestedCode && allowedCodes.has(requestedCode)
        ? requestedCode
        : upstream.status === 422
          ? "MODEL_CONFIGURATION_INVALID"
          : "MODEL_SETTINGS_UNAVAILABLE";
    return Response.json(
      {
        code,
        message:
          code === "MODEL_RESPONSE_INVALID" && diagnostic
            ? safeResponseDiagnostics[diagnostic] ?? safeMessages[code]
            : code === "MODEL_CONFIGURATION_INVALID"
              ? extractConfigurationMessage(upstreamBody) ?? safeMessages[code]
              : safeMessages[code],
      },
      { status: upstream.status, headers: noStoreHeaders() },
    );
  }

  if (agentPath.endsWith("/test")) {
    const ok = Boolean(
      upstreamBody &&
      typeof upstreamBody === "object" &&
      (upstreamBody as Record<string, unknown>).ok === true,
    );
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
