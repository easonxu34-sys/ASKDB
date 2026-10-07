export type ModelProvider = "openai" | "deepseek" | "custom" | "bailian";

export type ModelKind = "chat" | "embedding" | "rerank";
export type ModelProfile = {
  model_kind?: ModelKind;
  service_options?: { protocol?: string; dimensions?: number; max_candidates?: number };
  id: string;
  name: string;
  provider: ModelProvider;
  model: string;
  base_url: string;
  api_key_configured: boolean;
  available: boolean;
  context_window_tokens?: number | null;
  max_output_tokens?: number | null;
  tokenizer_id?: "tiktoken:cl100k_base" | "tiktoken:o200k_base" | null;
};

export type ModelCatalog = {
  personal_index_status?: {
    generations: { id: string; state: string; dimensions: number }[];
    pending_jobs: number;
    failed_jobs: number;
  };
  service_references?: Partial<Record<"embedding" | "rerank" | "memory_chat", string>>;
  default_profile_id: string | null;
  profiles: ModelProfile[];
};

export type ChatModelOption = {
  id: string;
  name: string;
  model: string;
  available: boolean;
};

export type ChatModelCatalog = {
  default_profile_id: string | null;
  profiles: ChatModelOption[];
};

export async function fetchChatModelOptions(): Promise<ChatModelCatalog> {
  const response = await fetch("/api/chat/model-options", { cache: "no-store" });
  if (!response.ok) throw new Error("可用模型列表读取失败，请重试。");
  const value: unknown = await response.json();
  if (!value || typeof value !== "object") throw new Error("模型列表响应无效。");
  const catalog = value as Partial<ChatModelCatalog>;
  if (
    !Array.isArray(catalog.profiles) ||
    !(typeof catalog.default_profile_id === "string" || catalog.default_profile_id === null)
  ) {
    throw new Error("模型列表响应无效。");
  }
  return {
    default_profile_id: catalog.default_profile_id,
    profiles: catalog.profiles.filter(isChatModelOption),
  };
}

function isChatModelOption(value: unknown): value is ChatModelOption {
  if (!value || typeof value !== "object") return false;
  const profile = value as Record<string, unknown>;
  return (
    typeof profile.id === "string" &&
    typeof profile.name === "string" &&
    typeof profile.model === "string" &&
    typeof profile.available === "boolean"
  );
}

export async function fetchModelCatalog(): Promise<ModelCatalog> {
  const response = await fetch("/api/settings/models", { cache: "no-store" });
  if (!response.ok) throw new Error("模型配置读取失败，请重试。");
  const value: unknown = await response.json();
  if (!value || typeof value !== "object") throw new Error("模型配置响应无效。");
  const catalog = value as Partial<ModelCatalog>;
  if (
    !Array.isArray(catalog.profiles) ||
    !(typeof catalog.default_profile_id === "string" || catalog.default_profile_id === null)
  )
    throw new Error("模型配置响应无效。");
  return {
    default_profile_id: catalog.default_profile_id,
    profiles: catalog.profiles.filter(isModelProfile),
    service_references: catalog.service_references,
    personal_index_status: catalog.personal_index_status,
  };
}

function isModelProfile(value: unknown): value is ModelProfile {
  if (!value || typeof value !== "object") return false;
  const profile = value as Record<string, unknown>;
  const budgetIsValid =
    (profile.context_window_tokens === undefined ||
      profile.context_window_tokens === null ||
      (typeof profile.context_window_tokens === "number" &&
        Number.isInteger(profile.context_window_tokens))) &&
    (profile.max_output_tokens === undefined ||
      profile.max_output_tokens === null ||
      (typeof profile.max_output_tokens === "number" &&
        Number.isInteger(profile.max_output_tokens))) &&
    (profile.tokenizer_id === undefined ||
      profile.tokenizer_id === null ||
      profile.tokenizer_id === "tiktoken:cl100k_base" ||
      profile.tokenizer_id === "tiktoken:o200k_base");
  return (
    typeof profile.id === "string" &&
    typeof profile.name === "string" &&
    (profile.provider === "openai" ||
      profile.provider === "deepseek" ||
      profile.provider === "custom" ||
      profile.provider === "bailian") &&
    typeof profile.model === "string" &&
    typeof profile.base_url === "string" &&
    typeof profile.api_key_configured === "boolean" &&
    typeof profile.available === "boolean" &&
    budgetIsValid
  );
}

export async function responseMessage(response: Response) {
  try {
    const body = await response.json();
    if (typeof body.message === "string") return body.message;
    if (typeof body.detail?.message === "string") return body.detail.message;
  } catch {
    // Fall through to the stable generic message.
  }
  return "请求失败，请稍后重试。";
}
