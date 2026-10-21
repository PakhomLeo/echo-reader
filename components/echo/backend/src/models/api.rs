use serde::Serialize;

use super::item::{MediaItem, MediaKind, MediaRole, PrimaryMedia, SourceType, ValidItem};

#[derive(Debug, Serialize)]
pub struct ItemSummary {
    pub id: String,
    pub folder_name: String,
    pub source_type: Option<SourceType>,
    pub source_title: Option<String>,
    pub source_author: Option<String>,
    pub published_at: Option<String>,
    pub has_article: bool,
    pub has_transcript: bool,
    pub has_media: bool,
    pub updated_at: String,
}

#[derive(Debug, Serialize)]
pub struct ItemsResponse {
    pub items: Vec<ItemSummary>,
}

#[derive(Debug, Serialize)]
pub struct SourceInfo {
    #[serde(rename = "type")]
    pub kind: Option<SourceType>,
    pub url: Option<String>,
    pub title: Option<String>,
    pub author: Option<String>,
    pub published_at: Option<String>,
    pub description: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct MediaInfo {
    #[serde(rename = "type")]
    pub kind: MediaKind,
    pub title: Option<String>,
    pub url: String,
    pub mime_type: String,
}

#[derive(Debug, Serialize)]
pub struct MediaItemInfo {
    pub id: String,
    pub role: MediaRole,
    #[serde(rename = "type")]
    pub kind: MediaKind,
    pub title: Option<String>,
    pub description: Option<String>,
    pub url: String,
    pub mime_type: String,
}

#[derive(Debug, Serialize)]
pub struct ArticleInfo {
    pub available: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub markdown: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub version: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct TranscriptInfo {
    pub available: bool,
    pub segment_count: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub version: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct ItemDetail {
    pub id: String,
    pub folder_name: String,
    pub source: SourceInfo,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub media: Option<MediaInfo>,
    pub media_items: Vec<MediaItemInfo>,
    pub article: ArticleInfo,
    pub transcript: TranscriptInfo,
    pub updated_at: String,
}

impl ItemDetail {
    pub fn from_item(item: &ValidItem, article: ArticleInfo, transcript: TranscriptInfo) -> Self {
        let media = item
            .manifest
            .primary_media
            .as_ref()
            .map(|m: &PrimaryMedia| MediaInfo {
                kind: m.kind.clone(),
                title: m.title.clone(),
                url: format!("/api/v1/items/{}/media", item.id),
                mime_type: m.mime_type.clone(),
            });
        let media_items = item
            .manifest
            .media_items
            .iter()
            .filter(|media| media.role != MediaRole::Primary)
            .map(|media: &MediaItem| MediaItemInfo {
                id: media.id.clone(),
                role: media.role.clone(),
                kind: media.kind.clone(),
                title: media.title.clone(),
                description: media.description.clone(),
                url: format!(
                    "/api/v1/items/{}/media/{}",
                    percent_encoding::utf8_percent_encode(
                        &item.id,
                        percent_encoding::NON_ALPHANUMERIC
                    ),
                    percent_encoding::utf8_percent_encode(
                        &media.id,
                        percent_encoding::NON_ALPHANUMERIC
                    )
                ),
                mime_type: media.mime_type.clone(),
            })
            .collect();

        Self {
            id: item.id.clone(),
            folder_name: item.folder_name.clone(),
            source: SourceInfo {
                kind: item.manifest.source.kind.clone(),
                url: item.manifest.source.url.clone(),
                title: item.manifest.source.title.clone(),
                author: item.manifest.source.author.clone(),
                published_at: item.manifest.source.published_at.clone(),
                description: item.manifest.source.description.clone(),
            },
            media,
            media_items,
            article,
            transcript,
            updated_at: item.manifest.updated_at.clone(),
        }
    }
}

#[derive(Debug, Serialize)]
pub struct RescanResponse {
    pub item_count: usize,
    pub invalid_item_count: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct Problem {
    pub folder: String,
    pub reason: String,
}

#[derive(Debug, Serialize)]
pub struct ProblemsResponse {
    pub problems: Vec<Problem>,
}

#[derive(Debug, Serialize)]
pub struct SaveResult {
    pub version: String,
    pub saved_at: String,
}

#[derive(Debug, Serialize)]
pub struct TranscriptResponse {
    pub version: String,
    pub language: String,
    pub segments: Vec<crate::models::transcript::Segment>,
}
