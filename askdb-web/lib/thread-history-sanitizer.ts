import type { ExportedMessageRepository } from "@assistant-ui/react";

export type ImportedTurn = { user_content: string; assistant_content: string };
export type PreparedHistory = {
  turns: ImportedTurn[];
  assistantMessageIds: string[];
  sanitizedTurns: number;
  omittedTurns: number;
};

export const MAX_IMPORTED_HISTORY_BYTES = 2 * 1024 * 1024;
export const MAX_IMPORTED_TURNS = 500;

type StoredRepository = ExportedMessageRepository;

function branchMessages(repository: StoredRepository) {
  const byId = new Map(repository.messages.map((entry) => [entry.message.id, entry]));
  const path: StoredRepository["messages"] = [];
  let messageId = repository.headId ?? repository.messages.at(-1)?.message.id ?? null;
  const seen = new Set<string>();
  while (messageId && !seen.has(messageId)) {
    seen.add(messageId);
    const entry = byId.get(messageId);
    if (!entry) break;
    path.push(entry);
    messageId = entry.parentId;
  }
  return path.reverse();
}

const IMPORT_FENCED_BLOCK = /```[\s\S]*?```|~~~[\s\S]*?~~~/g;
const IMPORT_RESULT_BLOCK =
  /<(result|tool_result|tool|observation|think|analysis)[^>]*>[\s\S]*?<\/\1\s*>/gi;
const IMPORT_RESULT_LABEL =
  /^\s*(?:\*\*)?(?:SQL\s*(?:语句|statement)|查询结果(?:\s*[·|｜].*)?|query\s+results?)(?:\*\*)?\s*$/gim;
const IMPORT_SECRET =
  /\b(?:api[_ -]?key|password|passwd|token|secret|access[_ -]?key)\b\s*[:=]\s*[^\s,;]+/gi;
const IMPORT_EMAIL = /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi;
const IMPORT_PHONE =
  /(?<![\w])(?:\+?86[- ]?)?1[3-9]\d{9}(?![\w])|(?<![\w])(?:\+?\d{1,3}[- ])?(?:\(?\d{2,4}\)?[- ])?\d{3,4}[- ]\d{4}(?![\w])/g;
const IMPORT_RECORD_ID =
  /(?:(?:customer|client|user|member|account|order)[_ -]?(?:id|no|number)|(?:客户|用户|会员|账号|订单)(?:id|ID|编号|号码|号))\s*[:=：#]?\s*["']?[A-Z0-9][A-Z0-9_-]{2,}["']?/gi;
const IMPORT_SQL_LINE =
  /^\s*(?:SELECT|WITH|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|EXPLAIN)\b/i;
const IMPORT_SQL_CONTINUATION =
  /^\s*(?:FROM\b|JOIN\b|LEFT\b|RIGHT\b|INNER\b|FULL\b|CROSS\b|ON\b|WHERE\b|GROUP\s+BY\b|ORDER\s+BY\b|HAVING\b|LIMIT\b|OFFSET\b|UNION\b|INTERSECT\b|EXCEPT\b|AND\b|OR\b|AS\b|ASC\b|DESC\b|,|\)|;|\.)/i;

function sanitizeImportedText(value: string, maxChars = 8192) {
  let text = value
    .slice(0, maxChars * 4)
    .replace(IMPORT_FENCED_BLOCK, " ")
    .replace(IMPORT_RESULT_BLOCK, " ")
    .replace(IMPORT_RESULT_LABEL, " ");
  const keptLines: string[] = [];
  let insideSql = false;
  // Match Python's str.splitlines() so Web-side sanitization and the Agent's
  // sanitizer agree for uncommon Unicode and control-character line breaks.
  // eslint-disable-next-line no-control-regex
  for (const line of text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/)) {
    const stripped = line.trim();
    if (stripped.startsWith("|")) continue;
    if (IMPORT_SQL_LINE.test(line)) {
      insideSql = !line.includes(";");
      continue;
    }
    if (insideSql) {
      if (!stripped || IMPORT_SQL_CONTINUATION.test(line)) {
        if (line.includes(";")) insideSql = false;
        continue;
      }
      insideSql = false;
    }
    keptLines.push(line);
  }
  text = keptLines
    .join("\n")
    .replace(IMPORT_SECRET, "[已脱敏]")
    .replace(IMPORT_EMAIL, "[已脱敏标识]")
    .replace(IMPORT_PHONE, "[已脱敏标识]")
    .replace(IMPORT_RECORD_ID, "[已脱敏记录标识]")
    .split("\n")
    .map((part) => part.replace(/\s+$/, ""))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return text.slice(0, maxChars);
}

export function importableHistory(repository: StoredRepository): PreparedHistory {
  const result: ImportedTurn[] = [];
  const assistantMessageIds: string[] = [];
  let sanitizedTurns = 0;
  let omittedTurns = 0;
  let pendingUser: string | null = null;
  for (const entry of branchMessages(repository)) {
    const { message } = entry;
    const text = message.content
      .flatMap((part) => (part.type === "text" ? [part.text] : []))
      .join("\n")
      .trim();
    if (message.role === "user") {
      if (pendingUser !== null) omittedTurns += 1;
      pendingUser = text || null;
      continue;
    }
    if (message.role === "assistant" && pendingUser !== null) {
      const safeUser = sanitizeImportedText(pendingUser);
      const safeAssistant = sanitizeImportedText(text);
      if (safeUser !== pendingUser || safeAssistant !== text) sanitizedTurns += 1;
      if (safeUser && safeAssistant) {
        result.push({ user_content: safeUser, assistant_content: safeAssistant });
        assistantMessageIds.push(message.id);
      } else {
        omittedTurns += 1;
      }
      pendingUser = null;
    }
  }
  if (pendingUser !== null) omittedTurns += 1;
  let contentBytes = result.reduce(
    (size, turn) =>
      size +
      new TextEncoder().encode(turn.user_content).byteLength +
      new TextEncoder().encode(turn.assistant_content).byteLength,
    0,
  );
  while (result.length > MAX_IMPORTED_TURNS || contentBytes > MAX_IMPORTED_HISTORY_BYTES) {
    const removed = result.shift();
    if (!removed) break;
    assistantMessageIds.shift();
    contentBytes -=
      new TextEncoder().encode(removed.user_content).byteLength +
      new TextEncoder().encode(removed.assistant_content).byteLength;
    omittedTurns += 1;
  }
  return { turns: result, assistantMessageIds, sanitizedTurns, omittedTurns };
}

export function extractLegacyResultArtifact(
  entry: StoredRepository["messages"][number],
  assistantContent: string,
) {
  if (!assistantContent) return "";
  if (entry.message.role !== "assistant") return "";
  const cachedText = entry.message.content
    .flatMap((part) => (part.type === "text" ? [part.text] : []))
    .join("\n")
    .trim();
  const separator = `\n\n${assistantContent}`;
  if (!cachedText.endsWith(separator)) return "";
  const artifact = cachedText.slice(0, -separator.length).trim();
  return /\*\*SQL 语句\*\*|查询结果|```sql/i.test(artifact) ? artifact : "";
}
