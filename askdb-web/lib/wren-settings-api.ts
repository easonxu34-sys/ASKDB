import { agentUrl, clearSessionCookie, requireAgentSession } from "@/lib/agent-proxy";

const MAX_JSON_BYTES = 1024 * 1024;

const allowedErrorCodes = new Set([
  "AUTH_REQUIRED",
  "INVALID_SESSION",
  "PASSWORD_CHANGE_REQUIRED",
  "ADMIN_REQUIRED",
  "DATA_SOURCE_NOT_FOUND",
  "DATA_SOURCE_UNAVAILABLE",
  "DATA_SOURCE_REQUIRED",
  "WREN_CONFIGURATION_INVALID",
  "WREN_CONNECTION_FAILED",
  "WREN_SCHEMA_DISCOVERY_FAILED",
  "WREN_VALIDATION_FAILED",
  "WREN_BUILD_FAILED",
  "WREN_RUNTIME_INIT_FAILED",
  "CHAT_DATA_SOURCE_MISMATCH",
  "WREN_SETTINGS_UNAVAILABLE",
]);

const errorMessages: Record<string, string> = {
  AUTH_REQUIRED: "登录状态已失效，请重新登录。",
  INVALID_SESSION: "登录状态已失效，请重新登录。",
  PASSWORD_CHANGE_REQUIRED: "请先修改临时密码。",
  ADMIN_REQUIRED: "此操作仅限管理员。",
  DATA_SOURCE_NOT_FOUND: "找不到所选数据源或操作。",
  DATA_SOURCE_UNAVAILABLE: "所选数据源当前不可用，请检查设置。",
  DATA_SOURCE_REQUIRED: "请先为此会话选择一个数据源。",
  WREN_CONFIGURATION_INVALID: "配置字段无效，请检查后重试。",
  WREN_CONNECTION_FAILED: "数据库连接失败，请检查地址、账号和网络策略。",
  WREN_SCHEMA_DISCOVERY_FAILED: "读取数据库表结构失败，请检查账号权限。",
  WREN_VALIDATION_FAILED: "配置校验失败，请检查模型、关系和规则。",
  WREN_BUILD_FAILED: "模型构建失败，请检查配置后重试。",
  WREN_RUNTIME_INIT_FAILED: "数据源运行环境初始化失败，当前版本仍保持生效。",
  CHAT_DATA_SOURCE_MISMATCH: "此会话已绑定其他数据源，请新建会话后切换。",
  WREN_SETTINGS_UNAVAILABLE: "设置服务当前不可用。",
};

const noStoreHeaders = {
  "cache-control": "no-store, max-age=0",
  pragma: "no-cache",
  "content-type": "application/json; charset=utf-8",
};

type SafePath = {
  path: string;
  kind:
    | "catalog"
    | "connectors"
    | "list"
    | "create"
    | "detail"
    | "revision"
    | "runtimeStatus"
    | "operation"
    | "schema"
    | "test"
    | "default";
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isId(value: string | undefined): value is string {
  return Boolean(value && /^[A-Za-z0-9_-]{1,128}$/.test(value));
}

function resolvePath(segments: string[], method: string): SafePath | null {
  if (
    segments.some(
      (segment) => !segment || segment.includes("/") || segment === "." || segment === "..",
    )
  )
    return null;
  if (segments.length === 1 && segments[0] === "data-sources" && method === "GET") {
    return { path: "/v1/settings/wren/data-sources", kind: "list" };
  }
  if (segments.length === 1 && segments[0] === "data-sources" && method === "POST") {
    return { path: "/v1/settings/wren/data-sources", kind: "create" };
  }
  if (segments.length === 1 && segments[0] === "connectors" && method === "GET") {
    return { path: "/v1/settings/wren/connectors", kind: "connectors" };
  }
  if (segments.length === 1 && segments[0] === "default-data-source" && method === "PUT") {
    return { path: "/v1/settings/wren/default-data-source", kind: "default" };
  }
  if (
    segments.length === 2 &&
    segments[0] === "operations" &&
    isId(segments[1]) &&
    method === "GET"
  ) {
    return {
      path: `/v1/settings/wren/operations/${encodeURIComponent(segments[1])}`,
      kind: "operation",
    };
  }
  if (segments[0] !== "data-sources" || !isId(segments[1])) return null;
  const id = encodeURIComponent(segments[1]);
  if (segments.length === 2 && (method === "GET" || method === "PUT")) {
    return { path: `/v1/settings/wren/data-sources/${id}`, kind: "detail" };
  }
  if (segments.length === 3 && segments[2] === "runtime-status" && method === "GET") {
    return {
      path: `/v1/settings/wren/data-sources/${id}/runtime-status`,
      kind: "runtimeStatus",
    };
  }
  if (
    segments.length === 4 &&
    method === "GET" &&
    segments[2] === "revisions" &&
    isId(segments[3])
  ) {
    return {
      path: `/v1/settings/wren/data-sources/${id}/revisions/${encodeURIComponent(segments[3])}`,
      kind: "revision",
    };
  }
  if (segments.length === 3 && method === "POST") {
    const action = segments[2];
    if (action === "apply")
      return { path: `/v1/settings/wren/data-sources/${id}/apply`, kind: "operation" };
    if (action === "deactivate")
      return { path: `/v1/settings/wren/data-sources/${id}/deactivate`, kind: "detail" };
    if (action === "enable")
      return { path: `/v1/settings/wren/data-sources/${id}/enable`, kind: "detail" };
    if (action === "connection") return null;
    if (action === "schema") return null;
  }
  if (
    segments.length === 4 &&
    method === "POST" &&
    segments[2] === "connection" &&
    segments[3] === "test"
  ) {
    return { path: `/v1/settings/wren/data-sources/${id}/connection/test`, kind: "test" };
  }
  if (
    segments.length === 4 &&
    method === "POST" &&
    segments[2] === "schema" &&
    segments[3] === "refresh"
  ) {
    return { path: `/v1/settings/wren/data-sources/${id}/schema/refresh`, kind: "schema" };
  }
  if (
    segments.length === 4 &&
    method === "POST" &&
    segments[2] === "rollback" &&
    isId(segments[3])
  ) {
    return {
      path: `/v1/settings/wren/data-sources/${id}/rollback/${encodeURIComponent(segments[3])}`,
      kind: "detail",
    };
  }
  return null;
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key));
}

function safeSemantic(value: unknown): Record<string, unknown> | null {
  if (
    !isRecord(value) ||
    !hasOnlyKeys(value, [
      "tables",
      "models",
      "relationships",
      "ignored_foreign_keys",
      "rules",
      "views",
    ])
  )
    return null;
  const tables = value.tables ?? [];
  const models = value.models ?? [];
  const relationships = value.relationships ?? [];
  const ignoredForeignKeys = value.ignored_foreign_keys ?? [];
  const rules = value.rules ?? [];
  const views = value.views ?? [];
  if (
    !Array.isArray(tables) ||
    !tables.every((item) => typeof item === "string") ||
    !Array.isArray(models) ||
    !Array.isArray(relationships) ||
    !Array.isArray(ignoredForeignKeys) ||
    ignoredForeignKeys.length > 2000 ||
    !ignoredForeignKeys.every((item) => typeof item === "string" && item.length <= 2048) ||
    !Array.isArray(rules) ||
    !Array.isArray(views)
  )
    return null;
  const safeModels = models.map((model) => {
    if (
      !isRecord(model) ||
      !hasOnlyKeys(model, ["table", "name", "description", "columns"]) ||
      typeof model.table !== "string" ||
      typeof model.name !== "string" ||
      typeof model.description !== "string" ||
      !Array.isArray(model.columns)
    )
      return null;
    const columns = model.columns.map((column) =>
      isRecord(column) &&
      hasOnlyKeys(column, ["name", "description", "hidden", "primary_key"]) &&
      typeof column.name === "string" &&
      typeof column.description === "string" &&
      typeof column.hidden === "boolean" &&
      typeof column.primary_key === "boolean"
        ? {
            name: column.name,
            description: column.description,
            hidden: column.hidden,
            primary_key: column.primary_key,
          }
        : null,
    );
    if (columns.some((column) => column === null)) return null;
    return { table: model.table, name: model.name, description: model.description, columns };
  });
  const safeRelationships = relationships.map((item) =>
    isRecord(item) &&
    hasOnlyKeys(item, [
      "name",
      "left_model",
      "right_model",
      "join_type",
      "condition",
      "foreign_key_id",
    ]) &&
    typeof item.name === "string" &&
    typeof item.left_model === "string" &&
    typeof item.right_model === "string" &&
    typeof item.join_type === "string" &&
    typeof item.condition === "string" &&
    (item.foreign_key_id === undefined ||
      item.foreign_key_id === null ||
      (typeof item.foreign_key_id === "string" && item.foreign_key_id.length <= 2048))
      ? {
          name: item.name,
          left_model: item.left_model,
          right_model: item.right_model,
          join_type: item.join_type,
          condition: item.condition,
          ...(typeof item.foreign_key_id === "string"
            ? { foreign_key_id: item.foreign_key_id }
            : {}),
        }
      : null,
  );
  const safeRules = rules.map((item) =>
    isRecord(item) &&
    hasOnlyKeys(item, ["name", "content"]) &&
    typeof item.name === "string" &&
    typeof item.content === "string"
      ? { name: item.name, content: item.content }
      : null,
  );
  const safeViews = views.map((item) =>
    isRecord(item) &&
    hasOnlyKeys(item, ["name", "description", "sql"]) &&
    typeof item.name === "string" &&
    typeof item.description === "string" &&
    typeof item.sql === "string"
      ? { name: item.name, description: item.description, sql: item.sql }
      : null,
  );
  if (
    safeModels.some((item) => item === null) ||
    safeRelationships.some((item) => item === null) ||
    safeRules.some((item) => item === null) ||
    safeViews.some((item) => item === null)
  )
    return null;
  return {
    tables,
    models: safeModels,
    relationships: safeRelationships,
    ignored_foreign_keys: ignoredForeignKeys,
    rules: safeRules,
    views: safeViews,
  };
}

function validateBody(kind: SafePath["kind"], method: string, value: unknown): boolean {
  if (kind === "create" && method === "POST") return false;
  if (kind === "detail" && method === "PUT") return false;
  if (kind === "default" && method === "PUT") {
    return (
      isRecord(value) &&
      hasOnlyKeys(value, ["data_source_id"]) &&
      (value.data_source_id === null || typeof value.data_source_id === "string")
    );
  }
  if (kind === "detail" && method === "POST") return value === undefined;
  return value === undefined;
}

function validateFormBody(method: string, value: unknown): boolean {
  if ((method !== "POST" && method !== "PUT") || !isRecord(value)) return false;
  const topLevel =
    method === "POST"
      ? ["display_name", "connector_type", "connection", "semantic"]
      : ["display_name", "connection", "semantic"];
  if (
    !hasOnlyKeys(value, topLevel) ||
    typeof value.display_name !== "string" ||
    !isRecord(value.connection)
  )
    return false;
  if (
    method === "POST" &&
    (typeof value.connector_type !== "string" || !/^[a-z][a-z0-9_]{0,63}$/.test(value.connector_type))
  )
    return false;
  const connectionEntries = Object.entries(value.connection);
  if (
    connectionEntries.length > 128 ||
    connectionEntries.some(
      ([key, item]) =>
        !/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(key) ||
        key === "__proto__" ||
        key === "constructor" ||
        (item !== null &&
          typeof item !== "string" &&
          typeof item !== "number" &&
          typeof item !== "boolean") ||
        (typeof item === "string" && item.length > 512_000) ||
        (typeof item === "number" && !Number.isFinite(item)),
    )
  )
    return false;
  return safeSemantic(value.semantic) !== null;
}

async function readLimitedBody(request: Request): Promise<string | null> {
  const reader = request.body?.getReader();
  if (!reader) return "";
  const chunks: Uint8Array[] = [];
  let bytes = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    bytes += value.byteLength;
    if (bytes > MAX_JSON_BYTES) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const joined = new Uint8Array(bytes);
  let offset = 0;
  for (const chunk of chunks) {
    joined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder().decode(joined);
}

async function readJsonLimited(response: Response): Promise<unknown | null> {
  const reader = response.body?.getReader();
  if (!reader) return null;
  const chunks: Uint8Array[] = [];
  let bytes = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    bytes += value.byteLength;
    if (bytes > MAX_JSON_BYTES) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const joined = new Uint8Array(bytes);
  let offset = 0;
  for (const chunk of chunks) {
    joined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder().decode(joined)) as unknown;
  } catch {
    return null;
  }
}

function safeSummary(value: unknown): Record<string, unknown> | null {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    typeof value.display_name !== "string" ||
    typeof value.connector_type !== "string" ||
    !/^[a-z][a-z0-9_]{0,63}$/.test(value.connector_type) ||
    typeof value.enabled !== "boolean" ||
    typeof value.runtime_status !== "string" ||
    typeof value.is_default !== "boolean"
  )
    return null;
  return {
    id: value.id,
    display_name: value.display_name,
    connector_type: value.connector_type,
    enabled: value.enabled,
    runtime_status: value.runtime_status,
    is_default: value.is_default,
  };
}

function safeOperation(value: unknown): Record<string, unknown> | null {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    typeof value.data_source_id !== "string" ||
    !["running", "active", "failed"].includes(String(value.status)) ||
    typeof value.phase !== "string"
  )
    return null;
  return {
    id: value.id,
    data_source_id: value.data_source_id,
    revision_id: typeof value.revision_id === "string" ? value.revision_id : null,
    status: value.status,
    phase: value.phase,
    error_code: typeof value.error_code === "string" ? value.error_code : null,
    message:
      typeof value.error_code === "string" ? (errorMessages[value.error_code] ?? null) : null,
    created_at: typeof value.created_at === "string" ? value.created_at : "",
    updated_at: typeof value.updated_at === "string" ? value.updated_at : "",
  };
}

const runtimeReasonCodes = new Set([
  "SOURCE_DISABLED",
  "NO_ACTIVE_REVISION",
  "SOURCE_RUNTIME_NOT_READY",
  "ACTIVE_REVISION_NOT_READY",
  "NO_ACTIVE_RULES",
  "ONLINE_RECALL_DISABLED",
  "RECALL_DISABLED",
]);

function safeRuntimeStatus(value: unknown): Record<string, unknown> | null {
  if (
    !isRecord(value) ||
    !hasOnlyKeys(value, [
      "data_source_id",
      "source_enabled",
      "source_runtime_status",
      "active_revision_id",
      "active_revision_status",
      "active_revision_ready",
      "semantic_rules",
      "online_recall",
    ]) ||
    typeof value.data_source_id !== "string" ||
    typeof value.source_enabled !== "boolean" ||
    typeof value.source_runtime_status !== "string" ||
    !(typeof value.active_revision_id === "string" || value.active_revision_id === null) ||
    !(typeof value.active_revision_status === "string" || value.active_revision_status === null) ||
    typeof value.active_revision_ready !== "boolean" ||
    !isRecord(value.semantic_rules) ||
    !hasOnlyKeys(value.semantic_rules, [
      "configured_count",
      "available_to_query_gate",
      "reason_codes",
    ]) ||
    !Number.isSafeInteger(value.semantic_rules.configured_count) ||
    Number(value.semantic_rules.configured_count) < 0 ||
    typeof value.semantic_rules.available_to_query_gate !== "boolean" ||
    !Array.isArray(value.semantic_rules.reason_codes) ||
    !value.semantic_rules.reason_codes.every(
      (code) => typeof code === "string" && runtimeReasonCodes.has(code),
    ) ||
    !isRecord(value.online_recall) ||
    !hasOnlyKeys(value.online_recall, ["enabled", "reason_codes"]) ||
    typeof value.online_recall.enabled !== "boolean" ||
    !Array.isArray(value.online_recall.reason_codes) ||
    !value.online_recall.reason_codes.every(
      (code) => typeof code === "string" && runtimeReasonCodes.has(code),
    )
  )
    return null;
  return {
    data_source_id: value.data_source_id,
    source_enabled: value.source_enabled,
    source_runtime_status: value.source_runtime_status,
    active_revision_id: value.active_revision_id,
    active_revision_status: value.active_revision_status,
    active_revision_ready: value.active_revision_ready,
    semantic_rules: {
      configured_count: value.semantic_rules.configured_count,
      available_to_query_gate: value.semantic_rules.available_to_query_gate,
      reason_codes: value.semantic_rules.reason_codes,
    },
    online_recall: {
      enabled: value.online_recall.enabled,
      reason_codes: value.online_recall.reason_codes,
    },
  };
}

function isSensitiveFieldName(key: string): boolean {
  const normalized = key.toLowerCase();
  if (normalized.endsWith("_type")) return false;
  return (
    /password|passwd|secret|credential|private|access_key|(^|_)key($|_)|ca_pem|ssl_ca|dsn|certificate|(^|_)cert($|_)/i.test(
      normalized,
    ) ||
    normalized === "token" ||
    normalized.endsWith("_token")
  );
}

function safeConnectionFields(value: unknown): Record<string, unknown> | null {
  if (!isRecord(value)) return null;
  const safe: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    if (
      !/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(key) ||
      isSensitiveFieldName(key) ||
      (item !== null &&
        typeof item !== "string" &&
        typeof item !== "number" &&
        typeof item !== "boolean") ||
      (typeof item === "string" && item.length > 512_000) ||
      (typeof item === "number" && !Number.isFinite(item))
    )
      continue;
    safe[key] = item;
  }
  return safe;
}

function safeSourceConfig(value: unknown): Record<string, unknown> | null {
  if (!isRecord(value)) return null;
  const config = safeConnectionFields(value);
  if (!config) return null;
  const semantic = safeSemantic({
    tables: value.tables,
    models: value.models,
    relationships: value.relationships,
    ignored_foreign_keys: value.ignored_foreign_keys,
    rules: value.rules,
    views: value.views,
  });
  if (!semantic) return null;
  for (const key of [
    "tables",
    "models",
    "relationships",
    "ignored_foreign_keys",
    "rules",
    "views",
  ]) {
    delete config[key];
  }
  return { ...config, ...semantic };
}

function safeDetail(value: unknown): Record<string, unknown> | null {
  if (
    !isRecord(value) ||
    !isRecord(value.data_source) ||
    !isRecord(value.connection) ||
    !isRecord(value.config)
  )
    return null;
  const source = value.data_source;
  if (
    typeof source.id !== "string" ||
    typeof source.display_name !== "string" ||
    typeof source.connector_type !== "string" ||
    !/^[a-z][a-z0-9_]{0,63}$/.test(source.connector_type)
  )
    return null;
  const connection = safeConnectionFields(value.connection);
  if (!connection) return null;
  connection.configured_secret_fields = Array.isArray(value.connection.configured_secret_fields)
    ? value.connection.configured_secret_fields.filter(
        (item): item is string =>
          typeof item === "string" && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(item),
      )
    : [];
  connection.credential_configured = value.connection.credential_configured === true;
  const config = safeSourceConfig(value.config);
  const activeConfig = safeSourceConfig(value.active_config ?? {});
  if (!config || !activeConfig) return null;
  const safeSource = {
    id: source.id,
    display_name: source.display_name,
    connector_type: source.connector_type,
    enabled: source.enabled === true,
    active_revision_id:
      typeof source.active_revision_id === "string" ? source.active_revision_id : null,
    draft_revision_id:
      typeof source.draft_revision_id === "string" ? source.draft_revision_id : null,
    runtime_status: typeof source.runtime_status === "string" ? source.runtime_status : "not_ready",
    created_at: typeof source.created_at === "string" ? source.created_at : "",
    updated_at: typeof source.updated_at === "string" ? source.updated_at : "",
  };
  const revisionValue = value.revision;
  const safeRevision =
    isRecord(revisionValue) && typeof revisionValue.id === "string"
      ? {
          id: revisionValue.id,
          status: typeof revisionValue.status === "string" ? revisionValue.status : "draft",
          error_code:
            typeof revisionValue.error_code === "string" ? revisionValue.error_code : null,
          mdl_digest:
            typeof revisionValue.mdl_digest === "string" ? revisionValue.mdl_digest : null,
        }
      : null;
  const revisions = Array.isArray(value.revisions)
    ? value.revisions.flatMap((item) =>
        isRecord(item) && typeof item.id === "string"
          ? [
              {
                id: item.id,
                status: typeof item.status === "string" ? item.status : "",
                error_code: typeof item.error_code === "string" ? item.error_code : null,
                mdl_digest: typeof item.mdl_digest === "string" ? item.mdl_digest : null,
                created_at: typeof item.created_at === "string" ? item.created_at : "",
              },
            ]
          : [],
      )
    : [];
  return {
    data_source: safeSource,
    connection,
    config,
    active_config: activeConfig,
    revision: safeRevision,
    revisions,
    last_operation: safeOperation(value.last_operation),
  };
}

function safeRevisionDetail(value: unknown): Record<string, unknown> | null {
  if (
    !isRecord(value) ||
    !isRecord(value.data_source) ||
    !isRecord(value.revision) ||
    !isRecord(value.config)
  )
    return null;
  const source = value.data_source;
  const revision = value.revision;
  const config = safeSourceConfig(value.config);
  if (
    typeof source.id !== "string" ||
    typeof source.display_name !== "string" ||
    typeof source.connector_type !== "string" ||
    !/^[a-z][a-z0-9_]{0,63}$/.test(source.connector_type) ||
    typeof revision.id !== "string" ||
    typeof revision.status !== "string" ||
    !config
  )
    return null;
  return {
    data_source: {
      id: source.id,
      display_name: source.display_name,
      connector_type: source.connector_type,
    },
    revision: {
      id: revision.id,
      status: revision.status,
      error_code: typeof revision.error_code === "string" ? revision.error_code : null,
      mdl_digest: typeof revision.mdl_digest === "string" ? revision.mdl_digest : null,
      created_at: typeof revision.created_at === "string" ? revision.created_at : "",
      updated_at: typeof revision.updated_at === "string" ? revision.updated_at : "",
    },
    config,
  };
}

function safePayload(value: unknown, kind: SafePath["kind"]): unknown | null {
  if (kind === "connectors") {
    if (!isRecord(value) || !Array.isArray(value.connectors)) return null;
    const connectors = value.connectors.flatMap((connector) => {
      if (
        !isRecord(connector) ||
        typeof connector.type !== "string" ||
        !/^[a-z][a-z0-9_]{0,63}$/.test(connector.type) ||
        typeof connector.label !== "string" ||
        !Array.isArray(connector.variants) ||
        !Array.isArray(connector.field_groups)
      )
        return [];
      const variants = connector.variants.filter(
        (variant): variant is string => typeof variant === "string",
      );
      const fieldGroups = connector.field_groups.flatMap((group) => {
        if (!isRecord(group) || !Array.isArray(group.fields)) return [];
        const fields = group.fields.flatMap((field) => {
          if (
            !isRecord(field) ||
            typeof field.name !== "string" ||
            typeof field.label !== "string" ||
            typeof field.input_type !== "string"
          )
            return [];
          return [{
            name: field.name,
            label: field.label,
            input_type: field.input_type,
            placeholder: typeof field.placeholder === "string" ? field.placeholder : "",
            hint: typeof field.hint === "string" ? field.hint : null,
            required: field.required === true,
            default:
              typeof field.default === "string" || typeof field.default === "number"
                ? String(field.default)
                : null,
            sensitive:
              field.sensitive === true ||
              field.input_type === "password" ||
              field.input_type === "file_base64",
            alias: typeof field.alias === "string" ? field.alias : null,
            examples: Array.isArray(field.examples)
              ? field.examples.filter((item): item is string => typeof item === "string")
              : [],
            accept: typeof field.accept === "string" ? field.accept : null,
          }];
        });
        return [{
          variant: typeof group.variant === "string" ? group.variant : null,
          fields,
        }];
      });
      return [{ type: connector.type, label: connector.label, variants, field_groups: fieldGroups }];
    });
    if (connectors.length !== value.connectors.length) return null;
    return { connectors };
  }
  if (kind === "catalog") {
    if (
      !isRecord(value) ||
      !(
        typeof value.default_data_source_id === "string" || value.default_data_source_id === null
      ) ||
      !Array.isArray(value.data_sources)
    )
      return null;
    const dataSources = value.data_sources.map(safeSummary);
    if (dataSources.some((item) => item === null)) return null;
    return {
      default_data_source_id: value.default_data_source_id,
      migration_status:
        typeof value.migration_status === "string" ? value.migration_status : "not_started",
      data_sources: dataSources,
    };
  }
  if (kind === "list") {
    if (!isRecord(value) || !Array.isArray(value.data_sources)) return null;
    const sources = value.data_sources.map(safeDetail);
    if (sources.some((item) => item === null)) return null;
    return {
      default_data_source_id:
        typeof value.default_data_source_id === "string" ? value.default_data_source_id : null,
      data_sources: sources,
    };
  }
  if (kind === "detail" || kind === "create") return safeDetail(value);
  if (kind === "revision") return safeRevisionDetail(value);
  if (kind === "runtimeStatus") return safeRuntimeStatus(value);
  if (kind === "operation") return safeOperation(value);
  if (kind === "test") {
    return isRecord(value) && value.ok === true
      ? { ok: true, data_source_id: value.data_source_id, revision_id: value.revision_id }
      : null;
  }
  if (kind === "schema") {
    if (!isRecord(value) || !Array.isArray(value.tables)) return null;
    return {
      data_source_id: value.data_source_id,
      revision_id: value.revision_id,
      foreign_keys_complete: value.foreign_keys_complete === true,
      warnings: Array.isArray(value.warnings)
        ? value.warnings.filter((warning): warning is string => typeof warning === "string")
        : [],
      tables: value.tables.flatMap((table) => {
        if (!isRecord(table) || typeof table.name !== "string" || !Array.isArray(table.columns))
          return [];
        return [
          {
            id: typeof table.id === "string" ? table.id : table.name,
            name: table.name,
            description: typeof table.description === "string" ? table.description : "",
            ...(typeof table.catalog === "string" ? { catalog: table.catalog } : {}),
            ...(typeof table.schema === "string" ? { schema: table.schema } : {}),
            columns: table.columns.flatMap((column) =>
              isRecord(column) && typeof column.name === "string" && typeof column.type === "string"
                ? [
                    {
                      name: column.name,
                      type: column.type,
                      nullable: column.nullable === true,
                      primary_key: column.primary_key === true,
                      description: typeof column.description === "string" ? column.description : "",
                    },
                  ]
                : [],
            ),
            foreign_keys: Array.isArray(table.foreign_keys)
              ? table.foreign_keys.flatMap((foreignKey) =>
                  isRecord(foreignKey) &&
                  typeof foreignKey.name === "string" &&
                  typeof foreignKey.column === "string" &&
                  typeof foreignKey.referenced_table === "string" &&
                  typeof foreignKey.referenced_column === "string"
                    ? [
                        {
                          name: foreignKey.name,
                          column: foreignKey.column,
                          referenced_table: foreignKey.referenced_table,
                          ...(typeof foreignKey.referenced_catalog === "string"
                            ? { referenced_catalog: foreignKey.referenced_catalog }
                            : {}),
                          ...(typeof foreignKey.referenced_schema === "string"
                            ? { referenced_schema: foreignKey.referenced_schema }
                            : {}),
                          referenced_column: foreignKey.referenced_column,
                        },
                      ]
                    : [],
                )
              : [],
          },
        ];
      }),
    };
  }
  if (
    kind === "default" &&
    isRecord(value) &&
    (typeof value.default_data_source_id === "string" || value.default_data_source_id === null)
  ) {
    return { default_data_source_id: value.default_data_source_id };
  }
  return null;
}

function errorCode(value: unknown): string | null {
  if (!isRecord(value)) return null;
  const detail = value.detail;
  const code =
    typeof value.code === "string"
      ? value.code
      : isRecord(detail) && typeof detail.code === "string"
        ? detail.code
        : null;
  return code && allowedErrorCodes.has(code) ? code : null;
}

export async function proxyWrenSettings(request: Request, segments: string[]) {
  const target = resolvePath(segments, request.method);
  if (!target) {
    return Response.json(
      { code: "DATA_SOURCE_NOT_FOUND", message: errorMessages.DATA_SOURCE_NOT_FOUND },
      {
        status: 404,
        headers: noStoreHeaders,
      },
    );
  }
  const access = await requireAgentSession(request, !["GET", "HEAD"].includes(request.method));
  if ("response" in access) return access.response;
  const hasBody = request.method === "POST" || request.method === "PUT";
  let body: string | undefined;
  if (hasBody) {
    const raw = await readLimitedBody(request);
    if (raw === null) {
      return Response.json(
        { code: "WREN_CONFIGURATION_INVALID", message: errorMessages.WREN_CONFIGURATION_INVALID },
        {
          status: 413,
          headers: noStoreHeaders,
        },
      );
    }
    let parsed: unknown;
    try {
      parsed = raw.trim() ? (JSON.parse(raw) as unknown) : undefined;
    } catch {
      return Response.json(
        { code: "WREN_CONFIGURATION_INVALID", message: errorMessages.WREN_CONFIGURATION_INVALID },
        {
          status: 400,
          headers: noStoreHeaders,
        },
      );
    }
    const isForm =
      target.kind === "create" || (target.kind === "detail" && request.method === "PUT");
    const valid = isForm
      ? validateFormBody(request.method, parsed)
      : validateBody(target.kind, request.method, parsed);
    if (!valid) {
      return Response.json(
        { code: "WREN_CONFIGURATION_INVALID", message: errorMessages.WREN_CONFIGURATION_INVALID },
        {
          status: 400,
          headers: noStoreHeaders,
        },
      );
    }
    body = JSON.stringify(parsed);
  }

  let upstream: Response;
  try {
    upstream = await fetch(agentUrl(target.path), {
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
      { code: "WREN_SETTINGS_UNAVAILABLE", message: errorMessages.WREN_SETTINGS_UNAVAILABLE },
      {
        status: 503,
        headers: noStoreHeaders,
      },
    );
  }

  const upstreamBody = await readJsonLimited(upstream);
  if (upstream.status === 401) await clearSessionCookie();
  if (!upstream.ok) {
    const code = errorCode(upstreamBody) ?? "WREN_SETTINGS_UNAVAILABLE";
    return Response.json(
      { code, message: errorMessages[code] },
      {
        status: upstream.status,
        headers: noStoreHeaders,
      },
    );
  }
  const projection = safePayload(upstreamBody, target.kind);
  if (projection === null) {
    return Response.json(
      { code: "WREN_SETTINGS_UNAVAILABLE", message: errorMessages.WREN_SETTINGS_UNAVAILABLE },
      {
        status: 502,
        headers: noStoreHeaders,
      },
    );
  }
  return Response.json(projection, { status: upstream.status, headers: noStoreHeaders });
}

export async function proxyDataSourceCatalog(request: Request) {
  if (request.method !== "GET") {
    return Response.json(
      { code: "DATA_SOURCE_NOT_FOUND", message: errorMessages.DATA_SOURCE_NOT_FOUND },
      {
        status: 405,
        headers: noStoreHeaders,
      },
    );
  }
  const access = await requireAgentSession(request);
  if ("response" in access) return access.response;
  try {
    const upstream = await fetch(agentUrl("/v1/data-sources"), {
      method: "GET",
      headers: { authorization: `Bearer ${access.token}`, accept: "application/json" },
      cache: "no-store",
      signal: request.signal,
    });
    if (upstream.status === 401) await clearSessionCookie();
    const body = await readJsonLimited(upstream);
    if (!upstream.ok) {
      const code = errorCode(body) ?? "WREN_SETTINGS_UNAVAILABLE";
      return Response.json(
        { code, message: errorMessages[code] },
        { status: upstream.status, headers: noStoreHeaders },
      );
    }
    const projection = safePayload(body, "catalog");
    if (projection === null) throw new Error("Invalid catalog response");
    return Response.json(projection, { status: upstream.status, headers: noStoreHeaders });
  } catch {
    return Response.json(
      { code: "WREN_SETTINGS_UNAVAILABLE", message: errorMessages.WREN_SETTINGS_UNAVAILABLE },
      {
        status: 503,
        headers: noStoreHeaders,
      },
    );
  }
}
