import assert from "node:assert/strict";
import test from "node:test";

import { getDuplicateWrenRuleIndexes, normalizeWrenRuleName } from "../lib/wren-rule-validation.ts";

test("normalizes compatibility characters, case, and repeated whitespace", () => {
  assert.equal(normalizeWrenRuleName("  ＲＥＶＥＮＵＥ   Rule "), "revenue rule");
});

test("marks every rule that shares a normalized name", () => {
  assert.deepEqual(
    [
      ...getDuplicateWrenRuleIndexes([
        { name: "异常地区", content: "first" },
        { name: "  异常地区  ", content: "second" },
        { name: "收入", content: "third" },
      ]),
    ],
    [0, 1],
  );
});

test("does not mark blank names or unique normalized names", () => {
  assert.deepEqual(
    [
      ...getDuplicateWrenRuleIndexes([
        { name: "  ", content: "blank" },
        { name: "收入", content: "first" },
        { name: "地区", content: "second" },
      ]),
    ],
    [],
  );
});
