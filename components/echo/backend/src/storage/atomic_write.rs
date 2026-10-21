use std::io::Write;
use std::path::Path;

use tempfile::NamedTempFile;

/// 原子写入：同目录临时文件 → 写入 → flush → sync → rename 替换目标。
/// Windows 上 std::fs::rename 使用 MOVEFILE_REPLACE_EXISTING，可覆盖已存在文件。
pub fn atomic_write(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let dir = path.parent().ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::InvalidInput, "目标路径缺少父目录")
    })?;
    let mut tmp = NamedTempFile::new_in(dir)?;
    tmp.write_all(bytes)?;
    tmp.flush()?;
    tmp.as_file().sync_all()?;
    let tmp_path = tmp.into_temp_path();
    std::fs::rename(&tmp_path, path)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn writes_and_replaces_content() {
        let dir = tempdir().unwrap();
        let target = dir.path().join("a.txt");
        atomic_write(&target, b"first").unwrap();
        atomic_write(&target, b"second").unwrap();
        assert_eq!(std::fs::read(&target).unwrap(), b"second");
    }

    #[test]
    fn no_temp_files_left_behind() {
        let dir = tempdir().unwrap();
        let target = dir.path().join("a.txt");
        atomic_write(&target, b"x").unwrap();
        let leftovers: Vec<_> = std::fs::read_dir(dir.path())
            .unwrap()
            .filter_map(|e| e.ok())
            .filter(|e| e.file_name() != "a.txt")
            .collect();
        assert!(leftovers.is_empty());
    }
}
