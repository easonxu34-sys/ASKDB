import assert from "node:assert/strict";
import test from "node:test";

import { sanitizeChatRequest } from "../lib/chat-request.ts";

const validRequest = () => ({
  thread_id: "thread-synthetic-chat-01",
  model_profile_id: "profile-synthetic-01",
  message: { role: "user", content: "Show recent orders" },
  turn_id: "turn-synthetic-chat-01",
  expected_sequence: 4,
});

test("accepts the current thread, user message, turn sequence, and model profile contract", () => {
  const request = validRequest();
  const result = sanitizeChatRequest(request);

  assert.deepEqual(result, request);
});

test("rejects chat payloads carrying client model configuration or credentials", () => {
  const request = validRequest();

  assert.equal(
    sanitizeChatRequest({ ...request, model: "client-model", base_url: "https://example.invalid" }),
    null,
  );
  assert.equal(sanitizeChatRequest({ ...request, api_key: "not-a-real-fixture" }), null);
});

test("rejects extra fields inside chat messages", () => {
  const request = validRequest();
  request.message.api_key = "not-a-real-fixture";

  assert.equal(sanitizeChatRequest(request), null);
});
