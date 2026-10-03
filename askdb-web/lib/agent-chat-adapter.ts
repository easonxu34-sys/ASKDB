import type { ChatModelAdapter, ThreadMessage } from "@assistant-ui/react";
import {
  ensureServerThread,
  getThreadResultArtifacts,
  getThreadModelProfileId,
  saveThreadResultArtifact,
} from "@/lib/local-thread-adapter";
import { shouldRefreshModelSelection } from "@/lib/model-selection";
import { requestCsrfToken } from "@/lib/auth-api";
import { formatQueryResults } from "@/lib/chat-output";

type AgentEvent = {
  event: string;
  data: Record<string, unknown>;
};

function getMessageText(message: ThreadMessage) {
  return message.content
    .flatMap((part) => (part.type === "text" ? [part.text] : []))
    .join("")
    .trim();
}

function parseEvent(frame: string): AgentEvent | undefined {
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  if (data.length === 0) return undefined;
  try {
    return { event, data: JSON.parse(data.join("\n")) as Record<string, unknown> };
  } catch {
    return undefined;
  }
}

export function createAgentChatAdapter(userId: string): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal, unstable_threadId }) {
      const threadId = unstable_threadId;
      const currentUserMessage = [...messages].reverse().find(
        (message) => message.role === "user" && getMessageText(message),
      );
      if (!threadId || !currentUserMessage) {
        throw new Error("当前会话尚未准备好，请刷新后重试。");
      }
      const serverThreadId = await ensureServerThread(userId, threadId);
      const historyResponse = await fetch(
        `/api/threads/${encodeURIComponent(serverThreadId)}/history`,
        { cache: "no-store", signal: abortSignal },
      );
      if (!historyResponse.ok) {
        if (historyResponse.status === 401) window.location.assign("/login");
        throw new Error("读取会话状态失败，请刷新后重试。");
      }
      const history = await historyResponse.json() as {
        current_sequence?: unknown;
      };
      if (!Number.isSafeInteger(history.current_sequence) || (history.current_sequence as number) < 0) {
        throw new Error("会话状态响应无效，请刷新后重试。");
      }
      const turnId = await stableTurnId(serverThreadId, currentUserMessage.id);
      const historyTurnId = await historyTurnKey(turnId);
      const expectedSequence = stableExpectedSequence(
        userId,
        serverThreadId,
        currentUserMessage.id,
        history.current_sequence as number,
      );
      const csrfToken = await requestCsrfToken();
      const requestBody = JSON.stringify({
        thread_id: serverThreadId,
        model_profile_id: getThreadModelProfileId(userId, serverThreadId),
        turn_id: turnId,
        expected_sequence: expectedSequence,
        message: { role: "user", content: getMessageText(currentUserMessage) },
      });
      const sendTurn = () => fetch("/api/chat", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "text/event-stream",
          "x-csrf-token": csrfToken,
        },
        body: requestBody,
        signal: abortSignal,
      });
      let answer = "";
      const queryResults: unknown[] = [];
      let terminalReceived = false;
      let eofRetries = 0;
      const update = () => ({
        content: [
          {
            type: "text" as const,
            text: [formatQueryResults(queryResults), answer].filter(Boolean).join("\n\n"),
          },
        ],
      });

      while (!terminalReceived) {
        let response = await sendTurn();
        // A running turn is resumed by polling the same idempotency key until the
        // Agent can return its completed replay envelope.
        for (let attempt = 0; response.status === 409 && attempt < 30; attempt += 1) {
          const detail = await response.clone().json().catch(() => null) as {
            code?: unknown;
            detail?: { code?: unknown };
          } | null;
          if ((detail?.code ?? detail?.detail?.code) !== "THREAD_TURN_RUNNING") break;
          await abortableDelay(1000, abortSignal);
          response = await sendTurn();
        }

        if (!response.ok || !response.body) {
          const detail = await response.text();
          let message = detail || `智能助手请求失败（${response.status}）。`;
          try {
            const payload = JSON.parse(detail) as {
              code?: string;
              message?: string;
              detail?: { code?: string; message?: string };
            };
            const errorCode = payload.code ?? payload.detail?.code;
            if (shouldRefreshModelSelection(errorCode)) {
              window.dispatchEvent(new Event("askdb:model-catalog-updated"));
              message =
                errorCode === "MODEL_PROFILE_NOT_FOUND"
                  ? "此会话所选模型已删除，正在更新会话选择。"
                  : errorCode === "MODEL_NOT_CONFIGURED"
                    ? "此会话所选模型已不可用，正在更新会话选择。"
                    : "此会话模型配置已更新，请重试。";
            } else {
              if (errorCode === "DATA_SOURCE_NOT_FOUND" || errorCode === "DATA_SOURCE_UNAVAILABLE") {
                window.dispatchEvent(new Event("askdb:data-source-catalog-updated"));
              }
              message = payload.message ?? payload.detail?.message ?? message;
            }
          } catch {
            // Keep the text response when the BFF did not return JSON.
          }
          if (response.status === 401) window.location.assign("/login");
          throw new Error(message);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        const consumeFrame = (frame: string) => {
          const event = parseEvent(frame);
          if (!event || event.data.thread_id !== serverThreadId || event.data.turn_id !== turnId ||
            !Number.isSafeInteger(event.data.user_sequence) ||
            event.data.user_sequence !== expectedSequence + 1) return undefined;
          return event;
        };
        const consumeBuffer = function* (flush = false) {
          buffer = buffer.replaceAll("\r\n", "\n");
          let boundary = buffer.indexOf("\n\n");
          while (boundary >= 0) {
            const frame = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            const event = consumeFrame(frame);
            if (event) yield event;
            boundary = buffer.indexOf("\n\n");
          }
          if (flush && buffer.trim()) {
            const event = consumeFrame(buffer);
            buffer = "";
            if (event) yield event;
          }
        };

        while (true) {
          const { done, value } = await reader.read();
          buffer += decoder.decode(value, { stream: !done });
          for (const event of consumeBuffer(done)) {
            if (event.event === "token" && typeof event.data.text === "string") {
              answer += event.data.text;
              yield update();
            } else if (event.event === "replay" && typeof event.data.assistant_content === "string") {
              answer = event.data.assistant_content;
              queryResults.splice(
                0,
                queryResults.length,
                ...getThreadResultArtifacts(userId, serverThreadId, historyTurnId, turnId),
              );
              yield update();
            } else if (event.event === "result") {
              queryResults.push(event.data.output);
              saveThreadResultArtifact(userId, serverThreadId, historyTurnId, event.data.output);
              if (formatQueryResults(queryResults)) yield update();
            } else if (event.event === "error" && typeof event.data.message === "string") {
              answer += `${answer ? "\n\n" : ""}${event.data.message}`;
              yield update();
            } else if (event.event === "done") {
              terminalReceived = true;
              if (event.data.status === "failed" && !answer) {
                answer = "此轮回答未完成，请使用新消息重试。";
                yield update();
              }
              break;
            }
          }
          if (done || terminalReceived) break;
        }
        if (!terminalReceived) {
          if (eofRetries >= 3) {
            throw new Error("回答流意外结束；已尝试从服务端恢复同一轮回答，请稍后重试。");
          }
          eofRetries += 1;
          await abortableDelay(500, abortSignal);
        }
      }
    },
  };
}

async function stableTurnId(threadId: string, messageId: string): Promise<string> {
  const bytes = new TextEncoder().encode(`${threadId}\u0000${messageId}`);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const hex = Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
  return `turn_${hex}`;
}

async function historyTurnKey(turnId: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(turnId));
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}

function stableExpectedSequence(
  userId: string,
  threadId: string,
  messageId: string,
  serverSequence: number,
) {
  const key = `askdb:user:${encodeURIComponent(userId)}:chat:turn:${threadId}:${messageId}`;
  try {
    const stored = window.localStorage.getItem(key);
    if (stored !== null) {
      const saved = Number(stored);
      if (Number.isSafeInteger(saved) && saved >= 0) return saved;
    }
    window.localStorage.setItem(key, String(serverSequence));
  } catch {
    // The current stream still works when browser storage is unavailable.
  }
  return serverSequence;
}

function abortableDelay(milliseconds: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason);
      return;
    }
    const timeout = window.setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    const onAbort = () => {
      window.clearTimeout(timeout);
      reject(signal.reason);
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}
