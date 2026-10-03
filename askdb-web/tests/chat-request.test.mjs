import assert from "node:assert/strict";
import test from "node:test";

import { sanitizeChatRequest } from "../lib/chat-request.ts";

const validRequest = () => ({
  thread_id: "thread-synthetic-chat-01",
  model_profile_id: "profile-synthetic-01",
  messages: [
    { role: "user", content: "Show recent orders" },
    { role: "assistant", content: "I can check that." },
  ],
});

test("forwards only the thread, messages, and model profile ID", () => {
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
  request.messages[0].api_key = "not-a-real-fixture";

  assert.equal(sanitizeChatRequest(request), null);
});
