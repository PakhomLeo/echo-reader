export type SourceType = "article" | "video" | "audio" | "mixed";

export interface ItemSummary {
  id: string;
  folder_name: string;
  source_type: SourceType | null;
  has_article: boolean;
  has_transcript: boolean;
  has_media: boolean;
  updated_at: string;
  source_title: string | null;
  source_author: string | null;
  published_at: string | null;
}

export interface ItemsResponse {
  items: ItemSummary[];
}

export interface MediaInfo {
  type: "video" | "audio";
  title: string | null;
  url: string;
  mime_type: string;
}

export interface MediaItemInfo extends MediaInfo {
  id: string;
  role: "primary" | "inline" | "attachment";
  description: string | null;
}

export interface ArticleInfo {
  available: boolean;
  markdown?: string;
  version?: string;
}

export interface TranscriptInfo {
  available: boolean;
  segment_count: number;
  version?: string;
}

export interface ItemDetail {
  id: string;
  folder_name: string;
  source: {
    type: SourceType | null;
    url: string | null;
    title: string | null;
    author: string | null;
    published_at: string | null;
    description: string | null;
  };
  media: MediaInfo | null;
  media_items: MediaItemInfo[];
  article: ArticleInfo;
  transcript: TranscriptInfo;
  updated_at: string;
}

export interface TranscriptSegment {
  id: string;
  start_ms: number;
  end_ms: number;
  speaker: string;
  text: string;
}

export interface TranscriptData {
  version: string;
  language: string;
  segments: TranscriptSegment[];
}

export interface SaveResult {
  version: string;
  saved_at: string;
}
