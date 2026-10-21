use std::path::PathBuf;
use std::sync::Arc;

use tokio::sync::{Mutex, RwLock};

use crate::config::Config;
use crate::error::ApiError;
use crate::library::index::ItemIndex;

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<Config>,
    /// 当前资料库根目录（可通过 switch 接口切换）
    pub library_root: Arc<RwLock<PathBuf>>,
    pub index: Arc<RwLock<ItemIndex>>,
    pub import_client: reqwest::Client,
    pub shutdown: Arc<tokio::sync::Notify>,
    scan_lock: Arc<Mutex<()>>,
}

impl AppState {
    pub fn new(config: Config) -> Self {
        let root = config.library_root.clone();
        let index = ItemIndex::scan(&root);
        Self {
            config: Arc::new(config),
            library_root: Arc::new(RwLock::new(root)),
            index: Arc::new(RwLock::new(index)),
            import_client: reqwest::Client::builder()
                .redirect(reqwest::redirect::Policy::none())
                .no_proxy()
                .build()
                .expect("构造 import HTTP client 失败"),
            shutdown: Arc::new(tokio::sync::Notify::new()),
            scan_lock: Arc::new(Mutex::new(())),
        }
    }

    pub async fn rescan(&self) -> Result<(usize, usize), ApiError> {
        let _scan_guard = self.scan_lock.lock().await;
        let root = self.library_root.read().await.clone();
        let fresh = tokio::task::spawn_blocking(move || ItemIndex::scan(&root))
            .await
            .map_err(|error| ApiError::internal(format!("资料库扫描任务失败: {error}")))?;
        let (count, invalid) = (fresh.items().count(), fresh.problems().len());
        *self.index.write().await = fresh;
        Ok((count, invalid))
    }

    /// 切换到新的资料库根目录并重建索引。
    pub async fn switch_root(&self, root: PathBuf) -> Result<(usize, usize), ApiError> {
        if !root.is_dir() {
            return Err(ApiError::not_found("所选路径不是有效文件夹"));
        }
        let _scan_guard = self.scan_lock.lock().await;
        let scan_root = root.clone();
        let fresh = tokio::task::spawn_blocking(move || ItemIndex::scan(&scan_root))
            .await
            .map_err(|error| ApiError::internal(format!("资料库扫描任务失败: {error}")))?;
        let (count, invalid) = (fresh.items().count(), fresh.problems().len());
        *self.library_root.write().await = root;
        *self.index.write().await = fresh;
        Ok((count, invalid))
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;

    #[tokio::test]
    async fn rescan_and_switch_share_one_scan_lock() {
        let first = tempfile::tempdir().unwrap();
        let second = tempfile::tempdir().unwrap();
        let state = AppState::new(Config {
            library_root: first.path().to_path_buf(),
            web_root: first.path().to_path_buf(),
            import_api_url: "http://127.0.0.1:3100".parse().unwrap(),
            internal_token: Some("test-token".to_owned()),
            public_token: None,
            boot_id: Some("test-boot".to_owned()),
            host_pid: None,
        });

        let guard = state.scan_lock.lock().await;
        let rescan_state = state.clone();
        let rescan = tokio::spawn(async move { rescan_state.rescan().await });
        let switch_state = state.clone();
        let next_root = second.path().to_path_buf();
        let switch = tokio::spawn(async move { switch_state.switch_root(next_root).await });

        tokio::time::sleep(Duration::from_millis(20)).await;
        assert!(!rescan.is_finished());
        assert!(!switch.is_finished());

        drop(guard);
        rescan.await.unwrap().unwrap();
        switch.await.unwrap().unwrap();
        assert_eq!(*state.library_root.read().await, second.path());
    }
}
