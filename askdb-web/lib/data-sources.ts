import { requestCsrfToken } from "@/lib/auth-api";

export type DataSourceSummary = {
  id: string;
  display_name: string;
  connector_type: string;
  enabled: boolean;
  runtime_status: "ready" | "not_ready" | "unavailable" | "disabled" | string;
  is_default: boolean;
};

export type DataSourceCatalog = {
  default_data_source_id: string | null;
  migration_status: "not_started" | "not_configured" | "complete" | "failed" | string;
  data_sources: DataSourceSummary[];
};

export function isChatAvailableDataSource(
  source: Pick<DataSourceSummary, "enabled" | "runtime_status">,
): boolean {
  return source.enabled && source.runtime_status === "ready";
}

export type DataSourceColumn = {
  name: string;
  type: string;
  nullable: boolean;
  primary_key: boolean;
  description?: string;
  hidden?: boolean;
};

export type DataSourceForeignKey = {
  name: string;
  column: string;
  referenced_table: string;
  referenced_column: string;
  referenced_catalog?: string;
  referenced_schema?: string;
};

export type DataSourceTable = {
  id: string;
  name: string;
  catalog?: string;
  schema?: string;
  description?: string;
  columns: DataSourceColumn[];
  foreign_keys?: DataSourceForeignKey[];
};

export type WrenModel = {
  table: string;
  name: string;
  description: string;
  columns: Array<{
    name: string;
    description: string;
    hidden: boolean;
    primary_key: boolean;
  }>;
};

export type WrenRelationship = {
  name: string;
  left_model: string;
  right_model: string;
  join_type: "one_to_one" | "one_to_many" | "many_to_one" | "many_to_many";
  condition: string;
};

export type WrenRule = { name: string; content: string };
export type WrenView = { name: string; description: string; sql: string };

export type WrenSemanticConfig = {
  tables: string[];
  models: WrenModel[];
  relationships: WrenRelationship[];
  rules: WrenRule[];
  views: WrenView[];
};

export type DataSourceConnection = Record<string, string | number | boolean | undefined>;

export type WrenConnectorField = {
  name: string;
  label: string;
  input_type: "text" | "password" | "hidden" | "file_base64" | string;
  placeholder: string;
  hint: string | null;
  required: boolean;
  default: string | null;
  sensitive: boolean;
  alias: string | null;
  examples: string[];
  accept: string | null;
};

export type WrenConnectorFieldGroup = {
  variant: string | null;
  fields: WrenConnectorField[];
};

export type WrenConnectorDefinition = {
  type: string;
  label: string;
  variants: string[];
  field_groups: WrenConnectorFieldGroup[];
};

export type WrenOperation = {
  id: string;
  data_source_id: string;
  revision_id: string | null;
  status: "running" | "active" | "failed";
  phase: string;
  error_code: string | null;
  message: string | null;
  created_at: string;
  updated_at: string;
};

export type WrenSourceConfig = DataSourceConnection & Partial<WrenSemanticConfig>;

export type DataSourceDetail = {
  data_source: {
    id: string;
    display_name: string;
    connector_type: string;
    enabled: boolean;
    active_revision_id: string | null;
    draft_revision_id: string | null;
    runtime_status: string;
    created_at: string;
    updated_at: string;
  };
  connection: DataSourceConnection & {
    configured_secret_fields: string[];
    credential_configured: boolean;
  };
  config: WrenSourceConfig;
  active_config?: WrenSourceConfig;
  revision: {
    id: string;
    status: string;
    error_code: string | null;
    mdl_digest: string | null;
  } | null;
  revisions?: Array<{
    id: string;
    status: string;
    error_code: string | null;
    mdl_digest: string | null;
    created_at: string;
  }>;
  last_operation: WrenOperation | null;
};

export type DataSourceRevisionDetail = {
  data_source: {
    id: string;
    display_name: string;
    connector_type: string;
  };
  revision: {
    id: string;
    status: string;
    error_code: string | null;
    mdl_digest: string | null;
    created_at: string;
    updated_at: string;
  };
  config: WrenSourceConfig;
};

export type SourceFormPayload = {
  display_name: string;
  connector_type: string;
  connection: DataSourceConnection;
  semantic: WrenSemanticConfig;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSourceSummary(value: unknown): value is DataSourceSummary {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.display_name === "string" &&
    typeof value.connector_type === "string" &&
    typeof value.enabled === "boolean" &&
    typeof value.runtime_status === "string" &&
    typeof value.is_default === "boolean"
  );
}

function isCatalog(value: unknown): value is DataSourceCatalog {
  return (
    isRecord(value) &&
    (typeof value.default_data_source_id === "string" || value.default_data_source_id === null) &&
    typeof value.migration_status === "string" &&
    Array.isArray(value.data_sources) &&
    value.data_sources.every(isSourceSummary)
  );
}

export async function fetchDataSourceCatalog(): Promise<DataSourceCatalog> {
  const response = await fetch("/api/data-sources", { cache: "no-store" });
  if (!response.ok) throw new Error("读取数据源列表失败，请重试。");
  const value: unknown = await response.json();
  if (!isCatalog(value)) throw new Error("数据源列表响应无效。");
  return {
    default_data_source_id: value.default_data_source_id,
    migration_status: value.migration_status,
    data_sources: value.data_sources.map((source) => ({
      id: source.id,
      display_name: source.display_name,
      connector_type: source.connector_type,
      enabled: source.enabled,
      runtime_status: source.runtime_status,
      is_default: source.is_default,
    })),
  };
}

async function settingsRequest<T>(segments: string[], init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const csrfToken = !["GET", "HEAD"].includes(method) ? await requestCsrfToken() : null;
  const response = await fetch(
    `/api/settings/wren/${segments.map((segment) => encodeURIComponent(segment)).join("/")}`,
    {
      ...init,
      cache: "no-store",
      headers: {
        ...(init.body ? { "content-type": "application/json" } : {}),
        ...(csrfToken ? { "x-csrf-token": csrfToken } : {}),
        ...init.headers,
      },
    },
  );
  let value: unknown;
  try {
    value = await response.json();
  } catch {
    throw new Error("Wren 设置服务返回了无效响应。");
  }
  if (!response.ok) {
    const body = value as { message?: unknown; detail?: { message?: unknown } } | null;
    const message =
      body && typeof body.message === "string"
        ? body.message
        : body && typeof body.detail?.message === "string"
          ? body.detail.message
          : "请求失败，请稍后重试。";
    throw new Error(message);
  }
  return value as T;
}

export function fetchDataSources(): Promise<{
  default_data_source_id: string | null;
  data_sources: DataSourceDetail[];
}> {
  return settingsRequest(["data-sources"]);
}

export function fetchWrenConnectors(): Promise<{ connectors: WrenConnectorDefinition[] }> {
  return settingsRequest(["connectors"]);
}

export function fetchDataSource(sourceId: string): Promise<DataSourceDetail> {
  return settingsRequest(["data-sources", sourceId]);
}

export function fetchDataSourceRevision(
  sourceId: string,
  revisionId: string,
): Promise<DataSourceRevisionDetail> {
  return settingsRequest(["data-sources", sourceId, "revisions", revisionId]);
}

export function createDataSource(payload: SourceFormPayload): Promise<DataSourceDetail> {
  return settingsRequest(["data-sources"], { method: "POST", body: JSON.stringify(payload) });
}

export function updateDataSource(
  sourceId: string,
  payload: SourceFormPayload,
): Promise<DataSourceDetail> {
  const { connector_type: _connectorType, ...body } = payload;
  return settingsRequest(["data-sources", sourceId], { method: "PUT", body: JSON.stringify(body) });
}

export function testDataSourceConnection(sourceId: string): Promise<{ ok: boolean }> {
  return settingsRequest(["data-sources", sourceId, "connection", "test"], { method: "POST" });
}

export function refreshDataSourceSchema(sourceId: string): Promise<{
  tables: DataSourceTable[];
  warnings?: string[];
}> {
  return settingsRequest(["data-sources", sourceId, "schema", "refresh"], { method: "POST" });
}

export function applyDataSource(sourceId: string): Promise<WrenOperation> {
  return settingsRequest(["data-sources", sourceId, "apply"], { method: "POST" });
}

export function fetchWrenOperation(operationId: string): Promise<WrenOperation> {
  return settingsRequest(["operations", operationId]);
}

export function setDefaultDataSource(
  sourceId: string | null,
): Promise<{ default_data_source_id: string | null }> {
  return settingsRequest(["default-data-source"], {
    method: "PUT",
    body: JSON.stringify({ data_source_id: sourceId }),
  });
}

export function deactivateDataSource(sourceId: string): Promise<DataSourceDetail> {
  return settingsRequest(["data-sources", sourceId, "deactivate"], { method: "POST" });
}

export function enableDataSource(sourceId: string): Promise<DataSourceDetail> {
  return settingsRequest(["data-sources", sourceId, "enable"], { method: "POST" });
}

export function rollbackDataSource(
  sourceId: string,
  revisionId: string,
): Promise<DataSourceDetail> {
  return settingsRequest(["data-sources", sourceId, "rollback", revisionId], { method: "POST" });
}
