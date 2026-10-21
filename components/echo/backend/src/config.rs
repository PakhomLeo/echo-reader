use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct Config {
    pub library_root: PathBuf,
    pub web_root: PathBuf,
    pub import_api_url: reqwest::Url,
    pub internal_token: Option<String>,
    pub public_token: Option<String>,
    pub boot_id: Option<String>,
    pub host_pid: Option<u32>,
}

impl Config {
    pub fn load() -> Result<Self, String> {
        let library_root = if let Ok(root) = std::env::var("LIBRARY_ROOT") {
            let path = PathBuf::from(root);
            if !path.is_dir() {
                return Err(format!("LIBRARY_ROOT 不是有效目录: {}", path.display()));
            }
            path
        } else {
            let manifest_dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
            let project_content = manifest_dir
                .ancestors()
                .nth(3)
                .map(|root| root.join("content"));
            if let Some(fallback) = project_content.as_ref().filter(|path| path.is_dir()) {
                fallback.clone()
            } else {
                let standalone = manifest_dir
                    .parent()
                    .expect("backend 目录必须有父目录")
                    .join("library.example");
                if standalone.is_dir() {
                    standalone
                } else {
                    return Err(format!(
                        "LIBRARY_ROOT 未设置，且默认内容目录不存在: {}",
                        project_content
                            .unwrap_or_else(|| PathBuf::from("content"))
                            .display()
                    ));
                }
            }
        };

        let web_root = if let Ok(value) = std::env::var("ECHO_WEB_ROOT") {
            let path = PathBuf::from(value);
            if !path.is_dir() || !path.join("index.html").is_file() {
                return Err(format!("ECHO_WEB_ROOT 缺少 index.html: {}", path.display()));
            }
            path
        } else {
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .expect("backend 目录必须有父目录")
                .join("frontend")
                .join("dist")
        };
        let import_api_url = validate_import_api_url(
            &std::env::var("IMPORT_API_URL").unwrap_or_else(|_| "http://127.0.0.1:3100".to_owned()),
        )?;
        let internal_token = std::env::var("ECHO_INTERNAL_TOKEN")
            .ok()
            .filter(|value| !value.is_empty());
        let boot_id = std::env::var("ECHO_BOOT_ID")
            .ok()
            .filter(|value| !value.is_empty());
        let host_pid = parse_host_pid(std::env::var("ECHO_HOST_PID").ok())?;
        let public_token = validate_public_token(
            std::env::var("ECHO_PUBLIC_TOKEN")
                .ok()
                .filter(|value| !value.is_empty()),
            boot_id.is_some() || host_pid.is_some(),
        )?;
        Ok(Self {
            library_root,
            web_root,
            import_api_url,
            internal_token,
            public_token,
            boot_id,
            host_pid,
        })
    }
}

fn validate_public_token(
    value: Option<String>,
    installed_host: bool,
) -> Result<Option<String>, String> {
    let Some(token) = value else {
        return if installed_host {
            Err("桌面宿主模式必须设置 ECHO_PUBLIC_TOKEN".to_owned())
        } else {
            Ok(None)
        };
    };
    if !token
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b'~'))
    {
        return Err("ECHO_PUBLIC_TOKEN 必须是非空 URL-safe token".to_owned());
    }
    Ok(Some(token))
}

fn parse_host_pid(value: Option<String>) -> Result<Option<u32>, String> {
    value
        .map(|raw| {
            raw.parse::<u32>()
                .ok()
                .filter(|pid| *pid > 0)
                .ok_or_else(|| "ECHO_HOST_PID 必须是正整数".to_owned())
        })
        .transpose()
}

fn validate_import_api_url(value: &str) -> Result<reqwest::Url, String> {
    let url = reqwest::Url::parse(value).map_err(|_| "IMPORT_API_URL 不是有效 URL".to_owned())?;
    let loopback = url.host_str().is_some_and(|host| {
        host.eq_ignore_ascii_case("localhost")
            || host
                .trim_matches(['[', ']'])
                .parse::<std::net::IpAddr>()
                .is_ok_and(|address| address.is_loopback())
    });
    if url.scheme() != "http"
        || !loopback
        || !url.username().is_empty()
        || url.password().is_some()
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("IMPORT_API_URL 必须是无凭据、无路径的 loopback HTTP 地址".to_owned());
    }
    Ok(url)
}

#[cfg(test)]
mod tests {
    use super::{parse_host_pid, validate_import_api_url, validate_public_token};

    #[test]
    fn import_upstream_accepts_only_loopback_http_origins() {
        assert!(validate_import_api_url("http://127.0.0.1:3100").is_ok());
        assert!(validate_import_api_url("http://localhost:3100").is_ok());
        assert!(validate_import_api_url("http://[::1]:3100").is_ok());
        assert!(validate_import_api_url("https://127.0.0.1:3100").is_err());
        assert!(validate_import_api_url("http://example.com:3100").is_err());
        assert!(validate_import_api_url("http://user@127.0.0.1:3100").is_err());
        assert!(validate_import_api_url("http://127.0.0.1:3100/api").is_err());
    }

    #[test]
    fn host_pid_must_be_positive_when_configured() {
        assert_eq!(parse_host_pid(None).unwrap(), None);
        assert_eq!(parse_host_pid(Some("42".to_owned())).unwrap(), Some(42));
        assert!(parse_host_pid(Some("0".to_owned())).is_err());
        assert!(parse_host_pid(Some("not-a-pid".to_owned())).is_err());
    }

    #[test]
    fn installed_host_requires_url_safe_public_token() {
        assert!(validate_public_token(None, true).is_err());
        assert_eq!(validate_public_token(None, false).unwrap(), None);
        assert_eq!(
            validate_public_token(Some("launch-token_123".to_owned()), true).unwrap(),
            Some("launch-token_123".to_owned())
        );
        assert!(validate_public_token(Some("unsafe/token".to_owned()), true).is_err());
    }
}
