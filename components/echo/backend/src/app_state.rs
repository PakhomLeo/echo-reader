use std::path::PathBuf;
use std::sync::Arc;

use tokio::sync::{Mutex, RwLock};

use crate::config::Config;
use crate::error::ApiError;
use crate::library::index::ItemIndex;
use crate::search::{self, DocSource, IndexStatus, SearchIndex, SearchResponse, SyncReport};

/// The full-text index is a cache: `index` is `None` until first use or after an error.
pub struct SearchSlot {
    pub path: PathBuf,
    pub index: Option<SearchIndex>,
}

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<Config>,
    /// 当前资料库根目录（可通过 switch 接口切换）
    pub library_root: Arc<RwLock<PathBuf>>,
    pub index: Arc<RwLock<ItemIndex>>,
    pub import_client: reqwest::Client,
    pub shutdown: Arc<tokio::sync::Notify>,
    pub search: Arc<std::sync::Mutex<SearchSlot>>,
    scan_lock: Arc<Mutex<()>>,
}

impl AppState {
    pub fn new(config: Config) -> Self {
        let root = config.library_root.clone();
        let index = ItemIndex::scan(&root);
        let search_path = config
            .search_index
            .clone()
            .unwrap_or_else(|| search::default_index_path(&root));
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
            search: Arc::new(std::sync::Mutex::new(SearchSlot {
                path: search_path,
                index: None,
            })),
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
        drop(_scan_guard);
        self.sync_search_logged().await;
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
        if self.config.search_index.is_none() {
            let path = search::default_index_path(&root);
            let mut slot = self.search.lock().unwrap_or_else(|e| e.into_inner());
            if slot.path != path {
                slot.path = path;
                slot.index = None;
            }
        }
        *self.library_root.write().await = root;
        *self.index.write().await = fresh;
        drop(_scan_guard);
        self.sync_search_logged().await;
        Ok((count, invalid))
    }

    async fn search_sources(&self) -> Vec<DocSource> {
        self.index
            .read()
            .await
            .items()
            .map(DocSource::from_item)
            .collect()
    }

    /// Run `f` against the open index on a blocking thread, opening it on first use.
    async fn with_search<T: Send + 'static>(
        &self,
        f: impl FnOnce(&mut SlotAccess<'_>) -> Result<T, String> + Send + 'static,
    ) -> Result<T, ApiError> {
        let slot = self.search.clone();
        tokio::task::spawn_blocking(move || {
            let mut guard = slot.lock().unwrap_or_else(|e| e.into_inner());
            let mut access = SlotAccess { slot: &mut guard };
            let result = f(&mut access);
            if result.is_err() {
                // Drop the connection so the next call reopens (or rebuilds) it.
                guard.index = None;
            }
            result
        })
        .await
        .map_err(|error| ApiError::internal(format!("搜索索引任务失败: {error}")))?
        .map_err(|error| {
            ApiError::new(
                axum::http::StatusCode::SERVICE_UNAVAILABLE,
                "SEARCH_UNAVAILABLE",
                format!("搜索索引不可用: {error}"),
            )
        })
    }

    /// Bring the index in line with the library (cheap when nothing changed).
    pub async fn sync_search(&self) -> Result<SyncReport, ApiError> {
        let sources = self.search_sources().await;
        self.with_search(move |access| access.index()?.sync(&sources).map_err(|e| e.to_string()))
            .await
    }

    pub async fn sync_search_logged(&self) {
        if let Err(error) = self.sync_search().await {
            tracing::warn!("搜索索引同步失败: {}", error.message);
        }
    }

    /// Re-index one item right after it was edited through the API.
    pub async fn reindex_search_item(&self, id: &str) {
        let source = self.index.read().await.get(id).map(DocSource::from_item);
        let Some(source) = source else { return };
        let result = self
            .with_search(move |access| {
                access
                    .index()?
                    .reindex_one(&source)
                    .map_err(|e| e.to_string())
            })
            .await;
        if let Err(error) = result {
            tracing::warn!("搜索索引更新失败: {}", error.message);
        }
    }

    pub async fn search(
        &self,
        query: String,
        question: bool,
        limit: usize,
    ) -> Result<SearchResponse, ApiError> {
        let sources = self.search_sources().await;
        self.with_search(move |access| {
            let index = access.index()?;
            index.sync(&sources).map_err(|e| e.to_string())?;
            index
                .search(&query, question, limit)
                .map_err(|e| e.to_string())
        })
        .await
    }

    pub async fn search_status(&self) -> Result<IndexStatus, ApiError> {
        let sources = self.search_sources().await;
        self.with_search(move |access| access.index()?.status(&sources).map_err(|e| e.to_string()))
            .await
    }

    /// Delete the index file and rebuild it from the library folders.
    pub async fn rebuild_search(&self) -> Result<(SyncReport, IndexStatus), ApiError> {
        let sources = self.search_sources().await;
        self.with_search(move |access| {
            access.slot.index = None;
            let (index, report) =
                search::rebuild(&access.slot.path, &sources).map_err(|e| e.to_string())?;
            let status = index.status(&sources).map_err(|e| e.to_string())?;
            access.slot.index = Some(index);
            Ok((report, status))
        })
        .await
    }
}

pub struct SlotAccess<'a> {
    pub slot: &'a mut SearchSlot,
}

impl SlotAccess<'_> {
    pub fn index(&mut self) -> Result<&mut SearchIndex, String> {
        if self.slot.index.is_none() {
            let opened = match SearchIndex::open(&self.slot.path) {
                Ok(index) => index,
                // A corrupt cache is never fatal: throw it away and start over.
                Err(_) => search::rebuild(&self.slot.path, &[])
                    .map(|(index, _)| index)
                    .map_err(|e| e.to_string())?,
            };
            self.slot.index = Some(opened);
        }
        Ok(self.slot.index.as_mut().expect("index just opened"))
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
            search_index: None,
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
