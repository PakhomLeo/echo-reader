use std::net::IpAddr;

use axum::body::Body;
use axum::extract::{Path, State};
use axum::http::{Method, Request, Response, StatusCode, header};
use axum::middleware::Next;
use axum::response::IntoResponse;

use crate::app_state::AppState;

const SESSION_COOKIE: &str = "echo_session";

pub async fn bootstrap_session(
    State(state): State<AppState>,
    Path(candidate): Path<String>,
) -> Response<Body> {
    let Some(expected) = state.config.public_token.as_deref() else {
        return rejected(StatusCode::NOT_FOUND, "session bootstrap is not configured");
    };
    if !constant_time_token_eq(expected, &candidate) {
        return rejected(StatusCode::UNAUTHORIZED, "invalid session token");
    }

    let mut response = axum::response::Redirect::to("/").into_response();
    response.headers_mut().insert(
        header::SET_COOKIE,
        format!("{SESSION_COOKIE}={expected}; HttpOnly; SameSite=Strict; Path=/")
            .parse()
            .expect("ECHO_PUBLIC_TOKEN 已通过 cookie 字符验证"),
    );
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
    response
        .headers_mut()
        .insert(header::REFERRER_POLICY, "no-referrer".parse().unwrap());
    response
}

pub async fn public_session_boundary(
    State(state): State<AppState>,
    request: Request<Body>,
    next: Next,
) -> Response<Body> {
    let path = request.uri().path();
    if state.config.public_token.is_none()
        || path == "/health"
        || path == "/internal/v1"
        || path.starts_with("/internal/v1/")
        || is_bootstrap_path(path)
    {
        return next.run(request).await;
    }

    let expected = state
        .config
        .public_token
        .as_deref()
        .expect("public token presence checked above");
    let authorized = request
        .headers()
        .get_all(header::COOKIE)
        .iter()
        .filter_map(|value| value.to_str().ok())
        .flat_map(|value| value.split(';'))
        .filter_map(|cookie| cookie.trim().split_once('='))
        .any(|(name, value)| {
            name.trim() == SESSION_COOKIE && constant_time_token_eq(expected, value.trim())
        });
    if !authorized {
        return rejected(StatusCode::UNAUTHORIZED, "public session required");
    }
    next.run(request).await
}

fn is_bootstrap_path(path: &str) -> bool {
    path.strip_prefix("/session/")
        .is_some_and(|token| !token.is_empty() && !token.contains('/'))
}

fn constant_time_token_eq(expected: &str, candidate: &str) -> bool {
    let expected_hash = blake3::hash(expected.as_bytes());
    let candidate_hash = blake3::hash(candidate.as_bytes());
    expected_hash
        .as_bytes()
        .iter()
        .zip(candidate_hash.as_bytes())
        .fold(0_u8, |difference, (left, right)| {
            difference | (left ^ right)
        })
        == 0
}

/// Enforce the browser security boundary of the loopback-only application.
///
/// A loopback listener alone is not sufficient: DNS rebinding can send a
/// browser request to 127.0.0.1 with an attacker-controlled Host header, and
/// cross-site forms can issue POST requests without CORS permission.
pub async fn loopback_browser_boundary(request: Request<Body>, next: Next) -> Response<Body> {
    let Some(authority) = request
        .headers()
        .get(header::HOST)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.parse::<axum::http::uri::Authority>().ok())
    else {
        return rejected(StatusCode::MISDIRECTED_REQUEST, "invalid Host header");
    };
    if !is_loopback_host(authority.host()) {
        return rejected(StatusCode::MISDIRECTED_REQUEST, "Host must be loopback");
    }

    let unsafe_method = !matches!(request.method(), &Method::GET | &Method::HEAD);
    if unsafe_method {
        if request
            .headers()
            .get("sec-fetch-site")
            .and_then(|value| value.to_str().ok())
            .is_some_and(|value| value.eq_ignore_ascii_case("cross-site"))
        {
            return rejected(StatusCode::FORBIDDEN, "cross-site request rejected");
        }
        if let Some(origin) = request
            .headers()
            .get(header::ORIGIN)
            .and_then(|value| value.to_str().ok())
            && !origin_matches_authority(origin, &authority)
        {
            return rejected(StatusCode::FORBIDDEN, "Origin does not match Host");
        }
    }
    next.run(request).await
}

fn is_loopback_host(host: &str) -> bool {
    host.eq_ignore_ascii_case("localhost")
        || host
            .trim_matches(['[', ']'])
            .parse::<IpAddr>()
            .is_ok_and(|address| address.is_loopback())
}

fn origin_matches_authority(origin: &str, authority: &axum::http::uri::Authority) -> bool {
    let Ok(url) = reqwest::Url::parse(origin) else {
        return false;
    };
    if url.scheme() != "http"
        || !url.username().is_empty()
        || url.password().is_some()
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return false;
    }
    let Some(host) = url.host_str() else {
        return false;
    };
    is_loopback_host(host)
        && host
            .trim_matches(['[', ']'])
            .eq_ignore_ascii_case(authority.host().trim_matches(['[', ']']))
        && url.port_or_known_default() == Some(authority.port_u16().unwrap_or(80))
}

fn rejected(status: StatusCode, message: &'static str) -> Response<Body> {
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, "text/plain; charset=utf-8")
        .body(Body::from(message))
        .expect("构造安全边界响应失败")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn origin_must_match_loopback_authority() {
        let authority = "127.0.0.1:43123".parse().unwrap();
        assert!(origin_matches_authority(
            "http://127.0.0.1:43123",
            &authority
        ));
        assert!(!origin_matches_authority(
            "http://evil.test:43123",
            &authority
        ));
        assert!(!origin_matches_authority("http://127.0.0.1:9", &authority));
        assert!(!origin_matches_authority(
            "https://127.0.0.1:43123",
            &authority
        ));
    }

    #[test]
    fn accepted_hosts_are_loopback_only() {
        assert!(is_loopback_host("localhost"));
        assert!(is_loopback_host("127.0.0.1"));
        assert!(is_loopback_host("::1"));
        assert!(!is_loopback_host("echo-reader.test"));
        assert!(!is_loopback_host("0.0.0.0"));
    }

    #[test]
    fn session_token_comparison_requires_an_exact_value() {
        assert!(constant_time_token_eq("launch-token", "launch-token"));
        assert!(!constant_time_token_eq("launch-token", "launch-token-x"));
        assert!(!constant_time_token_eq("launch-token", ""));
    }
}
