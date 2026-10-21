use std::path::{Component, Path, PathBuf};

use crate::error::ApiError;
use axum::http::StatusCode;

#[derive(Debug)]
pub enum PathError {
    Rejected(String),
    NotFound,
    Io(std::io::Error),
}

impl From<PathError> for ApiError {
    fn from(e: PathError) -> Self {
        match e {
            PathError::Rejected(msg) => ApiError::new(StatusCode::FORBIDDEN, "PATH_REJECTED", msg),
            PathError::NotFound => ApiError::not_found("资源不存在"),
            PathError::Io(io) => ApiError::internal(format!("文件系统错误: {io}")),
        }
    }
}

fn has_drive_prefix(relative: &str) -> bool {
    let bytes = relative.as_bytes();
    bytes.len() >= 2 && bytes[0].is_ascii_alphabetic() && bytes[1] == b':'
}

fn normalize_relative(relative: &str) -> Result<PathBuf, PathError> {
    // Reject Windows-style separators and drive prefixes on every platform so the
    // API contract does not depend on the host OS (e.g. `C:\\Windows` on Linux).
    if relative.contains('\\') || has_drive_prefix(relative) {
        return Err(PathError::Rejected("不允许根路径或盘符".into()));
    }
    let raw = Path::new(relative);
    if raw.is_absolute() {
        return Err(PathError::Rejected("不允许绝对路径".into()));
    }

    let mut out = PathBuf::new();
    for comp in raw.components() {
        match comp {
            Component::Normal(seg) => out.push(seg),
            Component::CurDir => {}
            Component::ParentDir => return Err(PathError::Rejected("不允许 .. 路径".into())),
            Component::RootDir | Component::Prefix(_) => {
                return Err(PathError::Rejected("不允许根路径或盘符".into()));
            }
        }
    }
    if out.as_os_str().is_empty() {
        return Err(PathError::Rejected("路径为空".into()));
    }
    Ok(out)
}

pub fn resolve_in(root: &Path, relative: &str) -> Result<PathBuf, PathError> {
    let rel = normalize_relative(relative)?;
    let root_can = root
        .canonicalize()
        .map_err(|e| PathError::Io(std::io::Error::new(e.kind(), format!("根目录: {e}"))))?;
    let candidate = root_can.join(&rel);
    let cand_can = candidate.canonicalize().map_err(|_| PathError::NotFound)?;
    if !cand_can.starts_with(&root_can) {
        return Err(PathError::Rejected("路径越出资源目录".into()));
    }
    Ok(cand_can)
}

pub fn resolve_existing_in(root: &Path, relative: &str) -> Result<PathBuf, PathError> {
    resolve_in(root, relative)
}

pub fn resolve_target_in(root: &Path, relative: &str) -> Result<PathBuf, PathError> {
    let rel = normalize_relative(relative)?;
    let root_can = root
        .canonicalize()
        .map_err(|e| PathError::Io(std::io::Error::new(e.kind(), format!("根目录: {e}"))))?;
    Ok(root_can.join(rel))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn rejects_parent_traversal() {
        let root = tempdir().unwrap();
        let err = resolve_in(root.path(), "images/../../secret.txt").unwrap_err();
        assert!(matches!(err, PathError::Rejected(_)));
    }

    #[test]
    fn rejects_absolute_path() {
        let root = tempdir().unwrap();
        let err = resolve_in(root.path(), "/etc/passwd").unwrap_err();
        assert!(matches!(err, PathError::Rejected(_)));
    }

    #[test]
    fn rejects_windows_drive() {
        let root = tempdir().unwrap();
        let err = resolve_in(root.path(), "C:\\Windows\\system32").unwrap_err();
        assert!(matches!(err, PathError::Rejected(_)));
    }

    #[test]
    fn resolves_file_inside_root() {
        let root = tempdir().unwrap();
        let f = root.path().join("a.txt");
        std::fs::write(&f, "x").unwrap();
        let resolved = resolve_in(root.path(), "a.txt").unwrap();
        assert_eq!(resolved, f.canonicalize().unwrap());
    }

    #[test]
    fn missing_file_is_not_found() {
        let root = tempdir().unwrap();
        let err = resolve_in(root.path(), "missing.txt").unwrap_err();
        assert!(matches!(err, PathError::NotFound));
    }
}
