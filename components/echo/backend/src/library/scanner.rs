use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};

use crate::library::manifest::parse_manifest;
use crate::models::api::Problem;
use crate::models::item::{Manifest, ValidItem};

pub struct ScanResult {
    pub items: Vec<ValidItem>,
    pub problems: Vec<Problem>,
}

fn read_manifest(folder: &Path) -> Result<Manifest, String> {
    let manifest_path = folder.join("manifest.json");
    let bytes = fs::read(&manifest_path).map_err(|e| format!("无法读取 manifest.json: {e}"))?;
    parse_manifest(&bytes)
}

fn check_relative_safe(folder: &Path, relative: &str, what: &str) -> Result<(), String> {
    crate::library::paths::resolve_target_in(folder, relative)
        .map_err(|_| format!("{what} 路径非法或越出文件夹: {relative}"))?;
    Ok(())
}

fn check_media_file(folder: &Path, relative: &str, what: &str) -> Result<(), String> {
    let resolved = crate::library::paths::resolve_existing_in(folder, relative)
        .map_err(|_| format!("{what} 路径非法、越出文件夹或文件不存在: {relative}"))?;
    if !resolved.is_file() {
        return Err(format!("{what} 不是文件: {relative}"));
    }
    Ok(())
}

/// 扫描一个文件夹作为 item。folder_name 用文件夹名。
fn scan_folder(
    folder: &Path,
    folder_name: &str,
    seen_ids: &mut HashSet<String>,
    items: &mut Vec<ValidItem>,
    problems: &mut Vec<Problem>,
) {
    let manifest = match read_manifest(folder) {
        Ok(m) => m,
        Err(reason) => {
            problems.push(Problem {
                folder: folder_name.to_string(),
                reason,
            });
            return;
        }
    };

    if seen_ids.contains(&manifest.id) {
        problems.push(Problem {
            folder: folder_name.to_string(),
            reason: format!("重复的 item id: {}", manifest.id),
        });
        return;
    }
    seen_ids.insert(manifest.id.clone());

    let article_path = folder.join(&manifest.article.path);
    let transcript_path = folder.join(&manifest.transcript.path);
    let mut validation_error = None;

    if let Err(e) = check_relative_safe(folder, &manifest.article.path, "article.path") {
        validation_error = Some(e);
    }
    if validation_error.is_none()
        && let Err(e) = check_relative_safe(folder, &manifest.transcript.path, "transcript.path")
    {
        validation_error = Some(e);
    }
    if validation_error.is_none()
        && let Some(media) = &manifest.primary_media
    {
        for (field, path) in [
            ("original_path", media.original_path.as_str()),
            ("playback_path", media.playback_path.as_str()),
        ] {
            let label = format!("primary_media.{field}");
            if let Err(e) = check_media_file(folder, path, &label) {
                validation_error = Some(e);
                break;
            }
        }
    }
    if validation_error.is_none() {
        'media: for media in &manifest.media_items {
            for (field, path) in [
                ("original_path", Some(media.original_path.as_str())),
                ("playback_path", Some(media.playback_path.as_str())),
                ("poster_path", media.poster_path.as_deref()),
            ] {
                let Some(path) = path else { continue };
                let label = format!("media_items.{}.{field}", media.id);
                if let Err(e) = check_media_file(folder, path, &label) {
                    validation_error = Some(e);
                    break 'media;
                }
            }
        }
    }
    if let Some(reason) = validation_error {
        problems.push(Problem {
            folder: folder_name.to_string(),
            reason,
        });
        return;
    }

    let has_article = article_path.is_file();
    let has_transcript = transcript_path.is_file();
    let has_media = manifest
        .primary_media
        .as_ref()
        .map(|m| folder.join(&m.playback_path).is_file())
        .unwrap_or(false)
        || !manifest.media_items.is_empty();

    items.push(ValidItem {
        id: manifest.id.clone(),
        folder_name: folder_name.to_string(),
        folder_path: folder.to_path_buf(),
        manifest,
        has_article,
        has_transcript,
        has_media,
    });
}

pub fn scan_library(root: &Path) -> ScanResult {
    let mut items = Vec::new();
    let mut problems = Vec::new();
    let mut seen_ids: HashSet<String> = HashSet::new();

    let entries = match fs::read_dir(root) {
        Ok(e) => e,
        Err(e) => {
            problems.push(Problem {
                folder: root.display().to_string(),
                reason: format!("无法读取资料库根目录: {e}"),
            });
            return ScanResult { items, problems };
        }
    };

    let mut folders: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let is_internal = path
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name.starts_with('.'));
        if path.is_dir() && !is_internal {
            folders.push(path);
        }
    }
    folders.sort_by(|a, b| a.file_name().cmp(&b.file_name()));

    for folder in folders {
        let folder_name = folder
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned();
        scan_folder(
            &folder,
            &folder_name,
            &mut seen_ids,
            &mut items,
            &mut problems,
        );
    }

    ScanResult { items, problems }
}

/// 扫描资料库根目录；若根目录自身含 manifest.json，则把根目录作为单个 item。
pub fn scan_library_or_single(root: &Path) -> ScanResult {
    if root.join("manifest.json").is_file() {
        let folder_name = root
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| root.display().to_string());
        let mut items = Vec::new();
        let mut problems = Vec::new();
        let mut seen_ids: HashSet<String> = HashSet::new();
        scan_folder(root, &folder_name, &mut seen_ids, &mut items, &mut problems);
        ScanResult { items, problems }
    } else {
        scan_library(root)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn write_manifest(dir: &std::path::Path, id: &str) {
        let manifest = serde_json::json!({
            "schema_version": 1,
            "id": id,
            "created_at": "2026-08-06T20:00:00+08:00",
            "updated_at": "2026-08-06T20:00:00+08:00",
            "source": { "type": "article" },
            "article": { "path": "article.md", "content_source": "content.json" },
            "transcript": { "path": "transcript.json", "language": "zh-CN" },
            "primary_media": null
        });
        std::fs::write(
            dir.join("manifest.json"),
            serde_json::to_vec_pretty(&manifest).unwrap(),
        )
        .unwrap();
    }

    #[test]
    fn scans_valid_folders() {
        let root = tempdir().unwrap();
        let a = root.path().join("A");
        std::fs::create_dir_all(&a).unwrap();
        write_manifest(&a, "id-a");
        let r = scan_library(root.path());
        assert_eq!(r.items.len(), 1);
        assert!(r.problems.is_empty());
        assert_eq!(r.items[0].folder_name, "A");
    }

    #[test]
    fn duplicate_id_reported_as_problem() {
        let root = tempdir().unwrap();
        for name in ["X", "Y"] {
            let d = root.path().join(name);
            std::fs::create_dir_all(&d).unwrap();
            write_manifest(&d, "same-id");
        }
        let r = scan_library(root.path());
        assert_eq!(r.items.len(), 1);
        assert_eq!(r.problems.len(), 1);
        assert!(r.problems[0].reason.contains("重复"));
    }

    #[test]
    fn folder_without_manifest_is_problem() {
        let root = tempdir().unwrap();
        std::fs::create_dir_all(root.path().join("Empty")).unwrap();
        let r = scan_library(root.path());
        assert!(r.items.is_empty());
        assert_eq!(r.problems.len(), 1);
    }

    #[test]
    fn internal_staging_and_backup_folders_are_not_scanned() {
        let root = tempdir().unwrap();
        for name in [".staging-task", ".previous-task"] {
            let dir = root.path().join(name);
            std::fs::create_dir_all(&dir).unwrap();
            write_manifest(&dir, "same-internal-id");
        }

        let r = scan_library(root.path());
        assert!(r.items.is_empty());
        assert!(r.problems.is_empty());
    }

    #[test]
    fn escaped_media_path_is_problem() {
        let root = tempdir().unwrap();
        let d = root.path().join("Esc");
        std::fs::create_dir_all(&d).unwrap();
        let v = serde_json::json!({
            "schema_version": 1,
            "id": "esc",
            "created_at": "2026-08-06T20:00:00+08:00",
            "updated_at": "2026-08-06T20:00:00+08:00",
            "source": { "type": "video" },
            "article": { "path": "article.md", "content_source": "content.json" },
            "transcript": { "path": "transcript.json", "language": "zh-CN" },
            "primary_media": {
                "type": "video",
                "original_path": "media/original.mkv",
                "playback_path": "../../outside.mp4",
                "mime_type": "video/mp4",
                "title": "t"
            }
        });
        std::fs::create_dir_all(d.join("media")).unwrap();
        std::fs::write(d.join("media/original.mkv"), b"original").unwrap();
        std::fs::write(d.join("manifest.json"), serde_json::to_vec(&v).unwrap()).unwrap();
        let r = scan_library(root.path());
        assert!(r.items.is_empty());
        assert_eq!(r.problems.len(), 1);
        assert!(r.problems[0].reason.contains("越出"));
    }

    #[test]
    fn primary_media_requires_existing_original_and_playback_files() {
        let root = tempdir().unwrap();
        let d = root.path().join("Primary");
        std::fs::create_dir_all(d.join("media")).unwrap();
        write_manifest(&d, "primary");
        let mut manifest: serde_json::Value =
            serde_json::from_slice(&std::fs::read(d.join("manifest.json")).unwrap()).unwrap();
        manifest["primary_media"] = serde_json::json!({
            "type": "audio",
            "original_path": "media/original.m4a",
            "playback_path": "media/playback.m4a",
            "mime_type": "audio/mp4"
        });
        std::fs::write(
            d.join("manifest.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();

        let missing = scan_library(root.path());
        assert!(missing.items.is_empty());
        assert!(missing.problems[0].reason.contains("original_path"));

        std::fs::write(d.join("media/original.m4a"), b"original").unwrap();
        std::fs::write(d.join("media/playback.m4a"), b"playback").unwrap();
        let valid = scan_library(root.path());
        assert!(valid.problems.is_empty());
        assert_eq!(valid.items.len(), 1);
    }

    #[test]
    fn media_items_require_safe_existing_files() {
        let root = tempdir().unwrap();
        let d = root.path().join("Mixed");
        std::fs::create_dir_all(d.join("media")).unwrap();
        write_manifest(&d, "mixed");
        let mut manifest: serde_json::Value =
            serde_json::from_slice(&std::fs::read(d.join("manifest.json")).unwrap()).unwrap();
        manifest["media_items"] = serde_json::json!([{
            "id": "inline-1",
            "role": "inline",
            "type": "video",
            "original_path": "media/original.mp4",
            "playback_path": "media/missing.mp4",
            "mime_type": "video/mp4",
            "poster_path": null
        }]);
        std::fs::write(
            d.join("manifest.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();
        std::fs::write(d.join("media/original.mp4"), b"original").unwrap();

        let missing = scan_library(root.path());
        assert!(missing.items.is_empty());
        assert!(missing.problems[0].reason.contains("文件不存在"));

        manifest["media_items"][0]["playback_path"] = serde_json::json!("../outside.mp4");
        std::fs::write(
            d.join("manifest.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();
        let escaped = scan_library(root.path());
        assert!(escaped.items.is_empty());
        assert!(escaped.problems[0].reason.contains("越出"));

        manifest["media_items"][0]["playback_path"] = serde_json::json!("media/playback.mp4");
        manifest["media_items"][0]["poster_path"] = serde_json::json!("media/poster.jpg");
        std::fs::write(
            d.join("manifest.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();
        std::fs::write(d.join("media/playback.mp4"), b"playback").unwrap();
        std::fs::write(d.join("media/poster.jpg"), b"poster").unwrap();
        let valid = scan_library(root.path());
        assert!(valid.problems.is_empty());
        assert_eq!(valid.items.len(), 1);
        assert!(valid.items[0].has_media);
    }
}
