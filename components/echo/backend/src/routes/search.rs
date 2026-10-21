use axum::Json;
use axum::extract::{Path, Query, State};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::app_state::AppState;
use crate::error::ApiError;
use crate::search::{IndexStatus, SearchResponse};

#[derive(Debug, Deserialize)]
pub struct SearchParams {
    #[serde(default)]
    pub q: String,
    /// `keyword` (default) or `question` (strips question words first).
    #[serde(default)]
    pub mode: Option<String>,
    #[serde(default)]
    pub limit: Option<usize>,
}

/// `GET /api/v1/search?q=...` — the index is synced (cheap stat check) before every query,
/// so edits made outside the app are picked up after the next library rescan.
pub async fn search(
    State(state): State<AppState>,
    Query(params): Query<SearchParams>,
) -> Result<Json<SearchResponse>, ApiError> {
    let question = matches!(params.mode.as_deref(), Some("question"));
    if params.q.chars().count() > 500 {
        return Err(ApiError::bad_request("搜索词过长"));
    }
    let limit = params.limit.unwrap_or(50);
    Ok(Json(state.search(params.q, question, limit).await?))
}

/// `GET /api/v1/search/status` — reports stale, missing and orphaned entries without
/// changing the index.
pub async fn status(State(state): State<AppState>) -> Result<Json<IndexStatus>, ApiError> {
    Ok(Json(state.search_status().await?))
}

/// `POST /api/v1/search/rebuild` — delete the index file and rebuild it from the library.
pub async fn rebuild(State(state): State<AppState>) -> Result<Json<Value>, ApiError> {
    let (report, status) = state.rebuild_search().await?;
    Ok(Json(json!({ "rebuilt": report, "status": status })))
}

/// `GET /api/v1/items/{id}/ocr` — the optional `ocr.json` sidecar (404 when absent).
pub async fn get_item_ocr(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    let index = state.index.read().await;
    let item = index
        .get(&id)
        .ok_or_else(|| ApiError::not_found("该内容不存在或已被移除"))?;
    let path = item.folder_path.join(crate::search::index::OCR_SIDECAR);
    let bytes = match std::fs::read(&path) {
        Ok(bytes) => bytes,
        Err(_) => return Err(ApiError::not_found("该内容还没有图片文字识别结果")),
    };
    let value: Value = serde_json::from_slice(&bytes).map_err(|e| {
        ApiError::new(
            axum::http::StatusCode::UNPROCESSABLE_ENTITY,
            "OCR_JSON_INVALID",
            format!("ocr.json 解析失败: {e}"),
        )
    })?;
    Ok(Json(value))
}
