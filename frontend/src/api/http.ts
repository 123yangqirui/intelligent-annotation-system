import { ref } from "vue";

export interface User {
  id: string;
  username: string;
  is_admin: boolean;
  enabled: boolean;
}
export const user = ref<User | null>(null);
let token = "";
let refreshing: Promise<boolean> | null = null;

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code: string,
    public requestId = "",
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function restoreSession(): Promise<boolean> {
  if (refreshing) return refreshing;
  refreshing = (async () => {
    const response = await fetch("/api/v1/auth/refresh", {
      method: "POST",
      credentials: "include",
    });
    if (!response.ok) {
      token = "";
      user.value = null;
      return false;
    }
    const data = await response.json();
    token = data.access_token;
    user.value = data.user;
    return true;
  })()
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

export async function api<T = any>(
  path: string,
  options: RequestInit = {},
  retry = true,
): Promise<T> {
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...options,
      headers,
      credentials: "include",
    });
  } catch {
    throw new ApiError("网络连接失败，请检查服务并重试。", 0, "NETWORK_ERROR");
  }
  if (response.status === 401 && retry && !path.startsWith("/auth/")) {
    if (await restoreSession()) return api(path, options, false);
    user.value = null;
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail =
      data.error?.message ??
      data.detail?.[0]?.msg ??
      `请求失败 (${response.status})`;
    throw new ApiError(
      detail,
      response.status,
      data.error?.code || "HTTP_ERROR",
      data.error?.request_id || response.headers.get("X-Request-ID") || "",
    );
  }
  return response.json();
}

export async function login(username: string, password: string) {
  const data = await api<{ access_token: string; user: User }>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  token = data.access_token;
  user.value = data.user;
}

export async function logout() {
  await api("/auth/logout", { method: "POST" });
  token = "";
  user.value = null;
}
export const post = <T = any>(
  path: string,
  body?: unknown,
  headers?: HeadersInit,
) =>
  api<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
    headers,
  });
export const jobHeaders = () => ({ "Idempotency-Key": crypto.randomUUID() });

export async function fileBlob(path: string): Promise<Blob> {
  let response = await fetch(`/api/v1${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (response.status === 401 && (await restoreSession()))
    response = await fetch(`/api/v1${path}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
  if (!response.ok) throw new Error("文件不可用或没有访问权限");
  return response.blob();
}

export async function download(path: string, filename: string) {
  const url = URL.createObjectURL(await fileBlob(path));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
