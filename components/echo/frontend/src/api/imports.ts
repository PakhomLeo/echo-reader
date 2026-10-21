export type ImportMode = "auto" | "download" | "transcribe";
export type ImportStatus =
  | "queued"
  | "running"
  | "pending_index"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "interrupted";
export type AsrBackend = "online" | "local";
export type RequestedAsrBackend = "auto" | AsrBackend;
export type LocalTimestampMode = "model" | "vad";

export interface ImportBackendCapability {
  available: boolean;
  readiness: "ready" | "download_required" | "unavailable";
  reason: string | null;
  /** Local backend only: "openvino_whisper" (default) or "funasr_nano". */
  engine?: string;
}

export interface ImportCapabilities {
  status: string;
  content_root: string;
  batch_max_urls: number;
  recommended_backend: AsrBackend | null;
  backends: Record<AsrBackend, ImportBackendCapability>;
  indexing: { available: boolean; reason: string | null };
}

export interface CreateImportBatchRequest {
  urls: string[];
  mode: ImportMode;
  audio: null;
  audio_format: string;
  speed_factor: number;
  timeout: number | null;
  asr_backend: RequestedAsrBackend;
  local_timestamp_mode: LocalTimestampMode;
  local_diarization: boolean;
  local_speaker_count: number;
}

export interface ImportErrorDetail {
  source?: string | null;
  code: string;
  message: string;
  details?: Record<string, unknown> | null;
}

export interface ImportJob {
  id: string;
  batch_id: string;
  position: number;
  url: string;
  requested_mode: ImportMode;
  effective_mode: ImportMode | null;
  asr_backend: AsrBackend | null;
  status: ImportStatus;
  stage: string | null;
  message: string | null;
  progress: number | null;
  queue_position: number | null;
  eta_seconds: number | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  attempt: number;
  item_id: string | null;
  item_path: string | null;
  warning: string | null;
  quality: Record<string, unknown> | null;
  duplicate: { job_id: string | null; item_id: string | null; score: number | null; kind: string } | null;
  error: (ImportErrorDetail & { retryable: boolean }) | null;
  can_cancel: boolean;
  can_retry: boolean;
  can_reindex: boolean;
  is_active: boolean;
}

export interface ImportBatch {
  id: string;
  status: ImportStatus;
  total: number;
  active_count: number;
  counts: Partial<Record<ImportStatus, number>>;
  created_at: string;
  updated_at: string;
  items: ImportJob[];
}

export interface ImportJobsPage {
  items: ImportJob[];
  page: number;
  page_size: number;
  total: number;
  active_count: number;
  counts: Record<ImportStatus, number>;
}

export interface ImportJobFilters {
  page?: number;
  pageSize?: number;
  status?: ImportStatus | "all";
  batchId?: string;
}

async function importFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new Error("导入服务未连接");
  }
  if (!response.ok) {
    let message = `导入服务请求失败（${response.status}）`;
    try {
      const body = (await response.json()) as {
        message?: string;
        error?: string;
        detail?: string | Array<{ msg?: string; loc?: Array<string | number> }>;
      };
      if (typeof body.detail === "string") {
        message = body.detail;
      } else if (Array.isArray(body.detail)) {
        const details = body.detail
          .slice(0, 3)
          .map((entry) => {
            const location = entry.loc?.filter((part) => part !== "body").join(".");
            return [location, entry.msg].filter(Boolean).join(": ");
          })
          .filter(Boolean);
        if (details.length) {
          message = `请求参数错误：${details.join("；")}${body.detail.length > 3 ? `；另有 ${body.detail.length - 3} 项` : ""}`;
        }
      } else {
        message = body.message || body.error || message;
      }
      if (message.length > 400) message = `${message.slice(0, 397)}…`;
    } catch {
      // The status is the only truthful detail for non-JSON responses.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return importFetch(path, {
    method: "POST",
    ...(body === undefined
      ? {}
      : {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  });
}

export function getImportCapabilities(): Promise<ImportCapabilities> {
  return importFetch("/api/v1/import-capabilities");
}

export function createImportBatch(
  request: CreateImportBatchRequest,
): Promise<ImportBatch> {
  return post("/api/v1/import-batches", request);
}

export function getImportBatch(id: string): Promise<ImportBatch> {
  return importFetch(`/api/v1/import-batches/${encodeURIComponent(id)}`);
}

export function listImportJobs(
  filters: ImportJobFilters = {},
): Promise<ImportJobsPage> {
  const params = new URLSearchParams();
  params.set("page", String(filters.page ?? 1));
  params.set("page_size", String(filters.pageSize ?? 20));
  if (filters.status && filters.status !== "all") params.set("status", filters.status);
  if (filters.batchId) params.set("batch_id", filters.batchId);
  return importFetch(`/api/v1/import-jobs?${params}`);
}

export function getImportJob(id: string): Promise<ImportJob> {
  return importFetch(`/api/v1/import-jobs/${encodeURIComponent(id)}`);
}

export function cancelImportJob(id: string): Promise<ImportJob> {
  return post(`/api/v1/import-jobs/${encodeURIComponent(id)}/cancel`);
}

export function retryImportJob(id: string): Promise<ImportJob> {
  return post(`/api/v1/import-jobs/${encodeURIComponent(id)}/retry`);
}

export function reindexImportJob(id: string): Promise<ImportJob> {
  return post(`/api/v1/import-jobs/${encodeURIComponent(id)}/reindex`);
}

export function retryFailedImportBatch(id: string): Promise<ImportBatch> {
  return post(`/api/v1/import-batches/${encodeURIComponent(id)}/retry-failed`);
}
