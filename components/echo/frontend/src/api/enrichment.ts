import { apiFetch } from "./client";

export type FeatureName = "tts" | "ocr";

export interface FeatureState {
  available: boolean;
  reason: string | null;
  engine?: string;
  /** Present when unavailable, e.g. "朗读（Kokoro 语音合成）功能未启用：未安装可选依赖。…" */
  message?: string;
}

export type Features = Record<FeatureName, FeatureState>;

export interface EnrichmentJob {
  id: string;
  kind: FeatureName;
  item_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  created_at: string;
  finished_at: string | null;
  error: string | null;
  result: Record<string, unknown> | null;
}

export interface OcrImageResult {
  path: string;
  text: string;
  confidence?: number | null;
  error?: string;
}

export interface OcrSidecar {
  schema_version: number;
  engine: string;
  created_at: string;
  images: OcrImageResult[];
}

export function getFeatures(): Promise<Features> {
  return apiFetch<Features>("/api/v1/features");
}

export function startEnrichment(
  feature: FeatureName,
  itemId: string,
): Promise<EnrichmentJob> {
  return apiFetch<EnrichmentJob>(
    `/api/v1/items/${encodeURIComponent(itemId)}/${feature}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    },
  );
}

export function getEnrichmentJob(jobId: string): Promise<EnrichmentJob> {
  return apiFetch<EnrichmentJob>(
    `/api/v1/enrichment-jobs/${encodeURIComponent(jobId)}`,
  );
}

export function getItemOcr(itemId: string): Promise<OcrSidecar> {
  return apiFetch<OcrSidecar>(`/api/v1/items/${encodeURIComponent(itemId)}/ocr`);
}
