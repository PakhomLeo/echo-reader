import { apiFetch } from "./client";

export type SearchHitKind = "title" | "article" | "transcript" | "ocr";

export interface SearchHit {
  item_id: string;
  folder_name: string;
  title: string;
  kind: SearchHitKind;
  locator: string;
  start_ms: number | null;
  snippet: string;
  score: number;
}

export interface SearchResponse {
  query: string;
  effective_query: string;
  hits: SearchHit[];
}

export interface SearchIndexStatus {
  index_path: string;
  schema_version: string;
  indexed_items: number;
  library_items: number;
  chunks: number;
  stale_items: string[];
  missing_items: string[];
  orphan_items: string[];
  up_to_date: boolean;
}

export type SearchMode = "keyword" | "question";

export function searchLibrary(
  q: string,
  mode: SearchMode = "keyword",
  limit = 50,
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q, mode, limit: String(limit) });
  return apiFetch<SearchResponse>(`/api/v1/search?${params.toString()}`);
}

export function getSearchStatus(): Promise<SearchIndexStatus> {
  return apiFetch<SearchIndexStatus>("/api/v1/search/status");
}

export function rebuildSearchIndex(): Promise<{
  rebuilt: { added: number; updated: number; removed: number; unchanged: number };
  status: SearchIndexStatus;
}> {
  return apiFetch("/api/v1/search/rebuild", { method: "POST" });
}
