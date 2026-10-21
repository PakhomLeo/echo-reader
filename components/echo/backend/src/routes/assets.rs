use axum::body::Body;
use axum::extract::{OriginalUri, Path, State};
use axum::http::{Request, StatusCode, header};
use axum::response::Response;
use percent_encoding::percent_decode_str;
use tokio_util::io::ReaderStream;

use crate::app_state::AppState;
use crate::error::ApiError;
use crate::library::paths::resolve_existing_in;
use crate::routes::library_file;

pub async fn get_asset(
    State(state): State<AppState>,
    Path((id, _relative)): Path<(String, String)>,
    OriginalUri(uri): OriginalUri,
    _request: Request<Body>,
) -> Result<Response, ApiError> {
    let index = state.index.read().await;
    let item = index
        .get(&id)
        .ok_or_else(|| ApiError::not_found("该内容不存在或已被移除"))?;

    // 从原始 URI 中提取 /api/v1/items/{id}/assets/ 之后的原始字节：
    // OriginalUri 不做规范化与解码，`../` 和 `%2F` 都以原样到达，路径校验可完全掌控。
    let raw = uri.path();
    let parts: Vec<&str> = raw.split('/').collect();
    // ["", "api", "v1", "items", id, "assets", ...rest]
    if parts.len() < 7
        || parts[1] != "api"
        || parts[2] != "v1"
        || parts[3] != "items"
        || parts[4] != id
        || parts[5] != "assets"
    {
        return Err(ApiError::bad_request("资源路径不合法"));
    }
    let encoded_rest = parts[6..].join("/");

    let relative = percent_decode_str(&encoded_rest)
        .decode_utf8()
        .map_err(|_| ApiError::bad_request("路径编码不合法"))?;

    let path = resolve_existing_in(&item.folder_path, &relative)?;
    let file = tokio::fs::File::open(&path).await.map_err(ApiError::from)?;
    let total = file.metadata().await.map_err(ApiError::from)?.len();
    let mime = mime_guess::from_path(&path).first_or_octet_stream();

    let body = Body::from_stream(ReaderStream::new(file));
    let response = library_file::response(StatusCode::OK)
        .header(header::ACCEPT_RANGES, "bytes")
        .header(header::CONTENT_TYPE, mime.as_ref())
        .header(header::CONTENT_LENGTH, total)
        .body(body)
        .expect("构造响应失败");
    Ok(response)
}
