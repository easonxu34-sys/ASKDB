export type AuthUser = {
  user_id: string;
  username: string;
  role: "admin" | "member";
  must_change_password: boolean;
};

export type AdminUser = {
  id: string;
  username: string;
  role: "admin" | "member";
  is_active: boolean;
  must_change_password: boolean;
  created_at?: string;
  updated_at?: string;
  last_login_at?: string | null;
  data_source_ids: string[];
};

export async function requestCsrfToken(): Promise<string> {
  const response = await fetch("/api/auth/csrf", { cache: "no-store" });
  if (!response.ok) throw new Error("安全校验暂不可用，请刷新后重试。");
  const value: unknown = await response.json();
  if (!value || typeof value !== "object" || !("csrf_token" in value) ||
    typeof value.csrf_token !== "string") {
    throw new Error("安全校验响应无效，请刷新后重试。");
  }
  return value.csrf_token;
}

export async function authMutation(
  path: string,
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  body?: unknown,
): Promise<Response> {
  const csrfToken = await requestCsrfToken();
  return fetch(path, {
    method,
    headers: {
      "x-csrf-token": csrfToken,
      ...(body === undefined ? {} : { "content-type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    cache: "no-store",
    credentials: "same-origin",
  });
}

export async function fetchCurrentUser(): Promise<AuthUser | null> {
  const response = await fetch("/api/auth/me", {
    cache: "no-store",
    credentials: "same-origin",
  });
  if (response.status === 401) return null;
  if (!response.ok) throw new Error("登录状态暂时无法读取，请稍后重试。");
  const value: unknown = await response.json();
  if (!isAuthUser(value)) throw new Error("登录状态响应无效。");
  return value;
}

export function isAuthUser(value: unknown): value is AuthUser {
  if (!value || typeof value !== "object") return false;
  const user = value as Record<string, unknown>;
  return typeof user.user_id === "string" && typeof user.username === "string" &&
    (user.role === "admin" || user.role === "member") &&
    typeof user.must_change_password === "boolean";
}

export async function responseError(response: Response, fallback: string): Promise<string> {
  try {
    const value: unknown = await response.json();
    if (!value || typeof value !== "object") return fallback;
    const body = value as Record<string, unknown>;
    if (typeof body.message === "string") return body.message;
    const detail = body.detail;
    if (detail && typeof detail === "object" && "message" in detail &&
      typeof detail.message === "string") return detail.message;
  } catch {
    // Use the stable fallback when the response isn't JSON.
  }
  return fallback;
}
