use axum::http::StatusCode;

use crate::error::ApiError;
use crate::markdown::generate::content_to_markdown;
use crate::models::content::ContentJson;
use crate::models::item::ValidItem;
use crate::storage::atomic_write::atomic_write;
use crate::storage::version::version_of;

/// 确保文章可用：article.md 存在则直接读取；否则用 content.json 生成并原子写入。
/// 返回 (markdown, version)；两者都没有时返回 None。
pub fn ensure_article(item: &ValidItem) -> Result<Option<(String, String)>, ApiError> {
    let article_path = item.article_path();
    if let Ok(bytes) = std::fs::read(&article_path) {
        return Ok(Some((
            String::from_utf8_lossy(&bytes).into_owned(),
            version_of(&bytes),
        )));
    }

    let content_path = item.folder_path.join(&item.manifest.article.content_source);
    let bytes = match std::fs::read(&content_path) {
        Ok(b) => b,
        Err(_) => return Ok(None),
    };

    let content: ContentJson = serde_json::from_slice(&bytes).map_err(|e| {
        ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "CONTENT_JSON_INVALID",
            format!("content.json 解析失败: {e}"),
        )
    })?;
    let md = content_to_markdown(&content)
        .map_err(|e| ApiError::new(StatusCode::UNPROCESSABLE_ENTITY, "CONTENT_JSON_INVALID", e))?;

    atomic_write(&article_path, md.as_bytes()).map_err(ApiError::from)?;
    let version = version_of(md.as_bytes());
    Ok(Some((md, version)))
}
