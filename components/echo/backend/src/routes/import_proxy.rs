use axum::body::{Body, to_bytes};
use axum::extract::State;
use axum::http::{Request, Response, StatusCode, header};
use std::time::Duration;

use crate::app_state::AppState;

const MAX_PROXY_BODY: usize = 1024 * 1024;
const MAX_PROXY_RESPONSE_BODY: usize = 8 * 1024 * 1024;
const IMPORT_REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

pub async fn forward(State(state): State<AppState>, request: Request<Body>) -> Response<Body> {
    if state.config.public_token.is_some() && state.config.internal_token.is_none() {
        return response(
            StatusCode::SERVICE_UNAVAILABLE,
            "导入服务认证未配置",
            Some("text/plain; charset=utf-8"),
        );
    }
    let (parts, body) = request.into_parts();
    let mut upstream = state.config.import_api_url.clone();
    upstream.set_path(&format!("/api/v1{}", parts.uri.path()));
    upstream.set_query(parts.uri.query());

    let bytes = match to_bytes(body, MAX_PROXY_BODY).await {
        Ok(bytes) => bytes,
        Err(_) => return response(StatusCode::PAYLOAD_TOO_LARGE, "请求体过大", None),
    };
    let mut upstream_request = state
        .import_client
        .request(parts.method, upstream)
        .body(bytes);
    if let Some(content_type) = parts.headers.get(header::CONTENT_TYPE) {
        upstream_request = upstream_request.header(header::CONTENT_TYPE, content_type);
    }
    if let Some(token) = state.config.internal_token.as_deref() {
        upstream_request = upstream_request.header("X-Echo-Internal-Token", token);
    }
    // Library switching includes a complete local scan and must reach a
    // deterministic result. Other control requests get a bounded deadline.
    if parts.uri.path() != "/library/switch" {
        upstream_request = upstream_request.timeout(IMPORT_REQUEST_TIMEOUT);
    }
    let mut upstream_response = match upstream_request.send().await {
        Ok(response) => response,
        Err(_) => {
            return response(
                StatusCode::BAD_GATEWAY,
                "导入服务未连接",
                Some("text/plain; charset=utf-8"),
            );
        }
    };
    let status = upstream_response.status();
    let content_type = upstream_response
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned);
    if upstream_response
        .content_length()
        .is_some_and(|length| length > MAX_PROXY_RESPONSE_BODY as u64)
    {
        return response(
            StatusCode::BAD_GATEWAY,
            "导入服务响应过大",
            Some("text/plain; charset=utf-8"),
        );
    }
    let mut bytes = Vec::with_capacity(
        upstream_response
            .content_length()
            .unwrap_or_default()
            .min(MAX_PROXY_RESPONSE_BODY as u64) as usize,
    );
    loop {
        match upstream_response.chunk().await {
            Ok(Some(chunk)) if bytes.len() + chunk.len() <= MAX_PROXY_RESPONSE_BODY => {
                bytes.extend_from_slice(&chunk);
            }
            Ok(Some(_)) => {
                return response(
                    StatusCode::BAD_GATEWAY,
                    "导入服务响应过大",
                    Some("text/plain; charset=utf-8"),
                );
            }
            Ok(None) => break,
            Err(_) => {
                return response(
                    StatusCode::BAD_GATEWAY,
                    "导入服务响应读取失败",
                    Some("text/plain; charset=utf-8"),
                );
            }
        }
    }
    response(status, bytes, content_type.as_deref())
}

fn response(
    status: StatusCode,
    body: impl Into<Body>,
    content_type: Option<&str>,
) -> Response<Body> {
    let mut builder = Response::builder().status(status);
    if let Some(value) = content_type {
        builder = builder.header(header::CONTENT_TYPE, value);
    }
    builder.body(body.into()).expect("构造代理响应失败")
}
