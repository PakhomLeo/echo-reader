use axum::http::{StatusCode, header, response};

/// Library files originate from untrusted pages and share the application's origin.
/// These headers keep a directly navigated HTML/SVG attachment from becoming an app page.
pub(crate) fn response(status: StatusCode) -> response::Builder {
    axum::response::Response::builder()
        .status(status)
        .header(header::CACHE_CONTROL, "no-store")
        .header("x-content-type-options", "nosniff")
        .header("content-security-policy", "sandbox; default-src 'none'")
        .header("referrer-policy", "no-referrer")
}
