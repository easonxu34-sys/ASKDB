import type { WrenRule } from "@/lib/data-sources";

export function normalizeWrenRuleName(name: string): string {
  return name.normalize("NFKC").trim().replace(/\s+/gu, " ").toLowerCase();
}

export function getDuplicateWrenRuleIndexes(rules: WrenRule[]): Set<number> {
  const indexesByName = new Map<string, number[]>();

  rules.forEach((rule, index) => {
    const normalizedName = normalizeWrenRuleName(rule.name);
    if (!normalizedName) return;
    indexesByName.set(normalizedName, [...(indexesByName.get(normalizedName) ?? []), index]);
  });

  return new Set([...indexesByName.values()].filter((indexes) => indexes.length > 1).flat());
}
