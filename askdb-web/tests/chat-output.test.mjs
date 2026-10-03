import assert from "node:assert/strict";
import test from "node:test";

test("formats every executed query result in order", async () => {
  const chatOutput = await import("../lib/chat-output.ts").catch(() => null);
  assert.ok(chatOutput, "chat output formatter should be available");

  const formatted = chatOutput.formatQueryResults([
    { data: { sql: "SELECT 1", columns: ["value"], rows: [{ value: 1 }] } },
    { data: { sql: "SELECT 2", columns: ["value"], rows: [{ value: 2 }] } },
  ]);

  assert.ok(formatted.includes("SELECT 1"));
  assert.ok(formatted.includes("SELECT 2"));
  assert.ok(formatted.indexOf("SELECT 1") < formatted.indexOf("SELECT 2"));
});
