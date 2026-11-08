// TypeScript 描述预期结构；收到网络响应后，仍要检查真实数据。
export interface HealthResponse {
  status: "ok";
  code?: number;
}

export const HEALTH_ENDPOINT = "/api/v1/health";
export const BACKEND_DOCS_URL = "/docs";

function isHealthResponse(value: unknown): value is HealthResponse {
  if (typeof value !== "object" || value === null || !("status" in value)) {
    return false;
  }

  return value.status === "ok" && (!("code" in value) || value.code === 200);
}

export async function checkBackendHealth(): Promise<HealthResponse> {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 5_000);

  try {
    const response = await fetch(HEALTH_ENDPOINT, {
      signal: controller.signal,
      cache: "no-store",
    });

    if (!response.ok) {
      throw new Error(
        `后端请求失败，HTTP 状态码为 ${response.status}。请确认后端服务正在运行。`,
      );
    }

    const data: unknown = await response.json();

    if (!isHealthResponse(data)) {
      throw new Error("后端返回的数据不符合健康检查格式，请查看 API 文档。");
    }

    return data;
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error("请求超过 5 秒，请确认后端已经启动，然后重新检查。");
    }

    if (error instanceof TypeError) {
      throw new Error("无法发送请求，请检查网络、前端代理配置和后端服务。");
    }

    if (error instanceof SyntaxError) {
      throw new Error("后端没有返回有效的 JSON，请查看接口响应。");
    }

    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}
