use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum SourceType {
    Article,
    Video,
    Audio,
    Mixed,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct Source {
    #[serde(rename = "type")]
    pub kind: Option<SourceType>,
    pub url: Option<String>,
    /// 原标题（如英文视频的原始标题），可作副标题展示
    pub title: Option<String>,
    pub author: Option<String>,
    pub published_at: Option<String>,
    pub description: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct ArticleMeta {
    pub path: String,
    pub content_source: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct TranscriptMeta {
    pub path: String,
    pub language: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum MediaKind {
    Video,
    Audio,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum MediaRole {
    Primary,
    Inline,
    Attachment,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MediaItem {
    pub id: String,
    pub role: MediaRole,
    #[serde(rename = "type")]
    pub kind: MediaKind,
    pub original_path: String,
    pub playback_path: String,
    pub mime_type: String,
    pub title: Option<String>,
    pub description: Option<String>,
    pub poster_path: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PrimaryMedia {
    #[serde(rename = "type")]
    pub kind: MediaKind,
    pub original_path: String,
    pub playback_path: String,
    pub mime_type: String,
    pub title: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Manifest {
    pub schema_version: u32,
    pub id: String,
    pub created_at: String,
    pub updated_at: String,
    #[serde(default)]
    pub source: Source,
    #[serde(default)]
    pub article: ArticleMeta,
    #[serde(default)]
    pub transcript: TranscriptMeta,
    pub primary_media: Option<PrimaryMedia>,
    #[serde(default)]
    pub media_items: Vec<MediaItem>,
}

impl Manifest {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != 1 {
            return Err(format!("不支持的 schema_version: {}", self.schema_version));
        }
        if self.id.is_empty() {
            return Err("manifest.id 为空".into());
        }
        if let Some(source) = &self.source.kind {
            match source {
                SourceType::Article | SourceType::Video | SourceType::Audio | SourceType::Mixed => {
                }
            }
        }
        if let Some(media) = &self.primary_media
            && media.playback_path.trim().is_empty()
        {
            return Err("primary_media.playback_path 为空".into());
        }
        let mut media_ids = std::collections::HashSet::new();
        for media in &self.media_items {
            let media_id = media.id.trim();
            if media_id.is_empty() {
                return Err("media_items.id 为空".into());
            }
            if !media_ids.insert(media_id) {
                return Err(format!("media_items.id 重复: {}", media.id));
            }
            for (field, value) in [
                ("original_path", media.original_path.as_str()),
                ("playback_path", media.playback_path.as_str()),
            ] {
                if value.trim().is_empty() {
                    return Err(format!("media_items.{}.{field} 为空", media.id));
                }
            }
            if media
                .poster_path
                .as_deref()
                .is_some_and(|path| path.trim().is_empty())
            {
                return Err(format!("media_items.{}.poster_path 为空", media.id));
            }
            if media.mime_type.parse::<mime::Mime>().is_err() {
                return Err(format!("media_items.{}.mime_type 非法", media.id));
            }
        }
        Ok(())
    }
}

#[derive(Debug)]
pub struct ValidItem {
    pub id: String,
    pub folder_name: String,
    pub folder_path: PathBuf,
    pub manifest: Manifest,
    pub has_article: bool,
    pub has_transcript: bool,
    pub has_media: bool,
}

impl ValidItem {
    pub fn article_path(&self) -> PathBuf {
        self.folder_path.join(&self.manifest.article.path)
    }

    pub fn transcript_path(&self) -> PathBuf {
        self.folder_path.join(&self.manifest.transcript.path)
    }
}
