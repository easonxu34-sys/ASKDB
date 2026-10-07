export type MemoryRecallKind = "schema" | "business_rule" | "query_example";

export type MemoryRecallCounts = Partial<Record<MemoryRecallKind, number>>;

export type MemoryRecallDetailItem = {
  kind: MemoryRecallKind;
  title: string;
  titleTruncated?: boolean;
  detail?: string;
  detailTruncated?: boolean;
};

export type MemoryRecallPayload = {
  counts: MemoryRecallCounts;
  items: MemoryRecallDetailItem[];
};

const MEMORY_RECALL_KINDS: readonly MemoryRecallKind[] = [
  "query_example",
  "business_rule",
  "schema",
];

const MEMORY_RECALL_LABELS: Record<MemoryRecallKind, string> = {
  query_example: "示例",
  business_rule: "口径",
  schema: "结构",
};

const MAX_TITLE_LENGTH = 160;
const MAX_DETAIL_LENGTH = 600;
const MAX_DETAIL_ITEMS = 13;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isMemoryRecallKind(value: unknown): value is MemoryRecallKind {
  return typeof value === "string" && MEMORY_RECALL_KINDS.includes(value as MemoryRecallKind);
}

function readBoundedText(
  value: unknown,
  limit: number,
): { text: string; truncated: boolean } | undefined {
  if (typeof value !== "string") return undefined;
  const text = value.trim();
  if (!text) return undefined;

  const characters = Array.from(text);
  return characters.length > limit
    ? {
        text: `${characters.slice(0, limit - 1).join("")}…`,
        truncated: true,
      }
    : { text, truncated: false };
}

export function readMemoryRecallCounts(value: unknown): MemoryRecallCounts | undefined {
  if (!isRecord(value)) return undefined;

  const counts: MemoryRecallCounts = {};
  for (const kind of MEMORY_RECALL_KINDS) {
    const count = value[kind];
    if (
      typeof count === "number" &&
      Number.isSafeInteger(count) &&
      count > 0 &&
      count <= MAX_DETAIL_ITEMS
    ) {
      counts[kind] = count;
    }
  }

  return Object.keys(counts).length > 0 ? counts : undefined;
}

export function readMemoryRecallPayload(value: unknown): MemoryRecallPayload | undefined {
  if (!isRecord(value)) return undefined;
  const counts = readMemoryRecallCounts(value.counts);
  if (!counts) return undefined;

  const items: MemoryRecallDetailItem[] = [];
  if (Array.isArray(value.items)) {
    for (const candidate of value.items.slice(0, MAX_DETAIL_ITEMS)) {
      if (!isRecord(candidate) || !isMemoryRecallKind(candidate.kind)) continue;
      const title = readBoundedText(candidate.title, MAX_TITLE_LENGTH);
      if (!title) continue;

      const item: MemoryRecallDetailItem = {
        kind: candidate.kind,
        title: title.text,
        ...(title.truncated || candidate.title_truncated === true ? { titleTruncated: true } : {}),
      };
      const detail =
        candidate.kind === "query_example"
          ? undefined
          : readBoundedText(candidate.detail, MAX_DETAIL_LENGTH);
      if (detail) {
        item.detail = detail.text;
        if (detail.truncated || candidate.detail_truncated === true) {
          item.detailTruncated = true;
        }
      }
      items.push(item);
    }
  }

  return { counts, items };
}

export function memoryRecallSummary(counts: MemoryRecallCounts): string | undefined {
  const labels = MEMORY_RECALL_KINDS.flatMap((kind) => {
    const count = counts[kind];
    return count ? [`${MEMORY_RECALL_LABELS[kind]} ${count}`] : [];
  });

  return labels.length > 0 ? `已提供参考：${labels.join(" · ")}` : undefined;
}

export function memoryRecallKindLabel(kind: MemoryRecallKind): string {
  return kind === "query_example"
    ? "查询示例"
    : kind === "business_rule"
      ? "业务口径"
      : "模型结构信息";
}
