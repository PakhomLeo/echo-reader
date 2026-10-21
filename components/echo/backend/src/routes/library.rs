use axum::Json;
use axum::extract::{Query, State};
use axum::http::HeaderMap;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;

use crate::app_state::AppState;
use crate::error::ApiError;
use crate::models::api::{ProblemsResponse, RescanResponse};

pub async fn rescan(State(state): State<AppState>) -> Result<Json<RescanResponse>, ApiError> {
    let (item_count, invalid_item_count) = state.rescan().await?;
    Ok(Json(RescanResponse {
        item_count,
        invalid_item_count,
    }))
}

pub async fn problems(State(state): State<AppState>) -> Json<ProblemsResponse> {
    let index = state.index.read().await;
    Json(ProblemsResponse {
        problems: index.problems().to_vec(),
    })
}

#[derive(Debug, Serialize)]
pub struct BrowseEntry {
    pub name: String,
    pub is_dir: bool,
}

#[derive(Debug, Serialize)]
pub struct BrowseResponse {
    pub path: String,
    pub parent: Option<String>,
    pub entries: Vec<BrowseEntry>,
}

fn disks_response() -> BrowseResponse {
    let mut entries = Vec::new();
    if cfg!(windows) {
        for letter in b'A'..=b'Z' {
            let path = format!("{}:\\", letter as char);
            if std::path::Path::new(&path).is_dir() {
                entries.push(BrowseEntry {
                    name: path.clone(),
                    is_dir: true,
                });
            }
        }
    } else {
        // Unix-like systems have a single filesystem root.
        entries.push(BrowseEntry {
            name: "/".to_string(),
            is_dir: true,
        });
    }
    BrowseResponse {
        path: String::new(),
        parent: None,
        entries,
    }
}

/// 浏览本机文件夹：path 为空时列出磁盘根，否则列出该目录内容。
pub async fn browse(
    Query(params): Query<HashMap<String, String>>,
) -> Result<Json<BrowseResponse>, ApiError> {
    let path = params.get("path").cloned().unwrap_or_default();
    if path.trim().is_empty() {
        return Ok(Json(disks_response()));
    }

    let dir = PathBuf::from(&path);
    if !dir.is_dir() {
        return Err(ApiError::not_found("文件夹不存在或无法访问"));
    }

    let parent = dir
        .parent()
        .map(|p| p.to_string_lossy().into_owned())
        .filter(|p| !p.is_empty());

    let mut entries: Vec<BrowseEntry> = Vec::new();
    if let Ok(read_dir) = std::fs::read_dir(&dir) {
        for entry in read_dir.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            let is_dir = entry.file_type().map(|t| t.is_dir()).unwrap_or(false);
            entries.push(BrowseEntry { name, is_dir });
        }
    }
    entries.sort_by(|a, b| {
        b.is_dir
            .cmp(&a.is_dir)
            .then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
    });

    Ok(Json(BrowseResponse {
        path: dir.to_string_lossy().into_owned(),
        parent,
        entries,
    }))
}

#[derive(Debug, Deserialize)]
pub struct SwitchRequest {
    pub path: String,
}

/// 切换资料库根目录并重建索引。
pub async fn internal_switch_root(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(request): Json<SwitchRequest>,
) -> Result<Json<serde_json::Value>, ApiError> {
    super::app::require_internal_token(&state, &headers)?;
    let (item_count, invalid_item_count) = state.switch_root(PathBuf::from(request.path)).await?;
    let library_root = state
        .library_root
        .read()
        .await
        .to_string_lossy()
        .into_owned();
    Ok(Json(serde_json::json!({
        "library_root": library_root,
        "item_count": item_count,
        "invalid_item_count": invalid_item_count,
    })))
}
