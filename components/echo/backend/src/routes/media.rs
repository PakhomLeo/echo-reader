use axum::body::Body;
use axum::extract::{Path, State};
use axum::http::{Request, StatusCode, header};
use axum::response::Response;
use tokio::io::{AsyncReadExt, AsyncSeekExt};
use tokio_util::io::ReaderStream;

use crate::app_state::AppState;
use crate::error::ApiError;
use crate::library::paths::resolve_existing_in;
use crate::routes::library_file;
use std::path::Path as FilePath;

#[derive(Debug, Clone, Copy)]
struct ByteRange {
    start: u64,
    end: u64,
}

impl ByteRange {
    fn size(&self) -> u64 {
        self.end - self.start + 1
    }
}

/// 解析单个 Range 头（字节闭区间）。仅支持单一范围，非法返回 Err。
fn parse_range(header_value: &str, total: u64) -> Result<Option<ByteRange>, ()> {
    let value = header_value.trim();
    let Some(rest) = value.strip_prefix("bytes=") else {
        return Err(());
    };
    if rest.contains(',') {
        return Err(());
    }
    let (a, b) = rest.split_once('-').ok_or(())?;

    let range = if a.is_empty() {
        let suffix: u64 = b.parse().map_err(|_| ())?;
        if suffix == 0 {
            return Err(());
        }
        ByteRange {
            start: total.saturating_sub(suffix),
            end: total.saturating_sub(1),
        }
    } else if b.is_empty() {
        let start: u64 = a.parse().map_err(|_| ())?;
        if start >= total {
            return Err(());
        }
        ByteRange {
            start,
            end: total.saturating_sub(1),
        }
    } else {
        let start: u64 = a.parse().map_err(|_| ())?;
        let end: u64 = b.parse().map_err(|_| ())?;
        if start > end || start >= total {
            return Err(());
        }
        ByteRange {
            start,
            end: end.min(total.saturating_sub(1)),
        }
    };
    Ok(Some(range))
}

fn not_satisfiable(total: u64, mime: &mime::Mime) -> Response {
    library_file::response(StatusCode::RANGE_NOT_SATISFIABLE)
        .header(header::CONTENT_RANGE, format!("bytes */{total}"))
        .header(header::CONTENT_TYPE, mime.as_ref())
        .body(Body::empty())
        .expect("构造响应失败")
}

pub async fn get_media(
    State(state): State<AppState>,
    Path(id): Path<String>,
    request: Request<Body>,
) -> Result<Response, ApiError> {
    let index = state.index.read().await;
    let item = index
        .get(&id)
        .ok_or_else(|| ApiError::not_found("该内容不存在或已被移除"))?;
    let media = item
        .manifest
        .primary_media
        .as_ref()
        .ok_or_else(|| ApiError::not_found("该内容没有媒体文件"))?;

    let path = resolve_existing_in(&item.folder_path, &media.playback_path)?;
    let mime_type = media.mime_type.clone();
    drop(index);
    stream_file(&path, &mime_type, range_header(&request)).await
}

pub async fn get_media_item(
    State(state): State<AppState>,
    Path((id, media_id)): Path<(String, String)>,
    request: Request<Body>,
) -> Result<Response, ApiError> {
    let index = state.index.read().await;
    let item = index
        .get(&id)
        .ok_or_else(|| ApiError::not_found("该内容不存在或已被移除"))?;
    let media = item
        .manifest
        .media_items
        .iter()
        .find(|media| media.id == media_id)
        .ok_or_else(|| ApiError::not_found("该媒体不存在或已被移除"))?;
    let path = resolve_existing_in(&item.folder_path, &media.playback_path)?;
    let mime_type = media.mime_type.clone();
    drop(index);
    stream_file(&path, &mime_type, range_header(&request)).await
}

fn range_header(request: &Request<Body>) -> Option<String> {
    request
        .headers()
        .get(header::RANGE)
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned)
}

async fn stream_file(
    path: &FilePath,
    mime_type: &str,
    range_header: Option<String>,
) -> Result<Response, ApiError> {
    let mime: mime::Mime = mime_type
        .parse()
        .map_err(|_| ApiError::internal("manifest 中的 mime_type 无法解析"))?;

    let mut file = tokio::fs::File::open(path).await.map_err(ApiError::from)?;
    let total = file.metadata().await.map_err(ApiError::from)?.len();

    let parsed: Option<Result<Option<ByteRange>, ()>> =
        range_header.as_deref().map(|v| parse_range(v, total));

    if let Some(Err(())) = parsed {
        return Ok(not_satisfiable(total, &mime));
    }

    let requested: Option<ByteRange> = parsed.and_then(|r| r.ok()).flatten();

    if let Some(range) = requested {
        file.seek(std::io::SeekFrom::Start(range.start))
            .await
            .map_err(ApiError::from)?;
        let stream = ReaderStream::new(file.take(range.size()));
        let body = Body::from_stream(stream);
        let response = library_file::response(StatusCode::PARTIAL_CONTENT)
            .header(header::ACCEPT_RANGES, "bytes")
            .header(header::CONTENT_TYPE, mime.as_ref())
            .header(header::CONTENT_LENGTH, range.size())
            .header(
                header::CONTENT_RANGE,
                format!("bytes {}-{}/{}", range.start, range.end, total),
            )
            .body(body)
            .expect("构造响应失败");
        return Ok(response);
    }

    let body = Body::from_stream(ReaderStream::new(file));
    let response = library_file::response(StatusCode::OK)
        .header(header::ACCEPT_RANGES, "bytes")
        .header(header::CONTENT_TYPE, mime.as_ref())
        .header(header::CONTENT_LENGTH, total)
        .body(body)
        .expect("构造响应失败");
    Ok(response)
}
