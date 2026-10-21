use axum::Json;
use axum::extract::{Path, State};

use crate::app_state::AppState;
use crate::error::ApiError;
use crate::library::index::sorted_summaries;
use crate::models::api::{ArticleInfo, ItemDetail, ItemSummary, ItemsResponse, TranscriptInfo};
use crate::models::item::ValidItem;

pub async fn list_items(State(state): State<AppState>) -> Json<ItemsResponse> {
    let index = state.index.read().await;
    let items = sorted_summaries(&index)
        .into_iter()
        .map(|i| ItemSummary {
            id: i.id.clone(),
            folder_name: i.folder_name.clone(),
            source_type: i.manifest.source.kind.clone(),
            source_title: i.manifest.source.title.clone(),
            source_author: i.manifest.source.author.clone(),
            published_at: i.manifest.source.published_at.clone(),
            has_article: i.has_article,
            has_transcript: i.has_transcript,
            has_media: i.has_media,
            updated_at: i.manifest.updated_at.clone(),
        })
        .collect();
    Json(ItemsResponse { items })
}

pub async fn get_item(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<ItemDetail>, ApiError> {
    let index = state.index.read().await;
    let item = index
        .get(&id)
        .ok_or_else(|| ApiError::not_found("该内容不存在或已被移除"))?;
    Ok(Json(load_detail(item)?))
}

pub fn load_detail(item: &ValidItem) -> Result<ItemDetail, ApiError> {
    let article = match crate::library::assembler::ensure_article(item)? {
        Some((markdown, version)) => ArticleInfo {
            available: true,
            markdown: Some(markdown),
            version: Some(version),
        },
        None => ArticleInfo {
            available: false,
            markdown: None,
            version: None,
        },
    };

    let transcript = if item.has_transcript {
        match std::fs::read(item.transcript_path()) {
            Ok(bytes) => {
                let count =
                    serde_json::from_slice::<crate::models::transcript::TranscriptJson>(&bytes)
                        .map(|t| t.segments.len() as u64)
                        .unwrap_or(0);
                TranscriptInfo {
                    available: true,
                    segment_count: count,
                    version: Some(crate::storage::version::version_of(&bytes)),
                }
            }
            Err(_) => TranscriptInfo {
                available: false,
                segment_count: 0,
                version: None,
            },
        }
    } else {
        TranscriptInfo {
            available: false,
            segment_count: 0,
            version: None,
        }
    };

    Ok(ItemDetail::from_item(item, article, transcript))
}
