import { timingSafeEqual, randomBytes } from "node:crypto";
import { cookies } from "next/headers";

export const SESSION_COOKIE = "__Host-askdb_session";
export const CSRF_COOKIE = "__Host-askdb_csrf";

const NO_STORE = {
  "cache-control": "no-store, max-age=0",
  pragma: "no-cache",
  "content-type": "application/json; charset=utf-8",
};

type AccessResult = { token: string } | { response: Response };

function errorResponse(code: string, message: string, status: number) {
  return Response.json({ code, message }, { status, headers: NO_STORE });
}

function cookieValue(request: Request, name: string): string | null {
  const header = request.headers.get("cookie");
  if (!header) return null;
  for (const entry of header.split(";")) {
    const separator = entry.indexOf("=");
    if (separator < 0 || entry.slice(0, separator).trim() !== name) continue;
    return entry.slice(separator + 1).trim();
  }
  return null;
}

function csrfMatches(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return false;
  let originValue: string;
  let expectedValue: string;
  try {
    originValue = new URL(origin).origin;
    expectedValue = new URL(process.env.ASKDB_WEB_ORIGIN ?? request.url).origin;
  } catch {
    return false;
  }
  if (originValue !== expectedValue) return false;

  const cookieToken = cookieValue(request, CSRF_COOKIE);
  const headerToken = request.headers.get("x-csrf-token");
  if (!cookieToken || !headerToken) return false;
  const cookieBytes = Buffer.from(cookieToken);
  const headerBytes = Buffer.from(headerToken);
  return cookieBytes.length === headerBytes.length && timingSafeEqual(cookieBytes, headerBytes);
}

export function requireCsrf(request: Request): Response | null {
  if (csrfMatches(request)) return null;
  return errorResponse("CSRF_CHECK_FAILED", "请求来源校验失败，请刷新页面后重试。", 403);
}

export async function requireAgentSession(
  request: Request,
  mutating = false,
): Promise<AccessResult> {
  if (mutating) {
    const csrfError = requireCsrf(request);
    if (csrfError) return { response: csrfError };
  }
  const store = await cookies();
  const token = store.get(SESSION_COOKIE)?.value;
  if (!token || token.length > 512) {
    return {
      response: errorResponse("AUTH_REQUIRED", "登录状态已失效，请重新登录。", 401),
    };
  }
  return { token };
}

export async function setSessionCookie(token: string): Promise<void> {
  const store = await cookies();
  store.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
  });
}

export async function clearSessionCookie(): Promise<void> {
  const store = await cookies();
  store.set(SESSION_COOKIE, "", {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
}

export async function csrfTokenFor(): Promise<string> {
  const store = await cookies();
  const current = store.get(CSRF_COOKIE)?.value;
  const token = current && /^[A-Za-z0-9_-]{40,64}$/.test(current)
    ? current
    : randomBytes(32).toString("base64url");
  if (token !== current) {
    store.set(CSRF_COOKIE, token, {
      httpOnly: false,
      secure: true,
      sameSite: "lax",
      path: "/",
    });
  }
  return token;
}

export function agentUrl(path: string): string {
  const base = (process.env.ASKDB_AGENT_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  const url = new URL(base);
  const hostname = url.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  const ipv4Parts = hostname.split(".");
  const isIpv4Loopback = ipv4Parts.length === 4 && ipv4Parts[0] === "127" &&
    ipv4Parts.every((part) => /^\d{1,3}$/.test(part) && Number(part) <= 255);
  const isLoopback = hostname === "localhost" || hostname === "::1" || isIpv4Loopback;
  if (url.username || url.password || !["http:", "https:"].includes(url.protocol) ||
    (url.protocol === "http:" && !isLoopback)) {
    throw new Error("ASKDB_AGENT_URL must use HTTPS except for loopback development.");
  }
  return `${base}${path}`;
}

export async function proxyAgentJson(
  request: Request,
  path: string,
  options: {
    body?: unknown;
    method?: string;
    bodyLimit?: number;
    allowedBodyKeys?: readonly string[];
  } = {},
): Promise<Response> {
  const method = options.method ?? request.method;
  const access = await requireAgentSession(request, !["GET", "HEAD"].includes(method));
  if ("response" in access) return access.response;

  let body: string | undefined;
  if (options.body !== undefined) {
    if (options.allowedBodyKeys && !hasOnlyBodyKeys(options.body, options.allowedBodyKeys)) {
      return errorResponse("REQUEST_INVALID", "请求字段无效。", 400);
    }
    body = JSON.stringify(options.body);
  } else if (
    !["GET", "HEAD"].includes(method) && request.body &&
    (method !== "DELETE" || request.headers.get("content-type")?.includes("application/json"))
  ) {
    const limit = options.bodyLimit ?? 64 * 1024;
    const declared = request.headers.get("content-length");
    if (declared && /^\d+$/.test(declared) && Number(declared) > limit) {
      return errorResponse("REQUEST_TOO_LARGE", "请求内容过大。", 413);
    }
    const reader = request.body.getReader();
    const chunks: Uint8Array[] = [];
    let receivedBytes = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      receivedBytes += value.byteLength;
      if (receivedBytes > limit) {
        try {
          await reader.cancel();
        } catch {
          // Preserve the bounded-size response even if the client has disconnected.
        }
        return errorResponse("REQUEST_TOO_LARGE", "请求内容过大。", 413);
      }
      chunks.push(value);
    }
    const bytes = new Uint8Array(receivedBytes);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    let raw: string;
    try {
      raw = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      return errorResponse("REQUEST_INVALID", "请求格式无效。", 400);
    }
    if (raw.length > 0) {
      try {
        const parsed: unknown = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          return errorResponse("REQUEST_INVALID", "请求格式无效。", 400);
        }
        if (options.allowedBodyKeys && !hasOnlyBodyKeys(parsed, options.allowedBodyKeys)) {
          return errorResponse("REQUEST_INVALID", "请求字段无效。", 400);
        }
        body = JSON.stringify(parsed);
      } catch {
        return errorResponse("REQUEST_INVALID", "请求格式无效。", 400);
      }
    }
  }
  if (body && new TextEncoder().encode(body).byteLength > (options.bodyLimit ?? 64 * 1024)) {
    return errorResponse("REQUEST_TOO_LARGE", "请求内容过大。", 413);
  }

  let upstream: Response;
  try {
    upstream = await fetch(agentUrl(path), {
      method,
      headers: {
        authorization: `Bearer ${access.token}`,
        ...(body ? { "content-type": "application/json" } : {}),
        accept: "application/json",
      },
      ...(body ? { body } : {}),
      cache: "no-store",
      signal: request.signal,
    });
  } catch {
    return errorResponse("AGENT_UNAVAILABLE", "AskDB Agent 暂时不可用。", 503);
  }

  const text = await upstream.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) as unknown : null;
  } catch {
    return errorResponse("AGENT_RESPONSE_INVALID", "AskDB Agent 返回了无效响应。", 502);
  }
  if (upstream.status === 401) await clearSessionCookie();
  return Response.json(payload ?? {}, {
    status: upstream.status,
    headers: NO_STORE,
  });
}

function hasOnlyBodyKeys(value: unknown, allowedKeys: readonly string[]): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  return Object.keys(value).every((key) => allowedKeys.includes(key));
}
