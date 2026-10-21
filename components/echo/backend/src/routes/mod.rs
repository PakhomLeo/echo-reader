pub mod app;
pub mod article;
pub mod assets;
pub mod image_upload;
pub mod images;
pub mod import_proxy;
pub mod items;
pub mod library;
mod library_file;
pub mod media;
pub mod security;
pub mod transcript;

use crate::app_state::AppState;
use axum::Json;
use axum::Router;
use axum::routing::{get, post};
use serde_json::json;

pub fn api() -> Router<AppState> {
    Router::new()
        .route("/health", get(|| async { Json(json!({"status": "ok"})) }))
        .route("/items", get(items::list_items))
        .route("/items/{id}", get(items::get_item))
        .route("/items/{id}/media", get(media::get_media))
        .route("/items/{id}/media/{media_id}", get(media::get_media_item))
        .route("/items/{id}/assets/{*relative}", get(assets::get_asset))
        .route("/items/{id}/transcript", get(transcript::get_transcript))
        .route(
            "/items/{id}/transcript",
            axum::routing::patch(transcript::patch_transcript),
        )
        .route(
            "/items/{id}/article",
            axum::routing::put(article::put_article),
        )
        .route(
            "/items/{id}/images",
            post(images::upload_image).layer(images::image_upload_layer()),
        )
        .route(
            "/items/{id}/image-reference",
            axum::routing::delete(images::delete_image_reference),
        )
        .route("/library/rescan", post(library::rescan))
        .route("/library/problems", get(library::problems))
        .route("/library/browse", get(library::browse))
        .route("/app-config", get(import_proxy::forward))
        .route("/app/shutdown", post(import_proxy::forward))
        .route("/library/switch", post(import_proxy::forward))
        .route("/import-capabilities", get(import_proxy::forward))
        .route("/import-batches", post(import_proxy::forward))
        .route("/import-batches/{id}", get(import_proxy::forward))
        .route(
            "/import-batches/{id}/retry-failed",
            post(import_proxy::forward),
        )
        .route("/import-jobs", get(import_proxy::forward))
        .route("/import-jobs/{id}", get(import_proxy::forward))
        .route("/import-jobs/{id}/timeline", get(import_proxy::forward))
        .route("/import-jobs/{id}/cancel", post(import_proxy::forward))
        .route("/import-jobs/{id}/retry", post(import_proxy::forward))
        .route("/import-jobs/{id}/reindex", post(import_proxy::forward))
        .route("/import-diagnostics", get(import_proxy::forward))
        .route("/credential-health", get(import_proxy::forward))
}

pub fn internal() -> Router<AppState> {
    Router::new()
        .route("/library/switch", post(library::internal_switch_root))
        .route("/app/shutdown", post(app::shutdown))
}

pub async fn serve_frontend(
    axum::extract::State(state): axum::extract::State<AppState>,
    request: axum::http::Request<axum::body::Body>,
) -> axum::response::Response {
    let path = request.uri().path();
    // API 路径未匹配时返回 404，避免落入 SPA fallback
    if path == "/api"
        || path.starts_with("/api/")
        || path == "/internal"
        || path.starts_with("/internal/")
    {
        return axum::response::Response::builder()
            .status(axum::http::StatusCode::NOT_FOUND)
            .body(axum::body::Body::empty())
            .expect("构造响应失败");
    }
    let dist = &state.config.web_root;
    let rel = path.trim_start_matches('/');
    let safe = !rel.is_empty()
        && std::path::Path::new(rel)
            .components()
            .all(|c| matches!(c, std::path::Component::Normal(_)));
    let target = if safe {
        let candidate = dist.join(rel);
        if candidate.is_file() {
            candidate
        } else {
            dist.join("index.html")
        }
    } else {
        dist.join("index.html")
    };

    match tokio::fs::read(&target).await {
        Ok(bytes) => {
            let mime = mime_guess::from_path(&target).first_or_octet_stream();
            let is_html = target.file_name().is_some_and(|n| n == "index.html");
            let mut builder = axum::response::Response::builder()
                .status(axum::http::StatusCode::OK)
                .header(axum::http::header::CONTENT_TYPE, mime.as_ref());
            if is_html {
                // HTML 永不缓存：保证新构建立即可见
                builder = builder.header(axum::http::header::CACHE_CONTROL, "no-cache");
            } else {
                // 带 hash 的静态资源可长期缓存
                builder = builder.header(
                    axum::http::header::CACHE_CONTROL,
                    "public, max-age=31536000, immutable",
                );
            }
            builder
                .body(axum::body::Body::from(bytes))
                .expect("构造响应失败")
        }
        Err(_) => axum::response::Response::builder()
            .status(axum::http::StatusCode::NOT_FOUND)
            .body(axum::body::Body::empty())
            .expect("构造响应失败"),
    }
}
