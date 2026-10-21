export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, details: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let code = "UNKNOWN_ERROR";
    let message = "请求失败，请稍后重试";
    let details: unknown = null;
    try {
      const body = (await res.json()) as {
        code?: string;
        message?: string;
        details?: unknown;
        detail?: string | { code?: string; message?: string };
      };
      if (body.code) code = body.code;
      if (body.message) message = body.message;
      if (typeof body.detail === "string") message = body.detail;
      if (body.detail && typeof body.detail === "object") {
        if (body.detail.code) code = body.detail.code;
        if (body.detail.message) message = body.detail.message;
      }
      details = body.details ?? null;
    } catch {
      // 非 JSON 响应，保留默认信息
    }
    throw new ApiError(res.status, code, message, details);
  }
  return (await res.json()) as T;
}
