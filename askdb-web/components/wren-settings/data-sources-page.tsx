"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIcon,
  AlertCircleIcon,
  CheckCircle2Icon,
  CirclePlusIcon,
  DatabaseIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
  SaveIcon,
  ServerIcon,
  ShieldCheckIcon,
  Trash2Icon,
  WandSparklesIcon,
} from "lucide-react";

import {
  applyDataSource,
  createDataSource,
  deactivateDataSource,
  enableDataSource,
  fetchDataSource,
  fetchDataSourceCatalog,
  fetchDataSources,
  fetchDataSourceRevision,
  fetchWrenConnectors,
  fetchWrenOperation,
  refreshDataSourceSchema,
  rollbackDataSource,
  setDefaultDataSource,
  testDataSourceConnection,
  updateDataSource,
  type DataSourceConnection,
  type DataSourceDetail,
  type DataSourceForeignKey,
  type DataSourceRevisionDetail,
  type DataSourceTable,
  type SourceFormPayload,
  type WrenModel,
  type WrenConnectorDefinition,
  type WrenConnectorField,
  type WrenOperation,
  type WrenRelationship,
  type WrenRule,
  type WrenSemanticConfig,
  type WrenSourceConfig,
  type WrenView,
} from "@/lib/data-sources";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { SettingsPageHeader } from "@/components/settings/settings-page-header";

const WREN_APPLY_FAILURE_FALLBACK = "Wren 配置应用失败，请修正配置后重试。";
const semanticConfigKeys = new Set([
  "tables",
  "models",
  "relationships",
  "ignored_foreign_keys",
  "rules",
  "views",
]);

const emptySemantic = (): WrenSemanticConfig => ({
  tables: [],
  models: [],
  relationships: [],
  ignored_foreign_keys: [],
  rules: [],
  views: [],
});

function defaultConnection(connectorType: string): DataSourceConnection {
  return connectorType === "mysql" ? { port: "3306" } : {};
}

const phaseLabels: Record<string, string> = {
  queued: "等待开始",
  testing_connection: "测试数据库连接",
  introspecting: "读取表结构",
  validating: "校验 Wren 配置",
  building: "构建语义模型",
  initializing_runtime: "启动查询运行环境",
  active: "已生效",
  failed: "应用失败",
};

function detailSemantic(detail: DataSourceDetail): WrenSemanticConfig {
  return {
    tables: detail.config.tables ?? [],
    models: detail.config.models ?? [],
    relationships: detail.config.relationships ?? [],
    ignored_foreign_keys: detail.config.ignored_foreign_keys ?? [],
    rules: detail.config.rules ?? [],
    views: detail.config.views ?? [],
  };
}

function baseFingerprint(payload: SourceFormPayload, sensitiveFields: string[] = []) {
  const connection = Object.fromEntries(
    Object.entries(payload.connection).filter(
      ([key, value]) => value !== undefined && !sensitiveFields.includes(key),
    ),
  );
  return JSON.stringify({ ...payload, connection });
}

function sourceTables(semantic: WrenSemanticConfig): DataSourceTable[] {
  return semantic.tables.map((name) => {
    const model = semantic.models.find((item) => item.table === name);
    return {
      id: name,
      name,
      description: model?.description ?? "",
      columns: (model?.columns ?? []).map((column) => ({
        name: column.name,
        type: "",
        nullable: true,
        primary_key: column.primary_key,
        description: column.description,
        hidden: column.hidden,
      })),
    };
  });
}

function collectionDiff<T>(active: T[] = [], draft: T[] = [], keyOf: (item: T) => string) {
  const before = new Map(active.map((item) => [keyOf(item), JSON.stringify(item)]));
  const after = new Map(draft.map((item) => [keyOf(item), JSON.stringify(item)]));
  let added = 0;
  let removed = 0;
  let changed = 0;
  for (const [key, value] of after) {
    if (!before.has(key)) added += 1;
    else if (before.get(key) !== value) changed += 1;
  }
  for (const key of before.keys()) if (!after.has(key)) removed += 1;
  return { added, removed, changed };
}

function revisionDiff(active: WrenSourceConfig = {}, draft: WrenSourceConfig = {}) {
  const activeModels = active.models ?? [];
  const draftModels = draft.models ?? [];
  const diff = [
    { name: "数据表", ...collectionDiff(active.tables ?? [], draft.tables ?? [], (item) => item) },
    { name: "语义模型", ...collectionDiff(activeModels, draftModels, (item) => item.table) },
    {
      name: "关系",
      ...collectionDiff(
        active.relationships ?? [],
        draft.relationships ?? [],
        (item) => item.name || `${item.left_model}-${item.right_model}`,
      ),
    },
    {
      name: "业务规则",
      ...collectionDiff(active.rules ?? [], draft.rules ?? [], (item) => item.name),
    },
    { name: "视图", ...collectionDiff(active.views ?? [], draft.views ?? [], (item) => item.name) },
  ];
  const connectionKeys = [...new Set([...Object.keys(active), ...Object.keys(draft)])].filter(
    (key) => !semanticConfigKeys.has(key),
  );
  const connectionChanged = connectionKeys.some(
    (key) => JSON.stringify(active[key]) !== JSON.stringify(draft[key]),
  );
  return { diff, connectionChanged };
}

function modelNameFor(table: string) {
  const words = table.split(/[^A-Za-z0-9\u4e00-\u9fa5]+/).filter(Boolean);
  return words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join("") || table;
}

function isPythonBytesRepr(value: string) {
  return /^b['"][\s\S]*\\x[0-9a-f]{2}['"]$/i.test(value.trim());
}

function createModelsForSelection(
  selectedTables: string[],
  schema: DataSourceTable[],
  current: WrenModel[],
): WrenModel[] {
  return selectedTables.flatMap((table) => {
    const existing = current.find((model) => model.table === table);
    const discovered = schema.find((item) => (item.id || item.name) === table);
    if (existing) {
      const existingDescription = existing.description.trim();
      return [
        {
          ...existing,
          description:
            existingDescription && !isPythonBytesRepr(existingDescription)
              ? existing.description
              : (discovered?.description ?? ""),
          columns: discovered
            ? discovered.columns.map((column) => {
                const old = existing.columns.find((item) => item.name === column.name);
                const oldDescription = old?.description.trim() ?? "";
                return {
                  name: column.name,
                  description:
                    oldDescription && !isPythonBytesRepr(oldDescription)
                      ? (old?.description ?? "")
                      : (column.description ?? ""),
                  hidden: old?.hidden ?? column.hidden ?? false,
                  primary_key: column.primary_key,
                };
              })
            : existing.columns,
        },
      ];
    }
    if (!discovered) return [];
    return [
      {
        table,
        name: modelNameFor(discovered.name),
        description: discovered.description ?? "",
        columns: discovered.columns.map((column) => ({
          name: column.name,
          description: column.description ?? "",
          hidden: false,
          primary_key: column.primary_key,
        })),
      },
    ];
  });
}

function foreignKeyId(tableId: string, foreignKey: DataSourceForeignKey): string {
  return JSON.stringify([
    tableId,
    foreignKey.name || `column:${foreignKey.column}`,
    foreignKey.referenced_table,
  ]);
}

function relationshipName(value: string): string {
  if (value.length <= 128) return value;
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash = Math.imul(hash ^ value.charCodeAt(index), 16777619);
  }
  const suffix = `_${(hash >>> 0).toString(36)}`;
  return `${value.slice(0, 128 - suffix.length)}${suffix}`;
}

function discoverForeignKeyRelationships(
  schema: DataSourceTable[],
  selectedTables: string[],
  models: WrenModel[],
): WrenRelationship[] {
  const selectedModels = models.filter((model) => selectedTables.includes(model.table));
  const modelForTable = (tableId: string) => {
    const exact = selectedModels.find((model) => model.table === tableId);
    if (exact) return exact;
    const candidates = selectedModels.filter(
      (model) =>
        model.table.endsWith(`.${tableId}`) || model.table.split(".").pop() === tableId,
    );
    return candidates.length === 1 ? candidates[0] : undefined;
  };
  const grouped = new Map<
    string,
    {
      source: WrenModel;
      target: WrenModel;
      constraint: string;
      foreignKeyId: string;
      conditions: string[];
    }
  >();

  for (const table of schema) {
    const tableId = table.id || table.name;
    const source = modelForTable(tableId);
    if (!source) continue;
    for (const foreignKey of table.foreign_keys ?? []) {
      const target = modelForTable(foreignKey.referenced_table);
      if (!target) continue;
      const id = foreignKeyId(tableId, foreignKey);
      const item = grouped.get(id) ?? {
        source,
        target,
        constraint: foreignKey.name || foreignKey.column,
        foreignKeyId: id,
        conditions: [],
      };
      item.conditions.push(
        `${source.name}.${foreignKey.column} = ${target.name}.${foreignKey.referenced_column}`,
      );
      grouped.set(id, item);
    }
  }

  return [...grouped.values()].map((item) => ({
    name: relationshipName(`${item.source.name}_${item.target.name}_${item.constraint}`),
    left_model: item.source.name,
    right_model: item.target.name,
    join_type: "many_to_one" as const,
    condition: item.conditions.join(" AND "),
    foreign_key_id: item.foreignKeyId,
  }));
}

function includeDirectForeignKeyNeighbors(
  selectedTables: string[],
  schema: DataSourceTable[],
): string[] {
  const selected = new Set(selectedTables);
  const anchors = new Set(selectedTables);
  const tableId = (table: DataSourceTable) => table.id || table.name;
  const resolveTable = (reference: string) => {
    const exact = schema.find((table) => tableId(table) === reference);
    if (exact) return tableId(exact);
    const candidates = schema.filter(
      (table) =>
        table.name === reference ||
        tableId(table).endsWith(`.${reference}`) ||
        reference.endsWith(`.${table.name}`),
    );
    return candidates.length === 1 ? tableId(candidates[0]) : undefined;
  };

  for (const table of schema) {
    const source = tableId(table);
    for (const foreignKey of table.foreign_keys ?? []) {
      const target = resolveTable(foreignKey.referenced_table);
      if (!target) continue;
      if (anchors.has(source)) selected.add(target);
      if (anchors.has(target)) selected.add(source);
    }
  }

  return [...selectedTables, ...[...selected].filter((table) => !selectedTables.includes(table))];
}

function mergeForeignKeyRelationships(
  current: WrenSemanticConfig,
  schema: DataSourceTable[],
  selectedTables: string[],
  models: WrenModel[],
  foreignKeysComplete: boolean,
): WrenSemanticConfig {
  const suggestions = discoverForeignKeyRelationships(schema, selectedTables, models);
  const schemaForeignKeyIds = new Set(
    schema.flatMap((table) =>
      (table.foreign_keys ?? []).map((foreignKey) =>
        foreignKeyId(table.id || table.name, foreignKey),
      ),
    ),
  );
  const ignoredForeignKeys = foreignKeysComplete
    ? current.ignored_foreign_keys.filter((id) => schemaForeignKeyIds.has(id))
    : current.ignored_foreign_keys;
  const ignored = new Set(ignoredForeignKeys);
  const existingByForeignKey = new Map<string, WrenRelationship>();
  for (const relationship of current.relationships) {
    if (typeof relationship.foreign_key_id === "string") {
      existingByForeignKey.set(relationship.foreign_key_id, relationship);
    }
  }
  const selectedModelNames = new Set(
    models.filter((model) => selectedTables.includes(model.table)).map((model) => model.name),
  );
  const claimedManualRelationships = new Set<number>();
  const imported: WrenRelationship[] = [];
  const representedConditions = new Set<string>();

  for (const suggestion of suggestions) {
    const id = suggestion.foreign_key_id;
    if (!id || ignored.has(id)) continue;
    const existing = existingByForeignKey.get(id);
    if (existing) {
      imported.push(existing);
      representedConditions.add(existing.condition);
      continue;
    }
    const matchingLegacyIndex = current.relationships.findIndex(
      (relationship, index) =>
        !relationship.foreign_key_id &&
        !claimedManualRelationships.has(index) &&
        relationship.name === suggestion.name,
    );
    const matchingManualIndex =
      matchingLegacyIndex >= 0
        ? matchingLegacyIndex
        : current.relationships.findIndex(
            (relationship, index) =>
              !relationship.foreign_key_id &&
              !claimedManualRelationships.has(index) &&
              relationship.condition === suggestion.condition,
          );
    if (matchingManualIndex >= 0) {
      claimedManualRelationships.add(matchingManualIndex);
      const matchingManual = current.relationships[matchingManualIndex];
      imported.push({ ...matchingManual, foreign_key_id: id });
      representedConditions.add(matchingManual.condition);
      continue;
    }
    if (representedConditions.has(suggestion.condition)) continue;
    imported.push(suggestion);
    representedConditions.add(suggestion.condition);
  }

  const manual = current.relationships.filter(
    (relationship, index) =>
      !relationship.foreign_key_id &&
      !claimedManualRelationships.has(index) &&
      selectedModelNames.has(relationship.left_model) &&
      selectedModelNames.has(relationship.right_model),
  );
  if (!foreignKeysComplete) {
    const importedIds = new Set(
      imported.flatMap((relationship) =>
        typeof relationship.foreign_key_id === "string" ? [relationship.foreign_key_id] : [],
      ),
    );
    for (const relationship of current.relationships) {
      if (
        typeof relationship.foreign_key_id === "string" &&
        !importedIds.has(relationship.foreign_key_id) &&
        selectedModelNames.has(relationship.left_model) &&
        selectedModelNames.has(relationship.right_model)
      ) {
        imported.push(relationship);
        importedIds.add(relationship.foreign_key_id);
      }
    }
  }
  return {
    ...current,
    relationships: [...manual, ...imported],
    ignored_foreign_keys: ignoredForeignKeys,
  };
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
  required = false,
  autoComplete,
}: {
  label: string;
  value: string | number;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
  required?: boolean;
  autoComplete?: string;
}) {
  return (
    <label className="grid gap-1.5 text-xs font-medium text-[#615b51]">
      {label}
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        required={required}
        autoComplete={autoComplete}
        onChange={(event) => onChange(event.target.value)}
        className="h-10 w-full rounded-lg border border-[#e7e2d8] bg-white px-3 text-sm font-normal text-[#393630] outline-none transition focus:border-[#d8cbb9] focus:ring-2 focus:ring-[#c57650]/20"
      />
    </label>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-[#e7e2d8] bg-[#fbfaf7] p-4 shadow-[0_1px_2px_rgba(59,48,35,0.03)] sm:p-5">
      <div className="mb-4">
        <h2 className="text-sm font-semibold text-[#393630]">{title}</h2>
        {description && <p className="mt-1 text-xs leading-5 text-[#89847a]">{description}</p>}
      </div>
      {children}
    </section>
  );
}

function ActionButton({
  children,
  onClick,
  disabled,
  variant = "outline",
  title,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  variant?: "outline" | "primary" | "quiet" | "danger";
  title?: string;
}) {
  const styles = {
    outline: "border border-[#e2d9cb] bg-white text-[#514b42] hover:bg-[#f6f3ed]",
    primary: "border border-[#b96e4a] bg-[#c57650] text-white hover:bg-[#ae6544]",
    quiet: "border border-transparent bg-transparent text-[#77736b] hover:bg-[#eeebe4]",
    danger: "border border-[#efd5cd] bg-[#fffaf8] text-[#9c4037] hover:bg-[#f8e9e4]",
  };
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex h-9 items-center justify-center gap-2 rounded-lg px-3 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none ${styles[variant]}`}
    >
      {children}
    </button>
  );
}

function formatRevisionDate(value: string) {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function revisionStatusLabel(status: string, isActive: boolean) {
  if (isActive) return "当前生效";
  if (status === "draft") return "草稿";
  if (status === "failed") return "失败";
  if (status === "retired") return "历史版本";
  if (status === "active") return "已生效";
  return status || "未知状态";
}

function RevisionNavigationItem({
  revision,
  isActive,
  selected,
  loading,
  onSelect,
}: {
  revision: NonNullable<DataSourceDetail["revisions"]>[number];
  isActive: boolean;
  selected: boolean;
  loading: boolean;
  onSelect: () => void;
}) {
  const failed = revision.status === "failed";
  return (
    <button
      type="button"
      title={revision.id}
      aria-pressed={selected}
      onClick={onSelect}
      disabled={loading}
      className={`flex w-full items-start gap-2 rounded-lg border px-2.5 py-2 text-left transition focus-visible:ring-2 focus-visible:ring-[#c57650] focus-visible:outline-none disabled:cursor-wait disabled:opacity-70 ${
        selected
          ? "border-[#d8c5af] bg-white shadow-[0_1px_3px_rgba(59,48,35,0.06)]"
          : isActive
            ? "border-[#dce6d5] bg-[#eef2e9] hover:bg-[#e7eee2]"
            : "border-transparent hover:border-[#ebe6dd] hover:bg-white/70"
      }`}
    >
      <span
        aria-hidden="true"
        className={`mt-1.5 size-2 shrink-0 rounded-full ${
          isActive ? "bg-[#638252]" : failed ? "bg-[#b64d43]" : "bg-[#c7bda9]"
        }`}
      />
      <span className="min-w-0 flex-1">
        <span className="flex items-center justify-between gap-1.5">
          <span className="truncate font-mono text-[10px] text-[#5e594f]">
            {revision.id.length > 19 ? `${revision.id.slice(0, 16)}…` : revision.id}
          </span>
          <span
            className={`shrink-0 rounded-full px-1.5 py-0.5 text-[9px] font-medium ${
              isActive
                ? "bg-[#dfe9d9] text-[#527249]"
                : failed
                  ? "bg-[#f8e9e4] text-[#9c4037]"
                  : revision.status === "draft"
                    ? "bg-[#f2e7dc] text-[#8c6149]"
                    : "bg-[#eeeae2] text-[#77736b]"
            }`}
          >
            {revisionStatusLabel(revision.status, isActive)}
          </span>
        </span>
        <span className="mt-1 block text-[9px] text-[#989286]">
          {formatRevisionDate(revision.created_at)}
        </span>
      </span>
    </button>
  );
}

function RevisionPreview({
  snapshot,
  activeRevisionId,
  connector,
  disabled,
  draftDirty,
  onReturnToDraft,
  onRequestActivation,
}: {
  snapshot: DataSourceRevisionDetail;
  activeRevisionId: string | null;
  connector: WrenConnectorDefinition | null;
  disabled: boolean;
  draftDirty: boolean;
  onReturnToDraft: () => void;
  onRequestActivation: () => void;
}) {
  const { data_source: source, revision, config } = snapshot;
  const isActive = revision.id === activeRevisionId;
  const canActivate =
    !isActive && (revision.status === "active" || revision.status === "retired");
  const connectorFields = new Map(
    (connector?.field_groups ?? []).flatMap((group) =>
      group.fields.map((field) => [field.name, field] as const),
    ),
  );
  const connectionEntries = Object.entries(config as unknown as Record<string, unknown>).filter(
    ([key, value]) => {
      const field = connectorFields.get(key);
      return (
        !semanticConfigKeys.has(key) &&
        value !== undefined &&
        value !== null &&
        value !== "" &&
        field?.sensitive !== true &&
        field?.input_type !== "hidden"
      );
    },
  );
  const tables = config.tables ?? [];
  const models = config.models ?? [];
  const relationships = config.relationships ?? [];
  const rules = config.rules ?? [];
  const views = config.views ?? [];
  const statusLabel = revisionStatusLabel(revision.status, isActive);

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-[#e7e2d8] bg-[#fbfaf7] p-5 shadow-[0_1px_2px_rgba(59,48,35,0.03)] sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold tracking-[0.13em] text-[#9b9589]">
              版本预览 · 只读
            </p>
            <h2 className="mt-1 text-xl font-semibold tracking-tight text-[#393630]">
              {source.display_name}
            </h2>
            <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-[#89847a]">
              <span>{formatRevisionDate(revision.created_at)}</span>
              <span className="rounded-full bg-[#eeeae2] px-2 py-0.5">{statusLabel}</span>
              <span>{connector?.label ?? source.connector_type}</span>
            </div>
            <code className="mt-2 block break-all font-mono text-[10px] text-[#77736b]" title={revision.id}>
              {revision.id}
            </code>
            {revision.mdl_digest && (
              <p className="mt-1 break-all text-[10px] text-[#989286]">
                模型摘要：<code className="font-mono">{revision.mdl_digest}</code>
              </p>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <ActionButton onClick={onReturnToDraft}>
              返回草稿继续编辑
            </ActionButton>
            {canActivate && (
              <ActionButton onClick={onRequestActivation} disabled={disabled} variant="primary">
                切换到此版本
              </ActionButton>
            )}
            {isActive && (
              <span className="inline-flex h-9 items-center rounded-lg bg-[#e7eee2] px-3 text-xs font-medium text-[#527249]">
                当前生效
              </span>
            )}
          </div>
        </div>
        <p className="mt-4 border-t border-[#eee9df] pt-3 text-[11px] text-[#89847a]">
          查看历史版本不会修改草稿或当前运行版本。数据库凭证内容不会在此展示。
        </p>
      </section>

      {draftDirty && (
        <div role="status" className="rounded-xl border border-[#e9dcc7] bg-[#fffaf0] px-4 py-3 text-xs text-[#8c6149]">
          草稿有未保存的修改；返回草稿后可以继续编辑。
        </div>
      )}

      {revision.status === "failed" && (
        <div role="status" className="rounded-xl border border-[#efd5cd] bg-[#fffaf8] px-4 py-3 text-xs text-[#9c4037]">
          此版本应用失败，无法切换生效。{revision.error_code ? ` 错误代码：${revision.error_code}` : ""}
        </div>
      )}

      <Section title="连接信息" description="仅显示非敏感连接参数。">
        {connectionEntries.length ? (
          <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {connectionEntries.map(([key, value]) => (
              <div key={key} className="min-w-0 rounded-xl border border-[#ebe6dd] bg-white/70 px-3 py-2.5">
                <dt className="text-[10px] text-[#89847a]">{connectorFields.get(key)?.label ?? key.replaceAll("_", " ")}</dt>
                <dd className="mt-1 break-all text-xs text-[#514b42]">{String(value)}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="text-xs text-[#89847a]">此版本没有连接参数。</p>
        )}
      </Section>

      <Section title={`数据表 · ${tables.length}`}>
        {tables.length ? (
          <div className="flex flex-wrap gap-2">
            {tables.map((table) => (
              <span key={table} className="rounded-lg border border-[#e7e2d8] bg-white px-2.5 py-1.5 text-xs text-[#514b42]">
                {table}
              </span>
            ))}
          </div>
        ) : (
          <p className="text-xs text-[#89847a]">此版本未选择数据表。</p>
        )}
      </Section>

      <Section title={`语义模型 · ${models.length}`}>
        {models.length ? (
          <div className="space-y-2">
            {models.map((model) => (
              <article key={model.table} className="rounded-xl border border-[#ebe6dd] bg-white/70 p-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="text-xs font-semibold text-[#514b42]">{model.name}</h3>
                  <code className="text-[10px] text-[#89847a]">{model.table}</code>
                </div>
                {model.description && <p className="mt-1 text-xs leading-5 text-[#77736b]">{model.description}</p>}
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {model.columns.map((column) => (
                    <span key={column.name} className="rounded-md bg-[#f1eee7] px-2 py-1 text-[10px] text-[#615b51]">
                      {column.name}{column.primary_key ? " · 主键" : ""}{column.hidden ? " · 隐藏" : ""}
                    </span>
                  ))}
                </div>
              </article>
            ))}
          </div>
        ) : (
          <p className="text-xs text-[#89847a]">此版本没有语义模型。</p>
        )}
      </Section>

      <Section title={`关系 · ${relationships.length}`}>
        {relationships.length ? (
          <div className="space-y-2">
            {relationships.map((relationship, index) => (
              <article key={relationship.name || `${relationship.left_model}-${relationship.right_model}-${index}`} className="rounded-xl border border-[#ebe6dd] bg-white/70 p-3">
                <p className="text-xs font-medium text-[#514b42]">
                  {relationship.name || "未命名关系"} <span className="px-1 text-[#aaa397]">·</span>
                  {relationship.left_model} → {relationship.right_model}
                </p>
                <p className="mt-1 text-[10px] text-[#89847a]">{relationship.join_type}</p>
                <code className="mt-2 block break-all text-[10px] leading-5 text-[#77736b]">{relationship.condition}</code>
              </article>
            ))}
          </div>
        ) : (
          <p className="text-xs text-[#89847a]">此版本没有关系定义。</p>
        )}
      </Section>

      <Section title={`业务规则 · ${rules.length}`}>
        {rules.length ? (
          <div className="space-y-2">
            {rules.map((rule, index) => (
              <details key={`${rule.name}-${index}`} className="rounded-xl border border-[#ebe6dd] bg-white/70 px-3 py-2.5">
                <summary className="cursor-pointer text-xs font-medium text-[#514b42]">{rule.name || "未命名规则"}</summary>
                <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-[#77736b]">{rule.content}</p>
              </details>
            ))}
          </div>
        ) : (
          <p className="text-xs text-[#89847a]">此版本没有业务规则。</p>
        )}
      </Section>

      <Section title={`视图 · ${views.length}`}>
        {views.length ? (
          <div className="space-y-2">
            {views.map((view, index) => (
              <details key={`${view.name}-${index}`} className="rounded-xl border border-[#ebe6dd] bg-white/70 px-3 py-2.5">
                <summary className="cursor-pointer text-xs font-medium text-[#514b42]">{view.name || "未命名视图"}</summary>
                {view.description && <p className="mt-2 text-xs leading-5 text-[#77736b]">{view.description}</p>}
                <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-[#f4f1e9] p-3 text-[10px] leading-5 text-[#514b42]">{view.sql}</pre>
              </details>
            ))}
          </div>
        ) : (
          <p className="text-xs text-[#89847a]">此版本没有视图。</p>
        )}
      </Section>
    </div>
  );
}

export function DataSourcesPage() {
  const [sources, setSources] = useState<DataSourceDetail[]>([]);
  const [connectors, setConnectors] = useState<WrenConnectorDefinition[]>([]);
  const [connectorType, setConnectorType] = useState("mysql");
  const [defaultSourceId, setDefaultSourceId] = useState<string | null>(null);
  const [catalogStatus, setCatalogStatus] = useState("not_started");
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<DataSourceDetail | null>(null);
  const [selectedRevisionId, setSelectedRevisionId] = useState<string | null>(null);
  const [revisionPreview, setRevisionPreview] = useState<DataSourceRevisionDetail | null>(null);
  const [revisionLoadingId, setRevisionLoadingId] = useState<string | null>(null);
  const [revisionPreviewError, setRevisionPreviewError] = useState("");
  const [revisionToActivate, setRevisionToActivate] = useState<
    DataSourceRevisionDetail["revision"] | null
  >(null);
  const revisionPreviewRequest = useRef(0);
  const [displayName, setDisplayName] = useState("");
  const [connection, setConnection] = useState<DataSourceConnection>(() =>
    defaultConnection("mysql"),
  );
  const [secretValues, setSecretValues] = useState<Record<string, string>>({});
  const [configuredSecretFields, setConfiguredSecretFields] = useState<string[]>([]);
  const [secretChanged, setSecretChanged] = useState(false);
  const [semantic, setSemantic] = useState<WrenSemanticConfig>(emptySemantic);
  const [schema, setSchema] = useState<DataSourceTable[]>([]);
  const [schemaForeignKeysComplete, setSchemaForeignKeysComplete] = useState(false);
  const [savedFingerprint, setSavedFingerprint] = useState("");
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState("");
  const [operation, setOperation] = useState<WrenOperation | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const connectorDefinition = useMemo(
    () => connectors.find((item) => item.type === connectorType) ?? null,
    [connectorType, connectors],
  );
  const connectorVariants = connectorDefinition?.variants ?? [];
  const activeFieldGroup = useMemo(() => {
    if (!connectorDefinition) return null;
    const variantField = connectorDefinition.field_groups
      .flatMap((group) => group.fields)
      .find((field) => field.name.endsWith("_type"));
    const variant = variantField ? String(connection[variantField.name] ?? "") : "";
    return (
      connectorDefinition.field_groups.find((group) => group.variant === variant) ??
      connectorDefinition.field_groups[0] ??
      null
    );
  }, [connection, connectorDefinition]);
  const sensitiveFieldNames = activeFieldGroup?.fields
    .filter((field) => field.sensitive)
    .map((field) => field.name) ?? [];
  const missingConnectionFields = (activeFieldGroup?.fields ?? []).filter((field) => {
    if (!field.required || field.input_type === "hidden") return false;
    if (field.sensitive) {
      return !secretValues[field.name]?.trim() && !configuredSecretFields.includes(field.name);
    }
    const value = connection[field.name] ?? field.default;
    return !(
      (typeof value === "string" && value.trim().length > 0) ||
      (typeof value === "number" && Number.isFinite(value)) ||
      typeof value === "boolean"
    );
  });
  const connectionReady = Boolean(activeFieldGroup) && missingConnectionFields.length === 0;
  const actionBlockReason = working
    ? "操作正在处理中，请稍候。"
    : operation?.status === "running"
      ? "Wren 配置正在处理中，请先刷新操作状态。"
      : !displayName.trim()
        ? "请先填写数据源名称。"
        : !activeFieldGroup
          ? "正在读取连接字段定义，请稍候。"
          : missingConnectionFields.length > 0
            ? `请补齐必填连接字段：${missingConnectionFields.map((field) => field.label).join("、")}`
            : "";

  const payload = useMemo<SourceFormPayload>(
    () => ({
      display_name: displayName.trim(),
      connector_type: connectorType,
      connection: {
        ...Object.fromEntries(
          (activeFieldGroup?.fields ?? [])
            .filter((field) => !field.sensitive && field.default !== null)
            .map((field) => [field.name, field.default ?? undefined] as const),
        ),
        ...connection,
        ...Object.fromEntries(
          Object.entries(secretValues).filter(([, value]) => Boolean(value)),
        ),
      },
      semantic,
    }),
    [activeFieldGroup, connection, connectorType, displayName, secretValues, semantic],
  );
  const dirty =
    Boolean(displayName.trim()) &&
    (!detail || baseFingerprint(payload, sensitiveFieldNames) !== savedFingerprint || secretChanged);
  const selectedTables = semantic.tables;
  const modelNames = semantic.models.map((model) => model.name);
  const tableOptions = useMemo(() => {
    if (schema.length) return schema;
    return sourceTables(semantic);
  }, [schema, semantic]);

  const emitCatalogUpdated = useCallback(() => {
    window.dispatchEvent(new Event("askdb:data-source-catalog-updated"));
  }, []);

  const hydrateDetail = useCallback((next: DataSourceDetail) => {
    revisionPreviewRequest.current += 1;
    setSelectedRevisionId(null);
    setRevisionPreview(null);
    setRevisionLoadingId(null);
    setRevisionPreviewError("");
    setRevisionToActivate(null);
    setDetail(next);
    setOperation(next.last_operation);
    setSelectedId(next.data_source.id);
    setDisplayName(next.data_source.display_name);
    setConnectorType(next.data_source.connector_type);
    setConnection({
      ...defaultConnection(next.data_source.connector_type),
      ...Object.fromEntries(
        Object.entries(next.config).filter(([key]) => !semanticConfigKeys.has(key)),
      ),
    });
    setSecretValues({});
    setConfiguredSecretFields(next.connection.configured_secret_fields ?? []);
    setSecretChanged(false);
    const nextSemantic = detailSemantic(next);
    setSemantic(nextSemantic);
    setSchema(sourceTables(nextSemantic));
    setSchemaForeignKeysComplete(false);
    setSavedFingerprint(
      baseFingerprint({
        display_name: next.data_source.display_name,
        connector_type: next.data_source.connector_type,
        connection: Object.fromEntries(
          Object.entries(next.config).filter(([key]) => !semanticConfigKeys.has(key)),
        ),
        semantic: nextSemantic,
      }, next.connection.configured_secret_fields ?? []),
    );
  }, []);

  const loadSources = useCallback(
    async (preferredId?: string) => {
      const [response, catalog, connectorResponse] = await Promise.all([
        fetchDataSources(),
        fetchDataSourceCatalog(),
        fetchWrenConnectors(),
      ]);
      setSources(response.data_sources);
      setConnectors(connectorResponse.connectors);
      setCatalogStatus(catalog.migration_status);
      setDefaultSourceId(response.default_data_source_id);
      const targetId =
        preferredId ||
        selectedId ||
        response.default_data_source_id ||
        response.data_sources[0]?.data_source.id;
      const existing = response.data_sources.find((item) => item.data_source.id === targetId);
      if (existing) hydrateDetail(existing);
      else if (!targetId) startNewSource();
    },
    [hydrateDetail, selectedId],
  );

  function startNewSource() {
    revisionPreviewRequest.current += 1;
    setSelectedRevisionId(null);
    setRevisionPreview(null);
    setRevisionLoadingId(null);
    setRevisionPreviewError("");
    setRevisionToActivate(null);
    setDetail(null);
    setSelectedId("");
    setDisplayName("");
    setConnectorType("mysql");
    setConnection(defaultConnection("mysql"));
    setSecretValues({});
    setConfiguredSecretFields([]);
    setSecretChanged(false);
    setSemantic(emptySemantic());
    setSchema([]);
    setSchemaForeignKeysComplete(false);
    setSavedFingerprint("");
    setOperation(null);
    setError("");
    setNotice("填写连接信息后保存，即可测试连接并读取表结构。");
  }

  useEffect(() => {
    let active = true;
    void Promise.all([fetchDataSources(), fetchDataSourceCatalog(), fetchWrenConnectors()])
      .then(([response, catalog, connectorResponse]) => {
        if (!active) return;
        setSources(response.data_sources);
        setConnectors(connectorResponse.connectors);
        setDefaultSourceId(response.default_data_source_id);
        setCatalogStatus(catalog.migration_status);
        const targetId =
          response.default_data_source_id ??
          catalog.default_data_source_id ??
          response.data_sources[0]?.data_source.id;
        const existing = response.data_sources.find((item) => item.data_source.id === targetId);
        if (existing) hydrateDetail(existing);
        else startNewSource();
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "读取 Wren 数据源失败。");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [hydrateDetail]);

  function setConnectionField(key: string, value: DataSourceConnection[string]) {
    setConnection((current) => ({ ...current, [key]: value }));
    setSchema([]);
    setSchemaForeignKeysComplete(false);
    setNotice("连接参数已修改，请重新测试连接后再应用。");
  }

  function setSecretField(name: string, value: string) {
    setSecretValues((current) => ({ ...current, [name]: value }));
    setSecretChanged(true);
    setSchema([]);
    setSchemaForeignKeysComplete(false);
    setNotice("凭证已修改，请重新测试连接后再应用。");
  }

  function chooseConnector(nextType: string) {
    const definition = connectors.find((item) => item.type === nextType);
    const firstGroup = definition?.field_groups[0];
    const initial: DataSourceConnection = defaultConnection(nextType);
    for (const field of firstGroup?.fields ?? []) {
      if (field.default !== null) initial[field.name] = field.default;
      if (field.name.endsWith("_type") && firstGroup?.variant) {
        initial[field.name] = firstGroup.variant;
      }
    }
    setConnectorType(nextType);
    setConnection(initial);
    setSecretValues({});
    setConfiguredSecretFields([]);
    setSecretChanged(false);
    setSemantic(emptySemantic());
    setSchema([]);
    setSchemaForeignKeysComplete(false);
    setNotice("填写连接信息后保存，即可测试连接并读取表结构。");
  }

  async function persistForm() {
    if (!displayName.trim()) throw new Error("请填写数据源名称。");
    const schemaBeforePersist = schema;
    const foreignKeysCompleteBeforePersist = schemaForeignKeysComplete;
    let saved: DataSourceDetail;
    if (!detail) {
      saved = await createDataSource(payload);
    } else if (dirty) {
      saved = await updateDataSource(detail.data_source.id, payload);
    } else {
      saved = detail;
    }
    setSecretValues({});
    setSecretChanged(false);
    hydrateDetail(saved);
    if (schemaBeforePersist.length > 0) {
      setSchema(schemaBeforePersist);
      setSchemaForeignKeysComplete(foreignKeysCompleteBeforePersist);
    }
    setSources((current) => [
      saved,
      ...current.filter((item) => item.data_source.id !== saved.data_source.id),
    ]);
    emitCatalogUpdated();
    return saved;
  }

  async function withWorking(kind: string, action: () => Promise<void>) {
    setWorking(kind);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "操作失败，请稍后重试。");
    } finally {
      setWorking("");
    }
  }

  async function saveDraft() {
    await withWorking("save", async () => {
      const saved = await persistForm();
      setNotice(`“${saved.data_source.display_name}”的草稿已保存。`);
    });
  }

  async function refreshOperationStatus() {
    if (!operation) return;
    await withWorking("operation", async () => {
      const next = await fetchWrenOperation(operation.id);
      setOperation(next);
      if (next.status === "active") {
        const refreshed = await fetchDataSource(next.data_source_id);
        hydrateDetail(refreshed);
        await loadSources(next.data_source_id);
        setNotice("新版本已验证并生效。现有会话继续使用原绑定，新会话可选择此数据源。");
        emitCatalogUpdated();
      } else if (next.status === "failed") {
        setError(next.message || WREN_APPLY_FAILURE_FALLBACK);
      }
    });
  }

  async function testConnection() {
    await withWorking("test", async () => {
      const saved = await persistForm();
      const result = await testDataSourceConnection(saved.data_source.id);
      setNotice(result.ok ? "数据库连接成功。" : "数据库连接失败。");
    });
  }

  async function refreshSchema() {
    await withWorking("schema", async () => {
      const saved = await persistForm();
      const result = await refreshDataSourceSchema(saved.data_source.id);
      setSchema(result.tables);
      setSchemaForeignKeysComplete(result.foreign_keys_complete);
      const selectedBeforeRefresh = semantic.tables;
      const existingSelection = selectedBeforeRefresh.filter((name) =>
        result.tables.some((table) => (table.id || table.name) === name),
      );
      const selected = result.foreign_keys_complete
        ? includeDirectForeignKeyNeighbors(existingSelection, result.tables)
        : existingSelection;
      const models = createModelsForSelection(selected, result.tables, semantic.models);
      setSemantic({
        ...mergeForeignKeyRelationships(
          semantic,
          result.tables,
          selected,
          models,
          result.foreign_keys_complete,
        ),
        tables: selected,
        models,
      });
      const addedRelatedTables = selected.filter((table) => !existingSelection.includes(table)).length;
      const message = result.foreign_keys_complete
        ? `读取到 ${result.tables.length} 张表，已根据数据库外键同步模型关系${
            addedRelatedTables ? `，并自动选中 ${addedRelatedTables} 张直接关联表` : ""
          }。`
        : `读取到 ${result.tables.length} 张表；外键元数据未完整可用，已有关系已保留，可手动添加关系。`;
      setNotice(result.warnings?.length ? `${message} ${result.warnings.join(" ")}` : message);
    });
  }

  async function applyDraft() {
    await withWorking("apply", async () => {
      if (!semantic.tables.length) throw new Error("请至少选择一张数据表，再应用配置。");
      const saved = await persistForm();
      if (!saved.data_source.enabled) throw new Error("请先启用数据源，再应用配置。");
      let current = await applyDataSource(saved.data_source.id);
      setOperation(current);
      const deadline = Date.now() + 180_000;
      while (current.status === "running" && Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        current = await fetchWrenOperation(current.id);
        setOperation(current);
      }
      if (current.status === "running")
        throw new Error("Wren 仍在构建。可稍后刷新页面查看应用状态。");
      if (current.status === "failed") {
        throw new Error(current.message || WREN_APPLY_FAILURE_FALLBACK);
      }
      const refreshed = await fetchDataSource(saved.data_source.id);
      hydrateDetail(refreshed);
      await loadSources(saved.data_source.id);
      setNotice("新版本已验证并生效。现有会话继续使用原绑定，新会话可选择此数据源。 ");
      emitCatalogUpdated();
    });
  }

  async function changeDefault() {
    if (!selectedId) return;
    await withWorking("default", async () => {
      await setDefaultDataSource(selectedId);
      await loadSources(selectedId);
      setNotice(`“${displayName}”已设为默认数据源。`);
      emitCatalogUpdated();
    });
  }

  async function toggleEnabled() {
    if (!selectedId || !detail) return;
    await withWorking("enabled", async () => {
      const next = detail.data_source.enabled
        ? await deactivateDataSource(selectedId)
        : await enableDataSource(selectedId);
      hydrateDetail(next);
      await loadSources(selectedId);
      setNotice(
        next.data_source.enabled ? "数据源已启用。" : "数据源已停用；已存在的会话保留原绑定。 ",
      );
      emitCatalogUpdated();
    });
  }

  async function rollback(revisionId: string) {
    if (!selectedId) return;
    await withWorking("rollback", async () => {
      const next = await rollbackDataSource(selectedId, revisionId);
      hydrateDetail(next);
      await loadSources(selectedId);
      setNotice("已切回所选版本，当前版本已重新加载。 ");
      emitCatalogUpdated();
    });
  }

  async function previewRevision(revisionId: string) {
    if (!selectedId) return;
    const sourceId = selectedId;
    const requestId = ++revisionPreviewRequest.current;
    setSelectedRevisionId(revisionId);
    setRevisionPreview(null);
    setRevisionLoadingId(revisionId);
    setRevisionPreviewError("");
    setError("");
    setNotice("");
    try {
      const snapshot = await fetchDataSourceRevision(sourceId, revisionId);
      if (requestId !== revisionPreviewRequest.current) return;
      if (snapshot.data_source.id !== sourceId || snapshot.revision.id !== revisionId) {
        throw new Error("版本详情与所选数据源不匹配，请刷新后重试。");
      }
      setRevisionPreview(snapshot);
    } catch (cause) {
      if (requestId === revisionPreviewRequest.current) {
        setRevisionPreviewError(
          cause instanceof Error ? cause.message : "读取版本详情失败，请重试。",
        );
      }
    } finally {
      if (requestId === revisionPreviewRequest.current) setRevisionLoadingId(null);
    }
  }

  function returnToDraft() {
    revisionPreviewRequest.current += 1;
    setSelectedRevisionId(null);
    setRevisionPreview(null);
    setRevisionLoadingId(null);
    setRevisionPreviewError("");
  }

  function toggleTable(tableName: string) {
    setSemantic((current) => {
      const selected = current.tables.includes(tableName)
        ? current.tables.filter((name) => name !== tableName)
        : [...current.tables, tableName];
      const models = createModelsForSelection(selected, tableOptions, current.models);
      return {
        ...mergeForeignKeyRelationships(
          current,
          tableOptions,
          selected,
          models,
          schemaForeignKeysComplete,
        ),
        tables: selected,
        models,
      };
    });
  }

  function updateModel(index: number, update: Partial<WrenModel>) {
    setSemantic((current) => {
      const existing = current.models[index];
      const nextName = update.name;
      return {
        ...current,
        models: current.models.map((model, modelIndex) =>
          modelIndex === index ? { ...model, ...update } : model,
        ),
        ...(existing && nextName && nextName !== existing.name
          ? {
              relationships: current.relationships.map((relationship) => ({
                ...relationship,
                left_model:
                  relationship.left_model === existing.name ? nextName : relationship.left_model,
                right_model:
                  relationship.right_model === existing.name ? nextName : relationship.right_model,
                condition: relationship.condition.replaceAll(existing.name, nextName),
              })),
            }
          : {}),
      };
    });
  }

  function updateModelColumn(
    modelIndex: number,
    columnIndex: number,
    update: Partial<WrenModel["columns"][number]>,
  ) {
    setSemantic((current) => ({
      ...current,
      models: current.models.map((model, index) =>
        index === modelIndex
          ? {
              ...model,
              columns: model.columns.map((column, currentColumn) =>
                currentColumn === columnIndex ? { ...column, ...update } : column,
              ),
            }
          : model,
      ),
    }));
  }

  function addRelationship() {
    const [left, right] = semantic.models;
    if (!left || !right) return;
    setSemantic((current) => ({
      ...current,
      relationships: [
        ...current.relationships,
        {
          name: `${left.name}_${right.name}`,
          left_model: left.name,
          right_model: right.name,
          join_type: "many_to_one",
          condition: `${left.name}.id = ${right.name}.id`,
        },
      ],
    }));
  }

  function updateRelationship(index: number, update: Partial<WrenRelationship>) {
    setSemantic((current) => {
      const relationship = current.relationships[index];
      const foreignKeyId = relationship?.foreign_key_id;
      return {
        ...current,
        relationships: current.relationships.map((item, itemIndex) =>
          itemIndex === index
            ? {
                ...item,
                ...update,
                ...(typeof foreignKeyId === "string" ? { foreign_key_id: null } : {}),
              }
            : item,
        ),
        ignored_foreign_keys:
          typeof foreignKeyId === "string"
            ? [...new Set([...current.ignored_foreign_keys, foreignKeyId])]
            : current.ignored_foreign_keys,
      };
    });
  }

  function addRule() {
    setSemantic((current) => ({
      ...current,
      rules: [...current.rules, { name: "新规则", content: "" }],
    }));
  }

  function updateRule(index: number, update: Partial<WrenRule>) {
    setSemantic((current) => ({
      ...current,
      rules: current.rules.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...update } : item,
      ),
    }));
  }

  function addView() {
    setSemantic((current) => ({
      ...current,
      views: [...current.views, { name: "新视图", description: "", sql: "" }],
    }));
  }

  function updateView(index: number, update: Partial<WrenView>) {
    setSemantic((current) => ({
      ...current,
      views: current.views.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...update } : item,
      ),
    }));
  }

  const disabled = Boolean(working || operation?.status === "running");
  const applyBlockReason =
    actionBlockReason ||
    (!semantic.tables.length ? "请读取表结构并至少选择一张表后再应用配置。" : "");
  const revisionRows = detail?.revisions ?? [];
  const activeRevision = revisionRows.find(
    (revision) => revision.id === detail?.data_source.active_revision_id,
  );
  const otherRevisionRows = revisionRows.filter(
    (revision) => revision.id !== detail?.data_source.active_revision_id,
  );
  const previewConnector = connectors.find(
    (item) => item.type === revisionPreview?.data_source.connector_type,
  ) ?? null;
  const currentRevisionPreview =
    revisionPreview?.data_source.id === selectedId &&
    revisionPreview.revision.id === selectedRevisionId
      ? revisionPreview
      : null;
  return (
    <main className="min-h-dvh bg-[#f5f2eb] text-[#393630]">
      <SettingsPageHeader
        title="数据源"
        description="管理数据库/数仓连接、语义模型和生效版本"
        icon={DatabaseIcon}
        rightSlot={detail && (
            <span
              className={`hidden items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] sm:inline-flex ${detail.data_source.runtime_status === "ready" ? "bg-[#e7eee2] text-[#527249]" : "bg-[#f2e7dc] text-[#8c6149]"}`}
            >
              {detail.data_source.runtime_status === "ready" ? (
                <CheckCircle2Icon className="size-3.5" />
              ) : (
                <ActivityIcon className="size-3.5" />
              )}
              {detail.data_source.runtime_status === "ready"
                ? "运行中"
                  : detail.data_source.enabled
                    ? "未应用"
                    : "已停用"}
            </span>
        )}
      />

      <div className="mx-auto grid max-w-[1440px] gap-5 px-4 py-5 sm:px-8 lg:grid-cols-[260px_minmax(0,1fr)] lg:gap-7 lg:py-8">
        <aside className="h-fit rounded-2xl border border-[#e7e2d8] bg-[#f9f7f2] p-3 lg:sticky lg:top-24">
          <div className="flex items-center justify-between px-2 pb-2 pt-1">
            <div>
              <p className="text-[10px] font-semibold tracking-[0.13em] text-[#9b9589]">
                已配置数据源
              </p>
              <p className="mt-1 text-xs text-[#89847a]">{sources.length} 个连接</p>
            </div>
            <button
              type="button"
              onClick={startNewSource}
              className="flex size-8 items-center justify-center rounded-lg text-[#a66443] hover:bg-[#eee8dc] focus-visible:ring-2 focus-visible:ring-[#c57650]"
              aria-label="添加数据源"
            >
              <CirclePlusIcon className="size-4" />
            </button>
          </div>
          {loading ? (
            <div role="status" className="flex items-center gap-2 px-2 py-5 text-xs text-[#89847a]">
              <LoaderCircleIcon className="size-4 animate-spin" />
              正在读取…
            </div>
          ) : sources.length === 0 ? (
            <p className="rounded-xl border border-dashed border-[#ddd5c8] px-3 py-4 text-xs leading-5 text-[#89847a]">
              尚未配置数据源。添加连接后即可在聊天框中选择。
            </p>
          ) : (
            <nav
              aria-label="数据源列表"
              className="flex gap-2 overflow-x-auto lg:flex-col lg:overflow-visible"
            >
              {sources.map((source) => {
                const selected = source.data_source.id === selectedId;
                const hasActiveRevision = Boolean(source.data_source.active_revision_id);
                return (
                  <button
                    key={source.data_source.id}
                    type="button"
                    onClick={() =>
                      void withWorking("load", async () =>
                        hydrateDetail(await fetchDataSource(source.data_source.id)),
                      )
                    }
                    className={`min-w-44 flex-1 rounded-xl border-l-2 px-3 py-3 text-left transition focus-visible:ring-2 focus-visible:ring-[#c57650] lg:min-w-0 ${
                      hasActiveRevision ? "border-l-[#6a8a5b]" : "border-l-transparent"
                    } ${
                      selected
                        ? "bg-[#ebe5d9]"
                        : hasActiveRevision
                          ? "bg-[#eef2e9] hover:bg-[#e7eee2]"
                          : "hover:bg-[#f0ede6]"
                    }`}
                  >
                    <span className="flex items-center gap-2 text-xs font-medium text-[#4a463f]">
                      <ServerIcon className="size-3.5 shrink-0 text-[#a66a4c]" />
                      <span className="min-w-0 flex-1 truncate">
                        {source.data_source.display_name}
                      </span>
                      {source.data_source.id === defaultSourceId && (
                        <span className="rounded-full bg-[#eee8dc] px-1.5 py-0.5 text-[9px] text-[#8c6149]">
                          默认
                        </span>
                      )}
                      {hasActiveRevision && (
                        <span className="shrink-0 rounded-full bg-[#e0ead9] px-1.5 py-0.5 text-[10px] font-semibold text-[#527249]">
                          已生效
                        </span>
                      )}
                    </span>
                    <span className="mt-1.5 block truncate pl-[22px] text-[10px] text-[#89847a]">
                      {connectors.find((item) => item.type === source.data_source.connector_type)?.label ??
                        source.data_source.connector_type}
                      {source.connection.host || source.connection.database
                        ? ` · ${String(source.connection.host ?? source.connection.database ?? "")}`
                        : " · 尚未填写连接信息"}
                    </span>
                  </button>
                );
              })}
            </nav>
          )}
          {detail && (
            <section aria-label={`${detail.data_source.display_name} 版本历史`} className="mt-3 border-t border-[#e7e2d8] pt-3">
              <div className="flex items-center justify-between px-2 pb-2">
                <p className="text-[10px] font-semibold tracking-[0.13em] text-[#9b9589]">版本历史</p>
                <span className="text-[10px] text-[#989286]">{revisionRows.length}</span>
              </div>
              {activeRevision && (
                <div className="mb-1.5">
                  <RevisionNavigationItem
                    revision={activeRevision}
                    isActive
                    selected={selectedRevisionId === activeRevision.id}
                    loading={Boolean(working) || revisionLoadingId === activeRevision.id}
                    onSelect={() => void previewRevision(activeRevision.id)}
                  />
                </div>
              )}
              {otherRevisionRows.length ? (
                <nav
                  aria-label="版本列表"
                  className="ml-2 max-h-[50vh] space-y-1 overflow-y-auto border-l border-[#dfd8cc] pl-2 pr-1"
                >
                  {otherRevisionRows.map((revision) => (
                    <RevisionNavigationItem
                      key={revision.id}
                      revision={revision}
                      isActive={revision.id === detail.data_source.active_revision_id}
                      selected={selectedRevisionId === revision.id}
                      loading={Boolean(working) || revisionLoadingId === revision.id}
                      onSelect={() => void previewRevision(revision.id)}
                    />
                  ))}
                </nav>
              ) : !activeRevision ? (
                <p className="rounded-lg px-2.5 py-2 text-[10px] leading-4 text-[#989286]">
                  保存配置后会在这里显示草稿和版本。
                </p>
              ) : null}
            </section>
          )}
          {catalogStatus === "failed" && (
            <p
              role="status"
              className="mt-3 rounded-lg bg-[#f8e9e4] px-3 py-2 text-[11px] leading-5 text-[#9c4037]"
            >
              配置导入未完成，可手动新增数据源。
            </p>
          )}
        </aside>

        <div className="min-w-0 space-y-5">
          {selectedRevisionId ? (
            <>
              {error && (
                <div role="alert" className="rounded-xl border border-[#efd5cd] bg-[#fff8f5] px-3.5 py-3 text-xs leading-5 text-[#9c4037]">
                  {error}
                </div>
              )}
              {revisionLoadingId === selectedRevisionId ? (
                <div role="status" className="flex min-h-44 items-center justify-center gap-2 rounded-2xl border border-[#e7e2d8] bg-[#fbfaf7] text-xs text-[#89847a]">
                  <LoaderCircleIcon className="size-4 animate-spin" />
                  正在读取版本配置…
                </div>
              ) : currentRevisionPreview ? (
                <RevisionPreview
                  snapshot={currentRevisionPreview}
                  activeRevisionId={detail?.data_source.active_revision_id ?? null}
                  connector={previewConnector}
                  disabled={disabled}
                  draftDirty={dirty}
                  onReturnToDraft={returnToDraft}
                  onRequestActivation={() => setRevisionToActivate(currentRevisionPreview.revision)}
                />
              ) : (
                <div className="rounded-2xl border border-[#e7e2d8] bg-[#fbfaf7] p-5">
                  <p role="alert" className="text-xs text-[#9c4037]">
                    {revisionPreviewError || "无法读取此版本配置。"}
                  </p>
                  <ActionButton onClick={returnToDraft}>
                    返回草稿
                  </ActionButton>
                </div>
              )}
            </>
          ) : (
            <>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="min-w-0">
              <p className="text-[10px] font-semibold tracking-[0.13em] text-[#9b9589]">
                {detail ? "数据源配置" : "新建连接"}
              </p>
              <input
                aria-label="数据源名称"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="数据源名称"
                className="mt-1 w-full max-w-xl border-0 bg-transparent p-0 text-2xl font-semibold tracking-tight text-[#393630] outline-none placeholder:text-[#c0baae] focus:ring-0"
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <ActionButton
                onClick={() => void saveDraft()}
                disabled={disabled || !displayName.trim() || !connectionReady}
              >
                <SaveIcon className="size-3.5" />
                保存草稿
              </ActionButton>
              <ActionButton
                onClick={() => void applyDraft()}
                disabled={
                  disabled || !displayName.trim() || !connectionReady || !semantic.tables.length
                }
                variant="primary"
              >
                <WandSparklesIcon className="size-3.5" />
                应用配置
              </ActionButton>
            </div>
          </div>

          {(error || notice) && (
            <div
              role={error ? "alert" : "status"}
              className={`flex items-start gap-2 rounded-xl px-3.5 py-3 text-xs leading-5 ${error ? "border border-[#efd5cd] bg-[#fff8f5] text-[#9c4037]" : "border border-[#dce7d7] bg-[#f4f8f0] text-[#54734d]"}`}
            >
              {error ? (
                <AlertCircleIcon className="mt-0.5 size-4 shrink-0" />
              ) : (
                <CheckCircle2Icon className="mt-0.5 size-4 shrink-0" />
              )}
              <span>{error || notice}</span>
            </div>
          )}

          {operation && (
            <div className="rounded-xl border border-[#e7e2d8] bg-white/70 px-4 py-3">
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="flex items-center gap-2 font-medium text-[#514b42]">
                  {operation.status === "running" ? (
                    <LoaderCircleIcon className="size-4 animate-spin text-[#a66443]" />
                  ) : operation.status === "active" ? (
                    <CheckCircle2Icon className="size-4 text-[#638252]" />
                  ) : (
                    <AlertCircleIcon className="size-4 text-[#9c4037]" />
                  )}
                  {phaseLabels[operation.phase] ?? operation.phase}
                </span>
                <span className="text-[#89847a]">
                  {operation.status === "running"
                    ? "正在处理"
                    : operation.status === "active"
                      ? "成功"
                      : "失败"}
                </span>
                {operation.status === "running" && (
                  <ActionButton
                    onClick={() => void refreshOperationStatus()}
                    disabled={Boolean(working)}
                  >
                    <RefreshCwIcon className="size-3.5" />
                    刷新状态
                  </ActionButton>
                )}
              </div>
              {operation.status === "running" && (
                <div className="mt-3 h-1 overflow-hidden rounded bg-[#eee8dc]">
                  <div className="h-full w-2/5 animate-pulse rounded bg-[#c57650]" />
                </div>
              )}
              {operation.status === "failed" &&
                error !== (operation.message || WREN_APPLY_FAILURE_FALLBACK) && (
                  <p role="alert" className="mt-3 text-xs leading-5 text-[#9c4037]">
                    {operation.message || WREN_APPLY_FAILURE_FALLBACK}
                  </p>
                )}
            </div>
          )}

          {detail &&
            detail.data_source.active_revision_id &&
            detail.data_source.draft_revision_id && (
              <Section
                title="待生效变更预览"
                description="下方表单编辑的是草稿版本。应用成功后才会替换此数据源的活动 runtime。"
              >
                {(() => {
                  const comparison = revisionDiff(detail.active_config, detail.config);
                  const hasChanges =
                    comparison.connectionChanged ||
                    comparison.diff.some((item) => item.added + item.removed + item.changed > 0);
                  return (
                    <div className="space-y-3">
                      <div className="flex flex-wrap items-center gap-2 text-[10px]">
                        <span className="rounded-full bg-[#e7eee2] px-2.5 py-1 text-[#54734d]">
                          活动 {detail.data_source.active_revision_id}
                        </span>
                        <span className="text-[#aaa397]">→</span>
                        <span className="rounded-full bg-[#f2e7dc] px-2.5 py-1 text-[#8c6149]">
                          草稿 {detail.data_source.draft_revision_id}
                        </span>
                      </div>
                      {!hasChanges ? (
                        <p className="text-xs text-[#89847a]">草稿内容与当前活动版本一致。</p>
                      ) : (
                        <div className="flex flex-wrap gap-2">
                          {comparison.connectionChanged && (
                            <span className="rounded-lg border border-[#ebe6dd] bg-white px-2.5 py-1.5 text-[10px] text-[#615b51]">
                              连接配置已修改
                            </span>
                          )}
                          {comparison.diff.map(
                            (item) =>
                              item.added + item.removed + item.changed > 0 && (
                                <span
                                  key={item.name}
                                  className="rounded-lg border border-[#ebe6dd] bg-white px-2.5 py-1.5 text-[10px] text-[#615b51]"
                                >
                                  <span className="mr-1 font-medium">{item.name}</span>
                                  {item.added > 0 && (
                                    <span className="text-[#54734d]">+{item.added}</span>
                                  )}
                                  {item.removed > 0 && (
                                    <span className="ml-1 text-[#9c4037]">−{item.removed}</span>
                                  )}
                                  {item.changed > 0 && (
                                    <span className="ml-1 text-[#9c6046]">
                                      {item.changed} 项修改
                                    </span>
                                  )}
                                </span>
                              ),
                          )}
                        </div>
                      )}
                    </div>
                  );
                })()}
              </Section>
            )}

          <Section
            title={`${connectorDefinition?.label ?? "数据库/数仓"} 连接`}
          >
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {!detail && (
                <label className="grid gap-1.5 text-xs font-medium text-[#615b51]">
                  数据库类型
                  <select
                    aria-label="数据库类型"
                    value={connectorType}
                    onChange={(event) => chooseConnector(event.target.value)}
                    className="h-10 rounded-lg border border-[#e7e2d8] bg-white px-3 text-sm font-normal text-[#393630] outline-none focus:border-[#d8cbb9] focus:ring-2 focus:ring-[#c57650]/20"
                  >
                    {connectors.map((item) => (
                      <option key={item.type} value={item.type}>{item.label}</option>
                    ))}
                  </select>
                </label>
              )}
              {connectorVariants.length > 1 && (
                <label className="grid gap-1.5 text-xs font-medium text-[#615b51]">
                  认证方式
                  <select
                    value={activeFieldGroup?.variant ?? connectorVariants[0]}
                    onChange={(event) => {
                      const discriminator = connectorDefinition?.field_groups
                        .flatMap((group) => group.fields)
                        .find((field) => field.name.endsWith("_type"));
                      const nextGroup = connectorDefinition?.field_groups.find(
                        (group) => group.variant === event.target.value,
                      );
                      if (discriminator && nextGroup) {
                        const nextFields = new Set(nextGroup.fields.map((field) => field.name));
                        const nextSecrets = new Set(
                          nextGroup.fields.filter((field) => field.sensitive).map((field) => field.name),
                        );
                        setConnection((current) => {
                          const next = Object.fromEntries(
                            Object.entries(current).filter(([key]) => nextFields.has(key)),
                          ) as DataSourceConnection;
                          for (const field of nextGroup.fields) {
                            if (next[field.name] === undefined && field.default !== null)
                              next[field.name] = field.default;
                          }
                          next[discriminator.name] = event.target.value;
                          return next;
                        });
                        setSecretValues((current) =>
                          Object.fromEntries(
                            Object.entries(current).filter(([key]) => nextSecrets.has(key)),
                          ),
                        );
                        setConfiguredSecretFields((current) =>
                          current.filter((key) => nextSecrets.has(key)),
                        );
                        setSecretChanged(true);
                        setNotice("认证方式已修改，请重新填写凭证并测试连接。");
                      }
                    }}
                    className="h-10 rounded-lg border border-[#e7e2d8] bg-white px-3 text-sm font-normal text-[#393630] outline-none focus:border-[#d8cbb9] focus:ring-2 focus:ring-[#c57650]/20"
                  >
                    {connectorVariants.map((variant) => (
                      <option key={variant} value={variant}>
                        {variant === "dataset" ? "按数据集" :
                          variant === "project" ? "按项目" :
                            variant === "redshift_iam" ? "IAM 临时凭证" :
                              variant === "service_principal" ? "服务主体" :
                                variant === "token" ? "访问令牌" : variant}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {(activeFieldGroup?.fields ?? [])
                .filter((field) => field.input_type !== "hidden")
                .map((field) => {
                  const storedSecret = configuredSecretFields.includes(field.name);
                  const label = `${field.label}${storedSecret ? "（留空保持不变）" : ""}`;
                  if (field.input_type === "file_base64") {
                    return (
                      <label key={field.name} className="grid gap-1.5 text-xs font-medium text-[#615b51]">
                        {label}
                        <input
                          type="file"
                          accept={field.accept ?? undefined}
                          required={field.required && !storedSecret}
                          onChange={(event) => {
                            const file = event.target.files?.[0];
                            if (!file) return;
                            const reader = new FileReader();
                            reader.onload = () => {
                              const encoded = String(reader.result ?? "").split(",")[1] ?? "";
                              setSecretField(field.name, encoded);
                            };
                            reader.readAsDataURL(file);
                          }}
                          className="min-h-10 w-full rounded-lg border border-[#e7e2d8] bg-white px-3 py-2 text-xs font-normal text-[#514b42]"
                        />
                        {field.hint && <span className="text-[10px] leading-4 text-[#89847a]">{field.hint}</span>}
                      </label>
                    );
                  }
                  if (field.sensitive || field.input_type === "password") {
                    return (
                      <Field
                        key={field.name}
                        label={label}
                        type="password"
                        value={secretValues[field.name] ?? ""}
                        placeholder={storedSecret ? "已配置，输入新值以更新" : field.placeholder}
                        required={field.required && !storedSecret}
                        autoComplete="new-password"
                        onChange={(value) => setSecretField(field.name, value)}
                      />
                    );
                  }
                  return (
                    <Field
                      key={field.name}
                      label={field.label}
                      type={field.name.toLowerCase().includes("port") ? "number" : "text"}
                      value={String(connection[field.name] ?? field.default ?? "")}
                      placeholder={field.placeholder}
                      required={field.required}
                      onChange={(value) => setConnectionField(field.name, value)}
                    />
                  );
                })}
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <ActionButton
                onClick={() => void testConnection()}
                disabled={disabled || !displayName.trim() || !connectionReady}
              >
                <ShieldCheckIcon className="size-3.5" />
                测试连接
              </ActionButton>
              <ActionButton
                onClick={() => void refreshSchema()}
                disabled={disabled || !displayName.trim() || !connectionReady}
              >
                <RefreshCwIcon className="size-3.5" />
                读取表结构
              </ActionButton>
              {detail?.data_source.active_revision_id && (
                <ActionButton
                  onClick={() => void toggleEnabled()}
                  disabled={disabled}
                  variant={detail.data_source.enabled ? "danger" : "outline"}
                >
                  {detail.data_source.enabled ? (
                    <Trash2Icon className="size-3.5" />
                  ) : (
                    <CheckCircle2Icon className="size-3.5" />
                  )}
                  {detail.data_source.enabled ? "停用数据源" : "重新启用"}
                </ActionButton>
              )}
              {detail &&
                detail.data_source.runtime_status === "ready" &&
                defaultSourceId !== selectedId && (
                  <ActionButton
                    onClick={() => void changeDefault()}
                    disabled={disabled}
                    variant="quiet"
                  >
                    设为默认
                  </ActionButton>
                )}
              {detail?.data_source.active_revision_id &&
                sources.find((source) => source.data_source.id === selectedId)?.data_source.id ===
                  selectedId && (
                  <span className="ml-auto flex items-center gap-1 text-[10px] text-[#638252]">
                    <CheckCircle2Icon className="size-3" />
                    已有已生效版本
                  </span>
                )}
            </div>
            {actionBlockReason && (
              <p className="mt-2 text-[11px] text-[#89847a]" role="status">
                {actionBlockReason}
              </p>
            )}
          </Section>

          <Section
            title="选择数据库表"
            description="先读取数据库或数仓的元数据，再勾选要交给 Wren 查询的表。这里只读取表和字段定义，不扫描业务数据。"
          >
            {tableOptions.length === 0 ? (
              <div className="rounded-xl border border-dashed border-[#ddd5c8] px-4 py-5 text-center">
                <p className="text-xs text-[#89847a]">尚未读取表结构</p>
                <button
                  type="button"
                  onClick={() => void refreshSchema()}
                  disabled={disabled || !displayName.trim() || !connectionReady}
                  className="mt-2 text-xs font-medium text-[#a66443] underline-offset-2 hover:underline disabled:opacity-50"
                >
                  读取表结构
                </button>
              </div>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {tableOptions.map((table) => (
                  <label
                    key={table.id || table.name}
                    className={`flex min-w-0 cursor-pointer items-start gap-2.5 rounded-xl border px-3 py-2.5 transition ${selectedTables.includes(table.id || table.name) ? "border-[#d8c5af] bg-[#f5f0e7]" : "border-[#ebe6dd] bg-white/70 hover:bg-white"}`}
                  >
                    <input
                      type="checkbox"
                      checked={selectedTables.includes(table.id || table.name)}
                      onChange={() => toggleTable(table.id || table.name)}
                      className="mt-0.5 accent-[#c57650]"
                    />
                    <span className="min-w-0">
                      <span className="block truncate text-xs font-medium text-[#514b42]">
                        {table.schema ? `${table.schema}.${table.name}` : table.name}
                      </span>
                      <span className="mt-1 block text-[10px] text-[#989186]">
                        {table.columns.length} 个字段
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            )}
          </Section>

          <Section
            title="语义模型"
            description="为表设置对用户友好的名称、说明和字段可见性；字段说明可帮助自然语言问题准确映射到数据。"
          >
            {semantic.models.length === 0 ? (
              <p className="text-xs text-[#89847a]">请先选择至少一张表。</p>
            ) : (
              <div className="space-y-3">
                {semantic.models.map((model, modelIndex) => (
                  <details
                    key={model.table}
                    open
                    className="rounded-xl border border-[#ebe6dd] bg-white/65"
                  >
                    <summary className="flex cursor-pointer items-center gap-2 px-3 py-3 text-xs font-medium text-[#514b42]">
                      <DatabaseIcon className="size-3.5 text-[#a66a4c]" />
                      <span>{model.name || model.table}</span>
                      <span className="font-normal text-[#989186]">
                        {model.table} · {model.columns.length} 个字段
                      </span>
                    </summary>
                    <div className="space-y-3 border-t border-[#eee9df] px-3 py-3">
                      <div className="grid gap-3 sm:grid-cols-2">
                        <Field
                          label="模型名称"
                          value={model.name}
                          onChange={(value) => updateModel(modelIndex, { name: value })}
                        />
                        <Field
                          label="模型说明"
                          value={model.description}
                          placeholder="描述这张表代表的业务对象"
                          onChange={(value) => updateModel(modelIndex, { description: value })}
                        />
                      </div>
                      <div className="overflow-x-auto rounded-lg border border-[#eee9df]">
                        <div className="grid min-w-[560px] grid-cols-[minmax(100px,0.8fr)_minmax(180px,1.4fr)_70px_70px] gap-2 bg-[#f5f2eb] px-3 py-2 text-[10px] font-medium text-[#89847a]">
                          <span>字段</span>
                          <span>字段说明</span>
                          <span>隐藏</span>
                          <span>主键</span>
                        </div>
                        {model.columns.map((column, columnIndex) => (
                          <div
                            key={column.name}
                            className="grid min-w-[560px] grid-cols-[minmax(100px,0.8fr)_minmax(180px,1.4fr)_70px_70px] items-center gap-2 border-t border-[#f0ece4] px-3 py-2"
                          >
                            <span
                              className="truncate font-mono text-[10px] text-[#615b51]"
                              title={column.name}
                            >
                              {column.name}
                            </span>
                            <input
                              aria-label={`${column.name} 字段说明`}
                              value={column.description}
                              onChange={(event) =>
                                updateModelColumn(modelIndex, columnIndex, {
                                  description: event.target.value,
                                })
                              }
                              className="h-8 rounded-md border border-[#e7e2d8] bg-white px-2 text-[11px] outline-none focus:border-[#d8cbb9]"
                            />
                            <input
                              aria-label={`隐藏 ${column.name}`}
                              type="checkbox"
                              checked={column.hidden}
                              onChange={(event) =>
                                updateModelColumn(modelIndex, columnIndex, {
                                  hidden: event.target.checked,
                                })
                              }
                              className="size-3.5 accent-[#c57650]"
                            />
                            <input
                              aria-label={`${column.name} 为主键`}
                              type="checkbox"
                              checked={column.primary_key}
                              onChange={(event) =>
                                updateModelColumn(modelIndex, columnIndex, {
                                  primary_key: event.target.checked,
                                })
                              }
                              className="size-3.5 accent-[#c57650]"
                            />
                          </div>
                        ))}
                      </div>
                    </div>
                  </details>
                ))}
              </div>
            )}
          </Section>

          <Section
            title="模型关系"
            description="读取表结构时会保留已选表，并自动补齐与其直接关联的外键表、生成数据库关系；可取消不需要的表，也可手动添加数据库未声明的关系。"
          >
            <div className="space-y-3">
              {semantic.relationships.length === 0 && (
                <p className="text-xs text-[#89847a]">
                  读取表结构并选中关联数据表后，可用的数据库外键会自动出现在这里；未声明或不可读取的关系可手动添加。
                </p>
              )}
              {semantic.relationships.map((relationship, index) => (
                <div
                  key={`${relationship.name}-${index}`}
                  className="rounded-xl border border-[#ebe6dd] bg-white/65 p-3"
                >
                  {typeof relationship.foreign_key_id === "string" && (
                    <p className="mb-2 text-[10px] text-[#89847a]">数据库外键</p>
                  )}
                  <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-[1fr_1fr_1fr]">
                    <label className="grid gap-1 text-[10px] text-[#89847a]">
                      左侧模型
                      <select
                        value={relationship.left_model}
                        onChange={(event) =>
                          updateRelationship(index, { left_model: event.target.value })
                        }
                        className="h-9 rounded-lg border border-[#e7e2d8] bg-white px-2 text-xs text-[#514b42]"
                      >
                        {modelNames.map((name) => (
                          <option key={name}>{name}</option>
                        ))}
                      </select>
                    </label>
                    <label className="grid gap-1 text-[10px] text-[#89847a]">
                      关系类型
                      <select
                        value={relationship.join_type}
                        onChange={(event) =>
                          updateRelationship(index, {
                            join_type: event.target.value as WrenRelationship["join_type"],
                          })
                        }
                        className="h-9 rounded-lg border border-[#e7e2d8] bg-white px-2 text-xs text-[#514b42]"
                      >
                        <option value="many_to_one">多对一</option>
                        <option value="one_to_many">一对多</option>
                        <option value="one_to_one">一对一</option>
                        <option value="many_to_many">多对多</option>
                      </select>
                    </label>
                    <label className="grid gap-1 text-[10px] text-[#89847a]">
                      右侧模型
                      <select
                        value={relationship.right_model}
                        onChange={(event) =>
                          updateRelationship(index, { right_model: event.target.value })
                        }
                        className="h-9 rounded-lg border border-[#e7e2d8] bg-white px-2 text-xs text-[#514b42]"
                      >
                        {modelNames.map((name) => (
                          <option key={name}>{name}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <div className="mt-2 flex gap-2">
                    <input
                      aria-label="关系条件"
                      value={relationship.condition}
                      onChange={(event) =>
                        updateRelationship(index, { condition: event.target.value })
                      }
                      placeholder="orders.customer_id = customers.id"
                      className="h-9 min-w-0 flex-1 rounded-lg border border-[#e7e2d8] bg-white px-2.5 font-mono text-[11px] outline-none focus:border-[#d8cbb9]"
                    />
                    <button
                      type="button"
                      aria-label="删除关系"
                      onClick={() =>
                        setSemantic((current) => {
                          const foreignKeyId = current.relationships[index]?.foreign_key_id;
                          return {
                            ...current,
                            relationships: current.relationships.filter(
                              (_, itemIndex) => itemIndex !== index,
                            ),
                            ignored_foreign_keys:
                              typeof foreignKeyId === "string"
                                ? [...new Set([...current.ignored_foreign_keys, foreignKeyId])]
                                : current.ignored_foreign_keys,
                          };
                        })
                      }
                      className="rounded-lg px-2 text-[#9c6046] hover:bg-[#f8e9e4]"
                    >
                      <Trash2Icon className="size-3.5" />
                    </button>
                  </div>
                </div>
              ))}
              <ActionButton onClick={addRelationship} disabled={semantic.models.length < 2}>
                <CirclePlusIcon className="size-3.5" />
                添加关系
              </ActionButton>
            </div>
          </Section>

          <Section
            title="业务规则"
            description="补充业务口径、指标定义或查询注意事项，作为 Wren 的语义上下文。"
          >
            <div className="space-y-3">
              {semantic.rules.map((rule, index) => (
                <div
                  key={`rule-${index}`}
                  className="rounded-xl border border-[#ebe6dd] bg-white/65 p-3"
                >
                  <div className="flex gap-2">
                    <input
                      aria-label="规则名称"
                      value={rule.name}
                      onChange={(event) => updateRule(index, { name: event.target.value })}
                      className="h-9 min-w-0 flex-1 rounded-lg border border-[#e7e2d8] bg-white px-2.5 text-xs outline-none focus:border-[#d8cbb9]"
                    />
                    <button
                      type="button"
                      aria-label="删除规则"
                      onClick={() =>
                        setSemantic((current) => ({
                          ...current,
                          rules: current.rules.filter((_, itemIndex) => itemIndex !== index),
                        }))
                      }
                      className="rounded-lg px-2 text-[#9c6046] hover:bg-[#f8e9e4]"
                    >
                      <Trash2Icon className="size-3.5" />
                    </button>
                  </div>
                  <textarea
                    aria-label="规则内容"
                    value={rule.content}
                    onChange={(event) => updateRule(index, { content: event.target.value })}
                    rows={3}
                    placeholder="例如：净收入不包含已取消订单。"
                    className="mt-2 w-full rounded-lg border border-[#e7e2d8] bg-white p-2.5 text-xs leading-5 outline-none focus:border-[#d8cbb9]"
                  />
                </div>
              ))}
              <ActionButton onClick={addRule}>
                <CirclePlusIcon className="size-3.5" />
                添加规则
              </ActionButton>
            </div>
          </Section>

          <Section
            title="自定义视图"
            description="可添加 SQL 视图，并为视图写明业务含义。SQL 需符合当前数据源的方言。"
          >
            <div className="space-y-3">
              {semantic.views.map((view, index) => (
                <div
                  key={`view-${index}`}
                  className="rounded-xl border border-[#ebe6dd] bg-white/65 p-3"
                >
                  <div className="grid gap-2 sm:grid-cols-2">
                    <Field
                      label="视图名称"
                      value={view.name}
                      onChange={(value) => updateView(index, { name: value })}
                    />
                    <Field
                      label="视图说明"
                      value={view.description}
                      onChange={(value) => updateView(index, { description: value })}
                    />
                  </div>
                  <div className="mt-2 flex gap-2">
                    <textarea
                      aria-label="视图 SQL"
                      value={view.sql}
                      onChange={(event) => updateView(index, { sql: event.target.value })}
                      rows={4}
                      placeholder="SELECT ..."
                      className="min-w-0 flex-1 rounded-lg border border-[#e7e2d8] bg-white p-2.5 font-mono text-[11px] leading-5 outline-none focus:border-[#d8cbb9]"
                    />
                    <button
                      type="button"
                      aria-label="删除视图"
                      onClick={() =>
                        setSemantic((current) => ({
                          ...current,
                          views: current.views.filter((_, itemIndex) => itemIndex !== index),
                        }))
                      }
                      className="rounded-lg px-2 text-[#9c6046] hover:bg-[#f8e9e4]"
                    >
                      <Trash2Icon className="size-3.5" />
                    </button>
                  </div>
                </div>
              ))}
              <ActionButton onClick={addView}>
                <CirclePlusIcon className="size-3.5" />
                添加视图
              </ActionButton>
            </div>
          </Section>

          <div className="sticky bottom-3 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-[#e7e2d8] bg-[#f8f6f1]/95 p-3 shadow-[0_8px_28px_rgba(59,48,35,0.10)] backdrop-blur">
            <p className="hidden text-[11px] text-[#89847a] sm:block">
              {dirty
                ? "有未保存的修改"
                : detail?.data_source.active_revision_id
                  ? "草稿与当前生效版本分开保存"
                  : "保存并应用后即可开始查询"}
            </p>
            <div className="ml-auto flex gap-2">
              <ActionButton
                onClick={() => void saveDraft()}
                disabled={disabled || !displayName.trim() || !connectionReady}
              >
                <SaveIcon className="size-3.5" />
                保存草稿
              </ActionButton>
              <ActionButton
                onClick={() => void applyDraft()}
                disabled={
                  disabled || !displayName.trim() || !connectionReady || !semantic.tables.length
                }
                variant="primary"
              >
                <WandSparklesIcon className="size-3.5" />
                应用配置
              </ActionButton>
            </div>
            {applyBlockReason && (
              <p className="basis-full text-right text-[11px] text-[#89847a]" role="status">
                {applyBlockReason}
              </p>
            )}
          </div>
            </>
          )}
        </div>
      </div>
      <ConfirmDialog
        open={Boolean(revisionToActivate)}
        title="切换到此版本？"
        description={
          revisionToActivate
            ? `将“${detail?.data_source.display_name ?? "此数据源"}”切换到 ${revisionToActivate.id}。成功后该版本生效，草稿仍会保留。`
            : ""
        }
        confirmLabel="切换并生效"
        onConfirm={() => {
          const target = revisionToActivate;
          setRevisionToActivate(null);
          if (target) void rollback(target.id);
        }}
        onCancel={() => setRevisionToActivate(null)}
      />
    </main>
  );
}
