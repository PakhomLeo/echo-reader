use std::path::Path;

use axum::Router;
use echo_backend::app_state::AppState;
use echo_backend::config::Config;

pub async fn spawn_server(root: &Path) -> (String, tokio::task::JoinHandle<()>) {
    spawn_server_with_import(root, "http://127.0.0.1:3100").await
}

pub async fn spawn_server_with_import(
    root: &Path,
    import_api_url: &str,
) -> (String, tokio::task::JoinHandle<()>) {
    let config = Config {
        library_root: root.to_path_buf(),
        web_root: root.to_path_buf(),
        import_api_url: import_api_url.parse().unwrap(),
        internal_token: Some("test-token".to_owned()),
        public_token: None,
        boot_id: Some("test-boot".to_owned()),
        host_pid: None,
    };
    let state = AppState::new(config);
    let app: Router = Router::new()
        .route(
            "/health",
            axum::routing::get(|| async { axum::Json(serde_json::json!({"status": "ok"})) }),
        )
        .route(
            "/session/{token}",
            axum::routing::get(echo_backend::routes::security::bootstrap_session),
        )
        .nest("/api/v1", echo_backend::routes::api())
        .nest("/internal/v1", echo_backend::routes::internal())
        .fallback(echo_backend::routes::serve_frontend)
        .layer(axum::middleware::from_fn(
            echo_backend::routes::security::loopback_browser_boundary,
        ))
        .layer(axum::middleware::from_fn_with_state(
            state.clone(),
            echo_backend::routes::security::public_session_boundary,
        ))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let handle = tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    (format!("http://{addr}"), handle)
}

pub async fn spawn_server_with_public_session(
    root: &Path,
    public_token: &str,
) -> (String, tokio::task::JoinHandle<()>) {
    spawn_server_with_public_session_and_internal(root, public_token, Some("test-token")).await
}

pub async fn spawn_server_with_public_session_and_internal(
    root: &Path,
    public_token: &str,
    internal_token: Option<&str>,
) -> (String, tokio::task::JoinHandle<()>) {
    let config = Config {
        library_root: root.to_path_buf(),
        web_root: root.to_path_buf(),
        import_api_url: "http://127.0.0.1:3100".parse().unwrap(),
        internal_token: internal_token.map(str::to_owned),
        public_token: Some(public_token.to_owned()),
        boot_id: Some("test-boot".to_owned()),
        host_pid: None,
    };
    let state = AppState::new(config);
    let app: Router = Router::new()
        .route(
            "/health",
            axum::routing::get(|| async { axum::Json(serde_json::json!({"status": "ok"})) }),
        )
        .route(
            "/session/{token}",
            axum::routing::get(echo_backend::routes::security::bootstrap_session),
        )
        .nest("/api/v1", echo_backend::routes::api())
        .nest("/internal/v1", echo_backend::routes::internal())
        .fallback(echo_backend::routes::serve_frontend)
        .layer(axum::middleware::from_fn(
            echo_backend::routes::security::loopback_browser_boundary,
        ))
        .layer(axum::middleware::from_fn_with_state(
            state.clone(),
            echo_backend::routes::security::public_session_boundary,
        ))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let handle = tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    (format!("http://{addr}"), handle)
}

pub fn manifest_json(id: &str, media: Option<(&str, &str, &str)>) -> serde_json::Value {
    let primary = match media {
        Some((kind, playback, mime)) => serde_json::json!({
            "type": kind,
            "original_path": "media/original.bin",
            "playback_path": playback,
            "mime_type": mime,
            "title": "测试媒体"
        }),
        None => serde_json::Value::Null,
    };
    serde_json::json!({
        "schema_version": 1,
        "id": id,
        "created_at": "2026-08-06T20:00:00+08:00",
        "updated_at": "2026-08-06T20:00:00+08:00",
        "source": {
            "type": "video",
            "url": "https://example.com/src",
            "title": "测试来源标题",
            "author": "测试作者",
            "published_at": "2026-08-05T12:00:00+08:00"
        },
        "article": { "path": "article.md", "content_source": "content.json" },
        "transcript": { "path": "transcript.json", "language": "zh-CN" },
        "primary_media": primary
    })
}

pub fn write_json(path: &Path, value: &serde_json::Value) {
    std::fs::write(path, serde_json::to_vec_pretty(value).unwrap()).unwrap();
}

/// 构建一个带文章、转录、媒体和图片的完整 item。
pub fn build_full_item(root: &Path, name: &str, id: &str) -> std::path::PathBuf {
    let dir = root.join(name);
    std::fs::create_dir_all(dir.join("media")).unwrap();
    std::fs::create_dir_all(dir.join("images")).unwrap();
    write_json(
        &dir.join("manifest.json"),
        &manifest_json(id, Some(("video", "media/playback.mp4", "video/mp4"))),
    );
    std::fs::write(
        dir.join("article.md"),
        "# 标题\n\n![图A](images/0001.webp)\n\n正文\n\n![图A](images/0001.webp)\n",
    )
    .unwrap();
    write_json(
        &dir.join("transcript.json"),
        &serde_json::json!({
            "schema_version": 1,
            "language": "zh-CN",
            "media_id": "primary",
            "segments": [
                { "id": "s1", "start_ms": 0, "end_ms": 1000, "speaker": "a", "text": "第一段" },
                { "id": "s2", "start_ms": 1000, "end_ms": 2000, "speaker": "b", "text": "第二段" }
            ]
        }),
    );
    std::fs::write(dir.join("media/original.bin"), vec![1u8; 4096]).unwrap();
    std::fs::write(dir.join("media/playback.mp4"), vec![0u8; 4096]).unwrap();
    // 合法 PNG 文件头 + 内容
    let mut png = vec![0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a];
    png.extend_from_slice(&[0x00; 64]);
    std::fs::write(dir.join("images/0001.webp"), png).unwrap();
    dir
}

pub const PNG_HEADER: &[u8] = &[0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a];
pub const JPEG_HEADER: &[u8] = &[0xff, 0xd8, 0xff, 0xe0];
pub const GIF_HEADER: &[u8] = b"GIF89a";
pub fn webp_bytes() -> Vec<u8> {
    let mut b = b"RIFF".to_vec();
    b.extend_from_slice(&[0, 0, 0, 0]);
    b.extend_from_slice(b"WEBP");
    b
}
