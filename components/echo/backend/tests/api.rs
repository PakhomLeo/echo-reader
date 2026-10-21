mod common;

use common::*;
use reqwest::header::{HOST, ORIGIN};
use serde_json::json;
use tempfile::tempdir;

#[tokio::test]
async fn loopback_host_and_cross_site_write_boundary_is_enforced() {
    let root = tempdir().unwrap();
    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();

    let rebound = client
        .get(format!("{base}/api/v1/items"))
        .header(HOST, "attacker.test")
        .send()
        .await
        .unwrap();
    assert_eq!(rebound.status(), 421);

    let cross_site = client
        .post(format!("{base}/api/v1/library/rescan"))
        .header(ORIGIN, "http://attacker.test")
        .header("sec-fetch-site", "cross-site")
        .send()
        .await
        .unwrap();
    assert_eq!(cross_site.status(), 403);

    let same_origin = client
        .post(format!("{base}/api/v1/library/rescan"))
        .header(ORIGIN, &base)
        .header("sec-fetch-site", "same-origin")
        .send()
        .await
        .unwrap();
    assert_eq!(same_origin.status(), 200);
    handle.abort();
}

#[tokio::test]
async fn public_session_protects_api_and_spa_but_not_health_or_internal_routes() {
    let root = tempdir().unwrap();
    std::fs::write(root.path().join("index.html"), "<main>private</main>").unwrap();
    std::fs::write(root.path().join("app.js"), "export {};").unwrap();
    let (base, handle) = spawn_server_with_public_session(root.path(), "launch-token").await;
    let client = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .unwrap();

    assert_eq!(
        client
            .get(format!("{base}/api/v1/items"))
            .send()
            .await
            .unwrap()
            .status(),
        401
    );
    assert_eq!(
        client
            .get(format!("{base}/"))
            .send()
            .await
            .unwrap()
            .status(),
        401
    );
    assert_eq!(
        client
            .get(format!("{base}/app.js"))
            .send()
            .await
            .unwrap()
            .status(),
        401
    );
    assert_eq!(
        client
            .get(format!("{base}/session/wrong"))
            .send()
            .await
            .unwrap()
            .status(),
        401
    );
    assert_eq!(
        client
            .get(format!("{base}/health"))
            .send()
            .await
            .unwrap()
            .status(),
        200
    );
    assert_eq!(
        client
            .post(format!("{base}/internal/v1/library/switch"))
            .json(&json!({"path": root.path()}))
            .send()
            .await
            .unwrap()
            .status(),
        401,
        "internal route must reach its own token check"
    );
    let unknown_internal = client
        .get(format!("{base}/internal/v1/not-a-route"))
        .send()
        .await
        .unwrap();
    assert_eq!(unknown_internal.status(), 404);
    assert_ne!(
        unknown_internal.headers().get("content-type"),
        Some(&"text/html".parse().unwrap())
    );

    let bootstrap = client
        .get(format!("{base}/session/launch-token"))
        .send()
        .await
        .unwrap();
    assert_eq!(bootstrap.status(), 303);
    assert_eq!(bootstrap.headers()["location"], "/");
    let set_cookie = bootstrap.headers()["set-cookie"].to_str().unwrap();
    assert!(set_cookie.starts_with("echo_session=launch-token;"));
    assert!(set_cookie.contains("HttpOnly"));
    assert!(set_cookie.contains("SameSite=Strict"));
    assert!(set_cookie.contains("Path=/"));
    let cookie = set_cookie.split(';').next().unwrap();
    assert_eq!(
        client
            .get(format!("{base}/api/v1/items"))
            .header("Cookie", cookie)
            .send()
            .await
            .unwrap()
            .status(),
        200
    );
    handle.abort();
}

#[tokio::test]
async fn public_session_fails_import_proxy_closed_without_internal_token() {
    let root = tempdir().unwrap();
    let (base, handle) =
        spawn_server_with_public_session_and_internal(root.path(), "launch-token", None).await;
    let response = reqwest::Client::new()
        .get(format!("{base}/api/v1/app-config"))
        .header("Cookie", "echo_session=launch-token")
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), 503);
    handle.abort();
}

#[tokio::test]
async fn items_list_sorted_and_excludes_invalid() {
    let root = tempdir().unwrap();
    build_full_item(root.path(), "A", "id-a");
    build_full_item(root.path(), "B", "id-b");
    // 无效文件夹：无 manifest
    std::fs::create_dir_all(root.path().join("Bad")).unwrap();

    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();
    let res: serde_json::Value = client
        .get(format!("{base}/api/v1/items"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let items = res["items"].as_array().unwrap();
    assert_eq!(items.len(), 2);
    assert_eq!(items[0]["source_title"], "测试来源标题");
    assert_eq!(items[0]["source_author"], "测试作者");
    assert_eq!(items[0]["published_at"], "2026-08-05T12:00:00+08:00");
    handle.abort();
}

#[tokio::test]
async fn get_item_full_and_404() {
    let root = tempdir().unwrap();
    let dir = build_full_item(root.path(), "A", "id-a");

    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();

    let res = client
        .get(format!("{base}/api/v1/items/id-a"))
        .send()
        .await
        .unwrap();
    assert_eq!(res.status(), 200);
    let body: serde_json::Value = res.json().await.unwrap();
    assert!(body["article"]["available"].as_bool().unwrap());
    assert!(
        body["article"]["version"]
            .as_str()
            .unwrap()
            .starts_with("blake3:")
    );
    assert_eq!(body["transcript"]["segment_count"], 2);
    assert_eq!(body["media"]["mime_type"], "video/mp4");
    assert_eq!(body["media_items"], json!([]));

    // 缺 article / 缺 transcript 的 item
    std::fs::remove_file(dir.join("article.md")).unwrap();
    let res2 = client
        .get(format!("{base}/api/v1/items/id-a"))
        .send()
        .await
        .unwrap();
    let body2: serde_json::Value = res2.json().await.unwrap();
    assert_eq!(body2["article"]["available"], false);

    // 404
    let res3 = client
        .get(format!("{base}/api/v1/items/nope"))
        .send()
        .await
        .unwrap();
    assert_eq!(res3.status(), 404);
    let err: serde_json::Value = res3.json().await.unwrap();
    assert_eq!(err["code"], "NOT_FOUND");
    handle.abort();
}

#[tokio::test]
async fn article_put_save_conflict_and_updated_at() {
    let root = tempdir().unwrap();
    build_full_item(root.path(), "A", "id-a");

    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();

    let detail: serde_json::Value = client
        .get(format!("{base}/api/v1/items/id-a"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let version = detail["article"]["version"].as_str().unwrap().to_string();
    let updated_before = detail["updated_at"].as_str().unwrap().to_string();

    // 成功保存
    let put = client
        .put(format!("{base}/api/v1/items/id-a/article"))
        .json(&json!({ "markdown": "# 新标题\n", "expected_version": version }))
        .send()
        .await
        .unwrap();
    assert_eq!(put.status(), 200);
    let saved: serde_json::Value = put.json().await.unwrap();
    assert!(saved["version"].as_str().unwrap().starts_with("blake3:"));
    assert!(saved["saved_at"].is_string());

    // 文章保存后 updated_at 不早于修改前（同一秒内可能相等）
    let detail2: serde_json::Value = client
        .get(format!("{base}/api/v1/items/id-a"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert!(detail2["updated_at"].as_str().unwrap() >= updated_before.as_str());

    // 过期版本 → 409
    let put2 = client
        .put(format!("{base}/api/v1/items/id-a/article"))
        .json(&json!({ "markdown": "x", "expected_version": version }))
        .send()
        .await
        .unwrap();
    assert_eq!(put2.status(), 409);
    let err: serde_json::Value = put2.json().await.unwrap();
    assert_eq!(err["code"], "ARTICLE_VERSION_CONFLICT");
    assert!(err["details"]["current_version"].is_string());
    handle.abort();
}

#[tokio::test]
async fn transcript_patch_validation_and_conflict() {
    let root = tempdir().unwrap();
    build_full_item(root.path(), "A", "id-a");

    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();

    let t: serde_json::Value = client
        .get(format!("{base}/api/v1/items/id-a/transcript"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let version = t["version"].as_str().unwrap().to_string();
    assert_eq!(t["segments"].as_array().unwrap().len(), 2);

    // 成功批量修改
    let ok = client
        .patch(format!("{base}/api/v1/items/id-a/transcript"))
        .json(&json!({
            "expected_version": version,
            "changes": [{ "id": "s1", "text": "修改后", "speaker": "新说话人" }]
        }))
        .send()
        .await
        .unwrap();
    assert_eq!(ok.status(), 200);
    let saved: serde_json::Value = ok.json().await.unwrap();
    assert!(saved["version"].as_str().unwrap().starts_with("blake3:"));

    // 携带时间戳字段 → 400
    let ts = client
        .patch(format!("{base}/api/v1/items/id-a/transcript"))
        .json(&json!({ "expected_version": saved["version"], "changes": [{ "id": "s1", "start_ms": 5 }] }))
        .send()
        .await
        .unwrap();
    assert_eq!(ts.status(), 400);

    // 不存在的 id → 400
    let bad = client
        .patch(format!("{base}/api/v1/items/id-a/transcript"))
        .json(&json!({ "expected_version": saved["version"], "changes": [{ "id": "nope", "text": "x" }] }))
        .send()
        .await
        .unwrap();
    assert_eq!(bad.status(), 400);

    // JSON 结构坏 → 422
    let malformed = client
        .patch(format!("{base}/api/v1/items/id-a/transcript"))
        .json(&json!({ "expected_version": saved["version"], "changes": [42] }))
        .send()
        .await
        .unwrap();
    assert_eq!(malformed.status(), 422);

    // 过期版本 → 409
    let stale = client
        .patch(format!("{base}/api/v1/items/id-a/transcript"))
        .json(&json!({ "expected_version": version, "changes": [] }))
        .send()
        .await
        .unwrap();
    assert_eq!(stale.status(), 409);
    handle.abort();
}

#[tokio::test]
async fn image_upload_accepts_four_kinds_and_rejects_bad() {
    let root = tempdir().unwrap();
    build_full_item(root.path(), "A", "id-a");

    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();
    let url = format!("{base}/api/v1/items/id-a/images");

    let cases: Vec<(&str, Vec<u8>)> = vec![
        ("pic.jpg", JPEG_HEADER.to_vec()),
        ("pic.png", PNG_HEADER.to_vec()),
        ("pic.webp", webp_bytes()),
        ("pic.gif", GIF_HEADER.to_vec()),
    ];
    for (name, bytes) in cases {
        let part = reqwest::multipart::Part::bytes(bytes).file_name(name.to_string());
        let form = reqwest::multipart::Form::new().part("file", part);
        let res = client.post(&url).multipart(form).send().await.unwrap();
        assert_eq!(res.status(), 200, "case {name}");
        let body: serde_json::Value = res.json().await.unwrap();
        assert!(
            body["relative_path"]
                .as_str()
                .unwrap()
                .starts_with("images/")
        );
        assert!(
            body["markdown"]
                .as_str()
                .unwrap()
                .starts_with("![图片](images/")
        );
    }

    // SVG → 415
    let svg =
        reqwest::multipart::Part::bytes(b"<svg xmlns='http://www.w3.org/2000/svg'/>".to_vec())
            .file_name("x.svg");
    let res = client
        .post(&url)
        .multipart(reqwest::multipart::Form::new().part("file", svg))
        .send()
        .await
        .unwrap();
    assert_eq!(res.status(), 415);

    // 伪造 MIME（文本伪装 png）→ 415
    let fake = reqwest::multipart::Part::bytes(b"plain text".to_vec()).file_name("fake.png");
    let res = client
        .post(&url)
        .multipart(reqwest::multipart::Form::new().part("file", fake))
        .send()
        .await
        .unwrap();
    assert_eq!(res.status(), 415);

    // 超过 20MB → 413
    let mut big = PNG_HEADER.to_vec();
    big.resize(20 * 1024 * 1024 + 128, 0u8);
    let big_part = reqwest::multipart::Part::bytes(big).file_name("big.png");
    let res = client
        .post(&url)
        .multipart(reqwest::multipart::Form::new().part("file", big_part))
        .send()
        .await
        .unwrap();
    assert_eq!(res.status(), 413);
    handle.abort();
}

#[tokio::test]
async fn delete_image_reference_by_occurrence_and_trash() {
    let root = tempdir().unwrap();
    let dir = build_full_item(root.path(), "A", "id-a");

    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();

    let detail: serde_json::Value = client
        .get(format!("{base}/api/v1/items/id-a"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let version = detail["article"]["version"].as_str().unwrap().to_string();

    // 删除第 0 次出现；文章里还有一次引用 → 文件不移动
    let del = client
        .delete(format!("{base}/api/v1/items/id-a/image-reference"))
        .json(&json!({ "src": "images/0001.webp", "occurrence": 0, "expected_version": version }))
        .send()
        .await
        .unwrap();
    assert_eq!(del.status(), 200);
    let body: serde_json::Value = del.json().await.unwrap();
    assert_eq!(
        body["article"]["markdown"]
            .as_str()
            .unwrap()
            .matches("images/0001.webp")
            .count(),
        1
    );
    assert!(std::fs::read_dir(dir.join("images")).unwrap().count() > 0);

    // 删除最后一次出现 → 文件移入 .trash，且响应契约完整
    let v2 = body["article"]["version"].as_str().unwrap();
    let del2 = client
        .delete(format!("{base}/api/v1/items/id-a/image-reference"))
        .json(&json!({ "src": "images/0001.webp", "occurrence": 0, "expected_version": v2 }))
        .send()
        .await
        .unwrap();
    assert_eq!(del2.status(), 200);
    let body2: serde_json::Value = del2.json().await.unwrap();
    assert!(
        !body2["article"]["markdown"]
            .as_str()
            .unwrap()
            .contains("images/0001.webp")
    );
    assert!(body2["article"]["version"].is_string());
    assert!(body2["saved_at"].is_string());
    let trash_files: Vec<_> = std::fs::read_dir(dir.join(".trash"))
        .unwrap()
        .filter_map(|e| e.ok())
        .collect();
    assert_eq!(trash_files.len(), 1);
    assert!(!dir.join("images/0001.webp").exists());

    // 不存在的 occurrence → 400
    let v3 = body2["article"]["version"].as_str().unwrap();
    let bad = client
        .delete(format!("{base}/api/v1/items/id-a/image-reference"))
        .json(&json!({ "src": "images/0001.webp", "occurrence": 0, "expected_version": v3 }))
        .send()
        .await
        .unwrap();
    assert_eq!(bad.status(), 400);
    handle.abort();
}

#[tokio::test]
async fn media_range_returns_206() {
    let root = tempdir().unwrap();
    build_full_item(root.path(), "A", "id-a");

    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();
    let url = format!("{base}/api/v1/items/id-a/media");

    // 全量
    let full = client.get(&url).send().await.unwrap();
    assert_eq!(full.status(), 200);
    assert_eq!(full.headers()["accept-ranges"], "bytes");
    assert_eq!(full.headers()["content-type"], "video/mp4");
    assert_eq!(full.headers()["content-length"], "4096");
    assert_eq!(full.headers()["x-content-type-options"], "nosniff");
    assert_eq!(
        full.headers()["content-security-policy"],
        "sandbox; default-src 'none'"
    );

    // Range: bytes=0-1023 → 206，长度 1024
    let ranged = client
        .get(&url)
        .header("Range", "bytes=0-1023")
        .send()
        .await
        .unwrap();
    assert_eq!(ranged.status(), 206);
    assert_eq!(ranged.headers()["accept-ranges"], "bytes");
    assert_eq!(ranged.headers()["content-range"], "bytes 0-1023/4096");
    assert_eq!(ranged.headers()["content-length"], "1024");
    let body = ranged.bytes().await.unwrap();
    assert_eq!(body.len(), 1024);

    // 开放范围
    let open = client
        .get(&url)
        .header("Range", "bytes=1024-")
        .send()
        .await
        .unwrap();
    assert_eq!(open.status(), 206);
    assert_eq!(open.headers()["content-range"], "bytes 1024-4095/4096");

    // 非法范围 → 416
    let bad = client
        .get(&url)
        .header("Range", "bytes=99999-")
        .send()
        .await
        .unwrap();
    assert_eq!(bad.status(), 416);
    assert_eq!(bad.headers()["content-range"], "bytes */4096");
    handle.abort();
}

#[tokio::test]
async fn additional_media_is_listed_and_streamed_by_manifest_id() {
    let root = tempdir().unwrap();
    let dir = build_full_item(root.path(), "Mixed", "mixed-id");
    let manifest_path = dir.join("manifest.json");
    let mut manifest: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&manifest_path).unwrap()).unwrap();
    manifest["source"]["type"] = json!("mixed");
    manifest["source"]["description"] = json!("页面简介");
    manifest["media_items"] = json!([
        {
            "id": "primary-copy",
            "role": "primary",
            "type": "video",
            "original_path": "media/primary-original.mp4",
            "playback_path": "media/playback.mp4",
            "mime_type": "video/mp4"
        },
        {
            "id": "inline audio/一",
            "role": "inline",
            "type": "audio",
            "original_path": "media/inline-original.wav",
            "playback_path": "media/inline.mp3",
            "mime_type": "audio/mpeg",
            "title": "内嵌音频",
            "description": "音频说明"
        }
    ]);
    write_json(&manifest_path, &manifest);
    std::fs::write(dir.join("media/primary-original.mp4"), b"primary").unwrap();
    std::fs::write(dir.join("media/inline-original.wav"), b"original").unwrap();
    std::fs::write(dir.join("media/inline.mp3"), vec![7u8; 2048]).unwrap();

    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();
    let detail: serde_json::Value = client
        .get(format!("{base}/api/v1/items/mixed-id"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(detail["source"]["description"], "页面简介");
    let media_items = detail["media_items"].as_array().unwrap();
    assert_eq!(media_items.len(), 1, "primary role must not be duplicated");
    assert_eq!(media_items[0]["id"], "inline audio/一");
    assert_eq!(media_items[0]["role"], "inline");
    assert_eq!(media_items[0]["type"], "audio");
    let media_url = media_items[0]["url"].as_str().unwrap();
    assert!(!media_url.contains("inline.mp3"));

    let ranged = client
        .get(format!("{base}{media_url}"))
        .header("Range", "bytes=100-199")
        .send()
        .await
        .unwrap();
    assert_eq!(ranged.status(), 206);
    assert_eq!(ranged.headers()["content-type"], "audio/mpeg");
    assert_eq!(ranged.headers()["content-range"], "bytes 100-199/2048");
    assert_eq!(ranged.bytes().await.unwrap().len(), 100);

    let missing = client
        .get(format!("{base}/api/v1/items/mixed-id/media/not-a-real-id"))
        .send()
        .await
        .unwrap();
    assert_eq!(missing.status(), 404);
    handle.abort();
}

#[tokio::test]
async fn asset_traversal_rejected() {
    let root = tempdir().unwrap();
    build_full_item(root.path(), "A", "id-a");
    std::fs::write(root.path().join("secret.txt"), "secret").unwrap();

    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();
    let url = format!("{base}/api/v1/items/id-a/assets/");

    // 正常资源
    let ok = client
        .get(format!("{url}images/0001.webp"))
        .send()
        .await
        .unwrap();
    assert_eq!(ok.status(), 200);
    assert_eq!(ok.headers()["x-content-type-options"], "nosniff");
    assert_eq!(
        ok.headers()["content-security-policy"],
        "sandbox; default-src 'none'"
    );

    // ../ 穿越 → 被拒绝（matchit 规范化可能使请求落入 fallback，返回 404）
    let t1 = client
        .get(format!("{url}../secret.txt"))
        .send()
        .await
        .unwrap();
    assert!(
        [403, 404].contains(&t1.status().as_u16()),
        "raw dots got {}",
        t1.status()
    );

    // URL 编码穿越 → 403
    let t2 = client
        .get(format!("{url}..%2F..%2Fsecret.txt"))
        .send()
        .await
        .unwrap();
    assert_eq!(t2.status(), 403);

    // 编码绝对路径 → 403
    let t3 = client
        .get(format!("{url}%2Fetc%2Fpasswd"))
        .send()
        .await
        .unwrap();
    assert_eq!(t3.status(), 403);

    // 不存在文件 → 404
    let nf = client
        .get(format!("{url}missing.png"))
        .send()
        .await
        .unwrap();
    assert_eq!(nf.status(), 404);
    handle.abort();
}

#[tokio::test]
async fn missing_resources_404() {
    let root = tempdir().unwrap();
    let dir = build_full_item(root.path(), "A", "id-a");
    // 只有文章，无转录
    std::fs::remove_file(dir.join("transcript.json")).unwrap();

    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();

    let t = client
        .get(format!("{base}/api/v1/items/id-a/transcript"))
        .send()
        .await
        .unwrap();
    assert_eq!(t.status(), 404);

    // 无媒体的 item
    let dir2 = root.path().join("B");
    std::fs::create_dir_all(&dir2).unwrap();
    common::write_json(
        &dir2.join("manifest.json"),
        &common::manifest_json("id-b", None),
    );
    std::fs::write(dir2.join("article.md"), "x").unwrap();
    let detail: serde_json::Value = client
        .get(format!("{base}/api/v1/items/id-b"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(detail["media"], serde_json::Value::Null);
    let m = client
        .get(format!("{base}/api/v1/items/id-b/media"))
        .send()
        .await
        .unwrap();
    assert_eq!(m.status(), 404);
    handle.abort();
}

#[tokio::test]
async fn library_browse_lists_disks_and_directories() {
    let root = tempdir().unwrap();
    build_full_item(root.path(), "A", "id-a");

    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();

    // 空路径 → 磁盘根列表
    let disks: serde_json::Value = client
        .get(format!("{base}/api/v1/library/browse"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert!(disks["path"].as_str().unwrap().is_empty());
    assert!(!disks["entries"].as_array().unwrap().is_empty());

    // 指定目录 → 子项列表
    let dir: serde_json::Value = client
        .get(format!(
            "{base}/api/v1/library/browse?path={}",
            urlencoding(root.path().to_str().unwrap())
        ))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(
        dir["parent"].as_str().unwrap(),
        root.path().parent().unwrap().to_str().unwrap()
    );
    let names: Vec<&str> = dir["entries"]
        .as_array()
        .unwrap()
        .iter()
        .map(|e| e["name"].as_str().unwrap())
        .collect();
    assert!(names.contains(&"A"));

    // 不存在的目录 → 404
    let missing = client
        .get(format!(
            "{base}/api/v1/library/browse?path={}",
            urlencoding("C:\\definitely-not-exist-12345")
        ))
        .send()
        .await
        .unwrap();
    assert_eq!(missing.status(), 404);
    handle.abort();
}

#[tokio::test]
async fn internal_library_switch_requires_token_and_changes_root() {
    let root_a = tempdir().unwrap();
    build_full_item(root_a.path(), "A", "id-a");
    let root_b = tempdir().unwrap();
    let b_dir = build_full_item(root_b.path(), "B", "id-b");

    let (base, handle) = spawn_server(root_a.path()).await;
    let client = reqwest::Client::new();

    // 初始为库 A
    let items: serde_json::Value = client
        .get(format!("{base}/api/v1/items"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(items["items"].as_array().unwrap().len(), 1);
    assert_eq!(items["items"][0]["id"], "id-a");

    let missing_token = client
        .post(format!("{base}/internal/v1/library/switch"))
        .json(&json!({ "path": root_b.path() }))
        .send()
        .await
        .unwrap();
    assert_eq!(missing_token.status(), 401);
    let wrong_token = client
        .post(format!("{base}/internal/v1/library/switch"))
        .header("X-Echo-Internal-Token", "wrong")
        .json(&json!({ "path": root_b.path() }))
        .send()
        .await
        .unwrap();
    assert_eq!(wrong_token.status(), 401);

    // 带内部令牌切换到库 B
    let switched = client
        .post(format!("{base}/internal/v1/library/switch"))
        .header("X-Echo-Internal-Token", "test-token")
        .json(&json!({ "path": root_b.path() }))
        .send()
        .await
        .unwrap();
    assert_eq!(switched.status(), 200);
    let items: serde_json::Value = client
        .get(format!("{base}/api/v1/items"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(items["items"][0]["id"], "id-b");

    // 单 item 模式：根目录自身含 manifest.json
    let single = client
        .post(format!("{base}/internal/v1/library/switch"))
        .header("X-Echo-Internal-Token", "test-token")
        .json(&json!({ "path": b_dir }))
        .send()
        .await
        .unwrap();
    assert_eq!(single.status(), 200);
    let body: serde_json::Value = single.json().await.unwrap();
    assert_eq!(body["item_count"], 1);
    let items: serde_json::Value = client
        .get(format!("{base}/api/v1/items"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(items["items"][0]["id"], "id-b");
    assert_eq!(items["items"][0]["folder_name"], "B");

    // 无效路径 → 404
    let bad = client
        .post(format!("{base}/internal/v1/library/switch"))
        .header("X-Echo-Internal-Token", "test-token")
        .json(&json!({ "path": "C:\\definitely-not-exist-54321" }))
        .send()
        .await
        .unwrap();
    assert_eq!(bad.status(), 404);
    handle.abort();
}

#[tokio::test]
async fn import_routes_proxy_exact_path_query_body_and_status() {
    use axum::{
        Json, Router,
        extract::Query,
        http::StatusCode,
        routing::{get, post},
    };
    use std::collections::HashMap;

    let upstream =
        Router::new()
            .route(
                "/api/v1/import-jobs",
                get(
                    |headers: axum::http::HeaderMap,
                     Query(query): Query<HashMap<String, String>>| async move {
                        Json(json!({
                            "page": query.get("page"),
                            "source": "upstream",
                            "internal_token": headers
                                .get("X-Echo-Internal-Token")
                                .and_then(|value| value.to_str().ok())
                        }))
                    },
                ),
            )
            .route(
                "/api/v1/import-batches",
                post(|Json(body): Json<serde_json::Value>| async move {
                    (StatusCode::ACCEPTED, Json(body))
                }),
            )
            .route(
                "/api/v1/import-diagnostics",
                get(|| async { vec![b'x'; 8 * 1024 * 1024 + 1] }),
            )
            .route(
                "/api/v1/credential-health",
                get(|Query(query): Query<HashMap<String, String>>| async move {
                    Json(json!({"url": query.get("url"), "platforms": []}))
                }),
            );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let upstream_url = format!("http://{}", listener.local_addr().unwrap());
    let upstream_handle = tokio::spawn(async move {
        axum::serve(listener, upstream).await.unwrap();
    });
    let root = tempdir().unwrap();
    let (base, handle) = common::spawn_server_with_import(root.path(), &upstream_url).await;
    let client = reqwest::Client::new();

    let listed: serde_json::Value = client
        .get(format!("{base}/api/v1/import-jobs?page=3"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(
        listed,
        json!({"page": "3", "source": "upstream", "internal_token": "test-token"})
    );
    let created = client
        .post(format!("{base}/api/v1/import-batches"))
        .json(&json!({"urls": ["https://example.test/"]}))
        .send()
        .await
        .unwrap();
    assert_eq!(created.status(), StatusCode::ACCEPTED);
    assert_eq!(
        created.json::<serde_json::Value>().await.unwrap(),
        json!({"urls": ["https://example.test/"]})
    );
    let health: serde_json::Value = client
        .get(format!(
            "{base}/api/v1/credential-health?url=https%3A%2F%2Fweibo.com%2F1"
        ))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(
        health,
        json!({"url": "https://weibo.com/1", "platforms": []})
    );
    assert_eq!(
        client
            .get(format!("{base}/api/v1/not-an-import-route"))
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::NOT_FOUND
    );
    assert_eq!(
        client
            .get(format!("{base}/api/v1/import-diagnostics"))
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::BAD_GATEWAY
    );

    handle.abort();
    upstream_handle.abort();
}

#[tokio::test]
async fn internal_shutdown_requires_token() {
    let root = tempdir().unwrap();
    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();
    assert_eq!(
        client
            .post(format!("{base}/internal/v1/app/shutdown"))
            .send()
            .await
            .unwrap()
            .status(),
        401
    );
    assert_eq!(
        client
            .post(format!("{base}/internal/v1/app/shutdown"))
            .header("X-Echo-Internal-Token", "wrong")
            .send()
            .await
            .unwrap()
            .status(),
        401
    );
    assert_eq!(
        client
            .post(format!("{base}/internal/v1/app/shutdown"))
            .header("X-Echo-Internal-Token", "test-token")
            .send()
            .await
            .unwrap()
            .status(),
        202
    );
    handle.abort();
}

#[tokio::test]
async fn runtime_web_root_serves_spa_with_cache_policy() {
    let root = tempdir().unwrap();
    std::fs::write(root.path().join("index.html"), "<main>installed</main>").unwrap();
    std::fs::write(root.path().join("app.abc.js"), "export {};").unwrap();
    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();

    let page = client
        .get(format!("{base}/items/deep-link"))
        .send()
        .await
        .unwrap();
    assert_eq!(page.status(), 200);
    assert_eq!(page.headers()["cache-control"], "no-cache");
    assert_eq!(page.text().await.unwrap(), "<main>installed</main>");
    let asset = client
        .get(format!("{base}/app.abc.js"))
        .send()
        .await
        .unwrap();
    assert_eq!(
        asset.headers()["cache-control"],
        "public, max-age=31536000, immutable"
    );
    assert_eq!(
        client
            .get(format!("{base}/api/not-real"))
            .send()
            .await
            .unwrap()
            .status(),
        404
    );
    handle.abort();
}

fn urlencoding(s: &str) -> String {
    s.replace('\\', "%5C").replace(' ', "%20")
}
