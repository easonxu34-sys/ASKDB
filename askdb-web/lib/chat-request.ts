type AgentChatRequestBase = {
  thread_id: string;
  data_source_id?: string;
  model_profile_id?: string;
};

export type AgentChatRequest = AgentChatRequestBase & {
  message: { role: "user"; content: string };
  turn_id: string;
  expected_sequence: number;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: string[]) {
  return Object.keys(value).every((key) => allowed.includes(key));
}

export function sanitizeChatRequest(value: unknown): AgentChatRequest | null {
  if (
    !isRecord(value) ||
    !hasOnlyKeys(value, [
      "thread_id", "data_source_id", "model_profile_id",
      "message", "turn_id", "expected_sequence",
    ])
  ) {
    return null;
  }

  const threadId = value.thread_id;
  const requestedSourceId = value.data_source_id;
  const requestedProfileId = value.model_profile_id;
  if (
    typeof threadId !== "string" ||
    threadId.length < 1 ||
    threadId.length > 128 ||
    (requestedSourceId !== undefined &&
      requestedSourceId !== null &&
      (typeof requestedSourceId !== "string" ||
        requestedSourceId.length < 1 ||
        requestedSourceId.length > 128)) ||
    (requestedProfileId !== undefined &&
      requestedProfileId !== null &&
      (typeof requestedProfileId !== "string" ||
        requestedProfileId.length < 1 ||
        requestedProfileId.length > 128))
  )
    return null;

  const base = {
    thread_id: threadId,
    ...(typeof requestedSourceId === "string" ? { data_source_id: requestedSourceId } : {}),
    ...(typeof requestedProfileId === "string" ? { model_profile_id: requestedProfileId } : {}),
  };
  const currentMessage = value.message;
  const turnId = value.turn_id;
  const expectedSequence = value.expected_sequence;
  if (
    !isRecord(currentMessage) || !hasOnlyKeys(currentMessage, ["role", "content"]) ||
    currentMessage.role !== "user" || typeof currentMessage.content !== "string" ||
    currentMessage.content.length < 1 || currentMessage.content.length > 8192 ||
    typeof turnId !== "string" || !/^[A-Za-z0-9_-]{16,128}$/.test(turnId) ||
    !Number.isSafeInteger(expectedSequence) || (expectedSequence as number) < 0
  ) return null;
  return {
    ...base,
    message: { role: "user", content: currentMessage.content },
    turn_id: turnId,
    expected_sequence: expectedSequence as number,
  };
}
