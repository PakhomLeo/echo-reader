import { apiFetch } from "./client";
import type {
  ItemDetail,
  ItemsResponse,
  SaveResult,
  TranscriptData,
  TranscriptSegment,
} from "./types";

export function listItems(): Promise<ItemsResponse> {
  return apiFetch<ItemsResponse>("/api/v1/items");
}

export function getItem(id: string): Promise<ItemDetail> {
  return apiFetch<ItemDetail>(`/api/v1/items/${encodeURIComponent(id)}`);
}

export function rescanLibrary(): Promise<{
  item_count: number;
  invalid_item_count: number;
}> {
  return apiFetch<{ item_count: number; invalid_item_count: number }>(
    "/api/v1/library/rescan",
    { method: "POST" },
  );
}

export interface BrowseEntry {
  name: string;
  is_dir: boolean;
}

export interface BrowseResponse {
  path: string;
  parent: string | null;
  entries: BrowseEntry[];
}

export interface AppConfig {
  library_root: string;
  state_root: string;
  cache_root: string;
  logs_root: string;
  tmp_root: string;
  config_root: string;
  install_mode: boolean;
}

export function getAppConfig(): Promise<AppConfig> {
  return apiFetch<AppConfig>("/api/v1/app-config");
}

export function shutdownApp(): Promise<{ status: string }> {
  return apiFetch<{ status: string }>("/api/v1/app/shutdown", { method: "POST" });
}

export function browseDirectory(path: string): Promise<BrowseResponse> {
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  return apiFetch<BrowseResponse>(`/api/v1/library/browse${query}`);
}

export function switchLibrary(
  path: string,
): Promise<{ library_root: string; item_count: number; invalid_item_count: number }> {
  return apiFetch<{ library_root: string; item_count: number; invalid_item_count: number }>(
    "/api/v1/library/switch",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    },
  );
}

export function getTranscript(id: string): Promise<TranscriptData> {
  return apiFetch<TranscriptData>(
    `/api/v1/items/${encodeURIComponent(id)}/transcript`,
  );
}

export interface TranscriptChange {
  id: string;
  speaker?: string;
  text?: string;
}

export function patchTranscript(
  id: string,
  expectedVersion: string,
  changes: TranscriptChange[],
): Promise<SaveResult> {
  return apiFetch<SaveResult>(
    `/api/v1/items/${encodeURIComponent(id)}/transcript`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: expectedVersion, changes }),
    },
  );
}

export function deleteImageReference(
  id: string,
  src: string,
  occurrence: number,
  expectedVersion: string,
): Promise<{
  article: { markdown: string; version: string };
  saved_at: string;
}> {
  return apiFetch<{
    article: { markdown: string; version: string };
    saved_at: string;
  }>(`/api/v1/items/${encodeURIComponent(id)}/image-reference`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      src,
      occurrence,
      expected_version: expectedVersion,
    }),
  });
}

export function uploadImage(
  id: string,
  file: File,
): Promise<{ relative_path: string; markdown: string }> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<{ relative_path: string; markdown: string }>(
    `/api/v1/items/${encodeURIComponent(id)}/images`,
    { method: "POST", body: form },
  );
}

export function saveArticle(
  id: string,
  markdown: string,
  expectedVersion: string,
): Promise<SaveResult> {
  return apiFetch<SaveResult>(
    `/api/v1/items/${encodeURIComponent(id)}/article`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ markdown, expected_version: expectedVersion }),
    },
  );
}

export type { TranscriptSegment };
