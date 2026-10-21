//! Rebuildable full-text search over the file library (SQLite FTS5).

pub mod extract;
pub mod index;
pub mod text;

pub use index::{
    DocSource, IndexStatus, SearchHit, SearchIndex, SearchResponse, SyncReport, rebuild,
};

use std::path::{Path, PathBuf};

/// Where the index lives. `ECHO_SEARCH_INDEX` wins; otherwise a per-library file in the
/// user cache directory (never inside the library itself, which stays untouched).
pub fn default_index_path(library_root: &Path) -> PathBuf {
    if let Some(path) = std::env::var_os("ECHO_SEARCH_INDEX").filter(|v| !v.is_empty()) {
        return PathBuf::from(path);
    }
    let canonical =
        std::fs::canonicalize(library_root).unwrap_or_else(|_| library_root.to_path_buf());
    let key = blake3::hash(canonical.to_string_lossy().as_bytes()).to_hex();
    cache_dir()
        .join("echo-reader")
        .join("search")
        .join(format!("{}.sqlite", &key[..16]))
}

fn cache_dir() -> PathBuf {
    if let Some(dir) = std::env::var_os("ECHO_CACHE_DIR").filter(|v| !v.is_empty()) {
        return PathBuf::from(dir);
    }
    #[cfg(windows)]
    if let Some(dir) = std::env::var_os("LOCALAPPDATA").filter(|v| !v.is_empty()) {
        return PathBuf::from(dir);
    }
    #[cfg(target_os = "macos")]
    if let Some(home) = std::env::var_os("HOME").filter(|v| !v.is_empty()) {
        return PathBuf::from(home).join("Library").join("Caches");
    }
    if let Some(dir) = std::env::var_os("XDG_CACHE_HOME").filter(|v| !v.is_empty()) {
        return PathBuf::from(dir);
    }
    if let Some(home) = std::env::var_os("HOME").filter(|v| !v.is_empty()) {
        return PathBuf::from(home).join(".cache");
    }
    std::env::temp_dir()
}
