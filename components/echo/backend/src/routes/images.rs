use axum::Json;
use axum::extract::{DefaultBodyLimit, Multipart, Path, State};
use axum::http::StatusCode;
use serde::Deserialize;
use serde_json::json;

use crate::app_state::AppState;
use crate::error::ApiError;
use crate::library::paths::{resolve_existing_in, resolve_target_in};
use crate::markdown::image_reference::{references_image, remove_image_occurrence};
use crate::storage::atomic_write::atomic_write;
use crate::storage::version::version_of;

use super::image_upload::validate_upload_bytes;
use super::transcript::{now_rfc3339, touch_manifest};

pub fn image_upload_layer() -> DefaultBodyLimit {
    DefaultBodyLimit::max(21 * 1024 * 1024)
}

pub async fn upload_image(
    State(state): State<AppState>,
    Path(id): Path<String>,
    mut multipart: Multipart,
) -> Result<Json<serde_json::Value>, ApiError> {
    let index = state.index.read().await;
    let item = index
        .get(&id)
        .ok_or_else(|| ApiError::not_found("该内容不存在或已被移除"))?;

    let field = multipart
        .next_field()
        .await
        .map_err(|e| ApiError::bad_request(format!("上传数据不合法: {e}")))?
        .ok_or_else(|| ApiError::bad_request("缺少文件字段 file"))?;
    if field.name() != Some("file") {
        return Err(ApiError::bad_request("缺少文件字段 file"));
    }

    let bytes = field
        .bytes()
        .await
        .map_err(|e| ApiError::bad_request(format!("读取上传文件失败: {e}")))?;
    let kind = validate_upload_bytes(&bytes)?;

    let images_dir = resolve_target_in(&item.folder_path, "images")
        .map_err(|_| ApiError::internal("images 目录路径非法"))?;
    std::fs::create_dir_all(&images_dir).map_err(ApiError::from)?;

    let file_name = format!("{}.{}", uuid::Uuid::new_v4(), kind.extension());
    let relative = format!("images/{file_name}");
    let target = resolve_target_in(&item.folder_path, &relative)
        .map_err(|_| ApiError::internal("图片路径非法"))?;
    std::fs::write(&target, &bytes).map_err(ApiError::from)?;

    Ok(Json(json!({
        "relative_path": relative,
        "markdown": format!("![图片]({relative})"),
    })))
}

#[derive(Debug, Deserialize)]
pub struct ImageReferenceDeleteRequest {
    src: String,
    occurrence: usize,
    expected_version: String,
}

pub async fn delete_image_reference(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(payload): Json<ImageReferenceDeleteRequest>,
) -> Result<Json<serde_json::Value>, ApiError> {
    let index = state.index.read().await;
    let item = index
        .get(&id)
        .ok_or_else(|| ApiError::not_found("该内容不存在或已被移除"))?;

    let article_path = item.article_path();
    let current = std::fs::read(&article_path)
        .map_err(|_| ApiError::bad_request("文章不存在，无法删除图片"))?;
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

    let markdown = String::from_utf8_lossy(&current).into_owned();
    let updated = remove_image_occurrence(&markdown, &payload.src, payload.occurrence)
        .ok_or_else(|| ApiError::bad_request("文章中没有找到对应的图片"))?;

    if updated != markdown {
        atomic_write(&article_path, updated.as_bytes()).map_err(ApiError::from)?;
    }
    touch_manifest(item)?;

    let new_version = version_of(updated.as_bytes());

    // 无剩余引用 → 移入 .trash
    if !references_image(&updated, &payload.src)
        && let Ok(source) = resolve_existing_in(&item.folder_path, &payload.src)
    {
        let file_name = source
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| format!("{}.bin", uuid::Uuid::new_v4()));
        let trash_dir = resolve_target_in(&item.folder_path, ".trash")
            .map_err(|_| ApiError::internal(".trash 路径非法"))?;
        std::fs::create_dir_all(&trash_dir).map_err(ApiError::from)?;
        let mut trash_target = trash_dir.join(&file_name);
        if trash_target.exists() {
            trash_target = trash_dir.join(format!("{}-{}", uuid::Uuid::new_v4(), file_name));
        }
        std::fs::rename(&source, &trash_target).map_err(ApiError::from)?;
    }

    drop(index);
    state.reindex_search_item(&id).await;

    Ok(Json(json!({
        "article": {
            "markdown": updated,
            "version": new_version,
        },
        "saved_at": now_rfc3339(),
    })))
}
