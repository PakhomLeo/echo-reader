use crate::error::ApiError;

const MAX_IMAGE_BYTES: u64 = 20 * 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ImageKind {
    Jpeg,
    Png,
    Webp,
    Gif,
}

impl ImageKind {
    pub fn extension(self) -> &'static str {
        match self {
            ImageKind::Jpeg => "jpg",
            ImageKind::Png => "png",
            ImageKind::Webp => "webp",
            ImageKind::Gif => "gif",
        }
    }
}

/// 根据文件头识别图片类型。识别失败返回 None（拒绝 SVG、伪造 MIME 与未知格式）。
pub fn detect_image_kind(bytes: &[u8]) -> Option<ImageKind> {
    if bytes.starts_with(&[0xff, 0xd8, 0xff]) {
        return Some(ImageKind::Jpeg);
    }
    if bytes.starts_with(&[0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a]) {
        return Some(ImageKind::Png);
    }
    // RIFF .... WEBP
    if bytes.len() >= 12 && &bytes[0..4] == b"RIFF" && &bytes[8..12] == b"WEBP" {
        return Some(ImageKind::Webp);
    }
    if bytes.starts_with(b"GIF8") {
        return Some(ImageKind::Gif);
    }
    None
}

pub fn validate_upload_bytes(bytes: &[u8]) -> Result<ImageKind, ApiError> {
    if bytes.len() as u64 > MAX_IMAGE_BYTES {
        return Err(ApiError::new(
            axum::http::StatusCode::PAYLOAD_TOO_LARGE,
            "IMAGE_TOO_LARGE",
            format!("图片不能超过 {} MB", MAX_IMAGE_BYTES / 1024 / 1024),
        ));
    }
    match detect_image_kind(bytes) {
        Some(kind) => Ok(kind),
        None => Err(ApiError::new(
            axum::http::StatusCode::UNSUPPORTED_MEDIA_TYPE,
            "IMAGE_TYPE_UNSUPPORTED",
            "仅支持 JPEG、PNG、WebP 和 GIF 图片",
        )),
    }
}
