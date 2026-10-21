use axum::Json;
use axum::extract::{Path, State};
use axum::http::StatusCode;
use serde::Deserialize;
use serde_json::json;

use crate::app_state::AppState;
use crate::error::ApiError;
use crate::models::api::SaveResult;
use crate::storage::atomic_write::atomic_write;
use crate::storage::version::version_of;

use super::transcript::{now_rfc3339, touch_manifest};

#[derive(Debug, Deserialize)]
pub struct ArticlePutRequest {
    markdown: String,
    expected_version: String,
}

pub async fn put_article(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(payload): Json<ArticlePutRequest>,
) -> Result<Json<SaveResult>, ApiError> {
    let index = state.index.read().await;
    let item = index
        .get(&id)
        .ok_or_else(|| ApiError::not_found("该内容不存在或已被移除"))?;

    let path = item.article_path();
    let current =
        std::fs::read(&path).map_err(|_| ApiError::bad_request("文章不存在，无法保存"))?;

    if version_of(&current) != payload.expected_version {
        return Err(ApiError::new(
            StatusCode::CONFLICT,
            "ARTICLE_VERSION_CONFLICT",
            "文章已被其他进程修改。",
        )
        .with_details(json!({
            "current_version": version_of(&current),
        })));
    }

    atomic_write(&path, payload.markdown.as_bytes()).map_err(ApiError::from)?;
    touch_manifest(item)?;

    drop(index);
    state.reindex_search_item(&id).await;

    let version = version_of(payload.markdown.as_bytes());
    Ok(Json(SaveResult {
        version,
        saved_at: now_rfc3339(),
    }))
}
