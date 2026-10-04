import type {
  DataSourceConnection,
  DataSourceDetail,
  DataSourceForeignKey,
  DataSourceTable,
  SourceFormPayload,
  WrenModel,
  WrenRelationship,
  WrenSemanticConfig,
  WrenSourceConfig,
} from "@/lib/data-sources";

const semanticConfigKeys = new Set([
  "tables",
  "models",
  "relationships",
  "ignored_foreign_keys",
  "rules",
  "views",
]);

export function isSemanticConfigKey(key: string): boolean {
  return semanticConfigKeys.has(key);
}

export function defaultConnection(connectorType: string): DataSourceConnection {
  return connectorType === "mysql" ? { port: "3306" } : {};
}

export function detailSemantic(detail: DataSourceDetail): WrenSemanticConfig {
  return {
    tables: detail.config.tables ?? [],
    models: detail.config.models ?? [],
    relationships: detail.config.relationships ?? [],
    ignored_foreign_keys: detail.config.ignored_foreign_keys ?? [],
    rules: detail.config.rules ?? [],
    views: detail.config.views ?? [],
  };
}

export function baseFingerprint(payload: SourceFormPayload, sensitiveFields: string[] = []) {
  const connection = Object.fromEntries(
    Object.entries(payload.connection).filter(
      ([key, value]) => value !== undefined && !sensitiveFields.includes(key),
    ),
  );
  return JSON.stringify({ ...payload, connection });
}

export function sourceTables(semantic: WrenSemanticConfig): DataSourceTable[] {
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

export function revisionDiff(active: WrenSourceConfig = {}, draft: WrenSourceConfig = {}) {
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

export function createModelsForSelection(
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
      (model) => model.table.endsWith(`.${tableId}`) || model.table.split(".").pop() === tableId,
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

export function includeDirectForeignKeyNeighbors(
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

export function mergeForeignKeyRelationships(
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
