import type { WrenRule } from "@/lib/data-sources";

const SESSION_SUBMITTED_RULE_NAME = /^askdb_br_[0-9a-f]{32}$/;

export type WrenRuleOrigin = "session" | "data_source";

export function getWrenRuleOrigin(rule: Pick<WrenRule, "name">): WrenRuleOrigin {
  return SESSION_SUBMITTED_RULE_NAME.test(rule.name) ? "session" : "data_source";
}

export function getWrenRuleDisplayName(rule: WrenRule): string {
  if (getWrenRuleOrigin(rule) === "session") {
    const heading = rule.content.match(/^#\s+([^\r\n]+)\s*$/m)?.[1]?.trim();
    if (heading) return heading;
  }

  return rule.name.trim() || "未命名规则";
}
