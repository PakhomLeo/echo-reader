use axum::Router;
use std::net::ToSocketAddrs;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "echo_backend=info,tower_http=info".into()),
        )
        .init();

    let config = match echo_backend::config::Config::load() {
        Ok(c) => c,
        Err(e) => {
            tracing::error!("配置错误: {e}");
            std::process::exit(1);
        }
    };
    tracing::info!("资料库根目录: {}", config.library_root.display());

    let state = echo_backend::app_state::AppState::new(config);

    // Maintenance commands: `echo-backend --rebuild-search-index` deletes the full-text
    // index and rebuilds it from the library; `--search-index-status` reports staleness.
    let args: Vec<String> = std::env::args().skip(1).collect();
    if let Some(command) = args.first().map(String::as_str) {
        let output = match command {
            "--rebuild-search-index" => state
                .rebuild_search()
                .await
                .map(|(report, status)| serde_json::json!({ "rebuilt": report, "status": status })),
            "--search-index-status" => state
                .search_status()
                .await
                .map(|status| serde_json::json!(status)),
            other => {
                eprintln!("未知参数 {other}。可用: --rebuild-search-index, --search-index-status");
                std::process::exit(2);
            }
        };
        match output {
            Ok(value) => {
                println!(
                    "{}",
                    serde_json::to_string_pretty(&value).unwrap_or_default()
                );
                return;
            }
            Err(error) => {
                eprintln!("{}", error.message);
                std::process::exit(1);
            }
        }
    }
    {
        let state = state.clone();
        tokio::spawn(async move { state.sync_search_logged().await });
    }
    let shutdown = state.shutdown.clone();
    let boot_id = state.config.boot_id.clone();
    let parent_watch = state
        .config
        .host_pid
        .map(|pid| tokio::spawn(watch_host_process(pid, shutdown.clone())));

    let app: Router = Router::new()
        .route("/health", axum::routing::get(health))
        .route(
            "/session/{token}",
            axum::routing::get(echo_backend::routes::security::bootstrap_session),
        )
        .nest("/api/v1", echo_backend::routes::api())
        .nest("/internal/v1", echo_backend::routes::internal())
        .fallback(echo_backend::routes::serve_frontend)
        .layer(axum::middleware::from_fn(
            echo_backend::routes::security::loopback_browser_boundary,
        ))
        .layer(axum::middleware::from_fn_with_state(
            state.clone(),
            echo_backend::routes::security::public_session_boundary,
        ))
        .with_state(state);

    let bind_address = std::env::var("ECHO_BIND").unwrap_or_else(|_| "127.0.0.1:3000".to_owned());
    let listener = bind_listener(&bind_address)
        .unwrap_or_else(|error| panic!("绑定 {bind_address} 失败: {error}"));
    let url = format!(
        "http://{}",
        listener.local_addr().expect("读取监听地址失败")
    );
    if let Ok(path) = std::env::var("ECHO_READY_FILE") {
        let body = serde_json::to_vec(&serde_json::json!({"url": url, "boot_id": boot_id}))
            .expect("序列化 ready file 失败");
        echo_backend::storage::atomic_write::atomic_write(std::path::Path::new(&path), &body)
            .unwrap_or_else(|error| panic!("写入 ECHO_READY_FILE 失败: {error}"));
    }
    tracing::info!("服务已启动: {url}");
    let graceful_shutdown = shutdown.clone();
    let result = axum::serve(listener, app)
        .with_graceful_shutdown(async move { graceful_shutdown.notified().await })
        .await;
    if let Some(task) = parent_watch {
        task.abort();
        let _ = task.await;
    }
    result.expect("服务异常退出");
}

fn bind_listener(bind_address: &str) -> std::io::Result<tokio::net::TcpListener> {
    let address = bind_address
        .to_socket_addrs()?
        .next()
        .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::InvalidInput, "监听地址为空"))?;
    // The API has no network authentication in development mode and is designed for a
    // single local user: refuse to listen on anything other than a loopback address.
    if !address.ip().is_loopback() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "ECHO_BIND 只能是 loopback 地址（127.0.0.1 / ::1）",
        ));
    }
    let socket = socket2::Socket::new(
        socket2::Domain::for_address(address),
        socket2::Type::STREAM,
        Some(socket2::Protocol::TCP),
    )?;
    #[cfg(windows)]
    set_exclusive_addr_use(&socket)?;
    socket.bind(&address.into())?;
    socket.listen(128)?;
    socket.set_nonblocking(true)?;
    tokio::net::TcpListener::from_std(socket.into())
}

#[cfg(windows)]
fn set_exclusive_addr_use(socket: &socket2::Socket) -> std::io::Result<()> {
    use std::os::windows::io::AsRawSocket;

    use windows_sys::Win32::Networking::WinSock::{
        SO_EXCLUSIVEADDRUSE, SOCKET_ERROR, SOL_SOCKET, setsockopt,
    };

    let enabled = 1_i32;
    let result = unsafe {
        setsockopt(
            socket.as_raw_socket() as usize,
            SOL_SOCKET,
            SO_EXCLUSIVEADDRUSE,
            (&enabled as *const i32).cast(),
            std::mem::size_of_val(&enabled) as i32,
        )
    };
    if result == SOCKET_ERROR {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(windows)]
async fn watch_host_process(pid: u32, shutdown: std::sync::Arc<tokio::sync::Notify>) {
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use std::time::Duration;

    use windows_sys::Win32::Foundation::{WAIT_OBJECT_0, WAIT_TIMEOUT};
    use windows_sys::Win32::System::Threading::{
        OpenProcess, PROCESS_SYNCHRONIZE, WaitForSingleObject,
    };

    let raw_handle = unsafe { OpenProcess(PROCESS_SYNCHRONIZE, 0, pid) };
    if raw_handle.is_null() {
        tracing::error!("无法监控宿主进程 {pid}，停止 Reader 以避免孤儿实例");
        shutdown.notify_one();
        return;
    }
    let handle = unsafe { OwnedHandle::from_raw_handle(raw_handle) };
    loop {
        match unsafe { WaitForSingleObject(handle.as_raw_handle(), 0) } {
            WAIT_OBJECT_0 => {
                tracing::warn!("宿主进程 {pid} 已退出，停止 Reader");
                shutdown.notify_one();
                tokio::time::sleep(Duration::from_secs(10)).await;
                tracing::error!("Reader 未在宿主退出后及时停止，强制终止孤儿进程");
                std::process::exit(70);
            }
            WAIT_TIMEOUT => tokio::time::sleep(Duration::from_millis(250)).await,
            status => {
                tracing::error!("监控宿主进程 {pid} 失败，状态 {status}；停止 Reader");
                shutdown.notify_one();
                tokio::time::sleep(Duration::from_secs(10)).await;
                tracing::error!("Reader 未在宿主监控失败后及时停止，强制终止");
                std::process::exit(70);
            }
        }
    }
}

#[cfg(not(windows))]
async fn watch_host_process(_pid: u32, _shutdown: std::sync::Arc<tokio::sync::Notify>) {
    std::future::pending::<()>().await;
}

async fn health(
    axum::extract::State(state): axum::extract::State<echo_backend::app_state::AppState>,
) -> axum::Json<serde_json::Value> {
    axum::Json(serde_json::json!({
        "status": "ok",
        "boot_id": state.config.boot_id.as_deref(),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn listener_binds_requested_loopback_address() {
        let listener = bind_listener("127.0.0.1:0").expect("bind loopback listener");
        let address = listener.local_addr().expect("read listener address");

        assert_eq!(
            address.ip(),
            "127.0.0.1".parse::<std::net::IpAddr>().unwrap()
        );
        assert_ne!(address.port(), 0);
    }

    #[tokio::test]
    async fn listener_rejects_non_loopback_addresses() {
        let error = bind_listener("0.0.0.0:0").expect_err("wildcard bind must be rejected");
        assert_eq!(error.kind(), std::io::ErrorKind::InvalidInput);
    }

    #[cfg(windows)]
    #[tokio::test]
    async fn listener_rejects_reuse_address_takeover_on_windows() {
        let listener = bind_listener("127.0.0.1:0").expect("bind exclusive listener");
        let address: std::net::SocketAddr = listener.local_addr().expect("read listener address");
        let competing = socket2::Socket::new(
            socket2::Domain::for_address(address),
            socket2::Type::STREAM,
            Some(socket2::Protocol::TCP),
        )
        .expect("create competing socket");
        competing
            .set_reuse_address(true)
            .expect("enable address reuse");

        assert!(competing.bind(&address.into()).is_err());
    }
}
