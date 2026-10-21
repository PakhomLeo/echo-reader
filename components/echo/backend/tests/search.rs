//! Full-text index: the library folders are the source of truth, the SQLite index is a
//! disposable cache. These tests delete it, rebuild it and check that file changes are
//! picked up.

mod common;

use common::{build_full_item, spawn_server, write_json};
use serde_json::{Value, json};

fn write_article(root: &std::path::Path, folder: &str, markdown: &str) {
    std::fs::write(root.join(folder).join("article.md"), markdown).unwrap();
}

fn library() -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    build_full_item(root.path(), "a", "id-a");
    write_article(
        root.path(),
        "a",
        "# 本地优先阅读器\n\n这是一篇关于离线朗读和全文检索的说明。\n\nSQLite FTS5 makes search fast.\n",
    );
    build_full_item(root.path(), "b", "id-b");
    write_article(
        root.path(),
        "b",
        "# 第二篇\n\n讲的是播放器和字幕。Search works offline too.\n",
    );
    write_json(
        &root.path().join("b").join("ocr.json"),
        &json!({
            "schema_version": 1,
            "engine": "test",
            "images": [{ "path": "images/0001.webp", "text": "图片里的独特水印文字" }]
        }),
    );
    root
}

async fn get(client: &reqwest::Client, url: String) -> Value {
    let response = client.get(url).send().await.unwrap();
    assert_eq!(response.status(), 200);
    response.json().await.unwrap()
}

fn hit_ids(result: &Value) -> Vec<(String, String)> {
    result["hits"]
        .as_array()
        .unwrap()
        .iter()
        .map(|h| {
            (
                h["item_id"].as_str().unwrap().to_owned(),
                h["kind"].as_str().unwrap().to_owned(),
            )
        })
        .collect()
}

const QUERIES: &[&str] = &["全文检索", "search", "播放器", "第一段", "水印", "测试作者"];

#[tokio::test]
async fn delete_and_rebuild_gives_identical_results() {
    let root = library();
    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();

    let mut before = Vec::new();
    for q in QUERIES {
        before.push(get(&client, format!("{base}/api/v1/search?q={q}")).await);
    }
    assert!(hit_ids(&before[0]).contains(&("id-a".into(), "article".into())));
    let search_hits = hit_ids(&before[1]);
    assert!(search_hits.contains(&("id-a".into(), "article".into())));
    assert!(search_hits.contains(&("id-b".into(), "article".into())));
    assert_eq!(hit_ids(&before[3])[0], ("id-a".into(), "transcript".into()));
    assert_eq!(hit_ids(&before[4]), vec![("id-b".into(), "ocr".into())]);
    assert!(!hit_ids(&before[5]).is_empty(), "author is indexed");

    let status = get(&client, format!("{base}/api/v1/search/status")).await;
    assert_eq!(status["up_to_date"], true);
    let index_file = std::path::PathBuf::from(status["index_path"].as_str().unwrap());
    assert!(index_file.is_file());
    assert!(
        !index_file.starts_with(root.path().join("a")),
        "index never lives inside an item folder"
    );

    // Throw the whole index away, then rebuild through the API.
    let rebuilt: Value = client
        .post(format!("{base}/api/v1/search/rebuild"))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(rebuilt["rebuilt"]["added"], 2);
    assert_eq!(rebuilt["status"]["up_to_date"], true);

    for (q, expected) in QUERIES.iter().zip(&before) {
        let after = get(&client, format!("{base}/api/v1/search?q={q}")).await;
        assert_eq!(&after, expected, "results differ after rebuild for {q}");
    }
    handle.abort();

    // Deleting the file behind the server's back is also fine: a fresh server rebuilds it.
    std::fs::remove_file(&index_file).unwrap();
    let (base, handle) = spawn_server(root.path()).await;
    for (q, expected) in QUERIES.iter().zip(&before) {
        let after = get(&client, format!("{base}/api/v1/search?q={q}")).await;
        assert_eq!(
            &after, expected,
            "results differ after deleting the index for {q}"
        );
    }
    handle.abort();
}

#[tokio::test]
async fn index_follows_api_edits_external_edits_and_deletions() {
    let root = library();
    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();
    assert!(!hit_ids(&get(&client, format!("{base}/api/v1/search?q=离线朗读")).await).is_empty());

    // Edit through the API → re-indexed immediately.
    let detail = get(&client, format!("{base}/api/v1/items/id-a")).await;
    let version = detail["article"]["version"].as_str().unwrap().to_owned();
    let put = client
        .put(format!("{base}/api/v1/items/id-a/article"))
        .json(
            &json!({ "markdown": "# 新版\n\n全新的段落：向日葵。\n", "expected_version": version }),
        )
        .send()
        .await
        .unwrap();
    assert_eq!(put.status(), 200);
    let status = get(&client, format!("{base}/api/v1/search/status")).await;
    assert_eq!(
        status["up_to_date"], true,
        "API edits keep the index current"
    );
    assert!(hit_ids(&get(&client, format!("{base}/api/v1/search?q=离线朗读")).await).is_empty());
    assert_eq!(
        hit_ids(&get(&client, format!("{base}/api/v1/search?q=向日葵")).await),
        vec![("id-a".into(), "article".into())]
    );

    // Edit on disk behind the app's back → status reports it as stale, search syncs it.
    write_article(root.path(), "b", "# 第二篇\n\n外部编辑加入了蒲公英。\n");
    let status = get(&client, format!("{base}/api/v1/search/status")).await;
    assert_eq!(status["up_to_date"], false);
    assert_eq!(status["stale_items"], json!(["id-b"]));
    assert_eq!(
        hit_ids(&get(&client, format!("{base}/api/v1/search?q=蒲公英")).await),
        vec![("id-b".into(), "article".into())]
    );
    assert_eq!(
        get(&client, format!("{base}/api/v1/search/status")).await["up_to_date"],
        true
    );

    // New item and deleted item → picked up by the library rescan.
    build_full_item(root.path(), "c", "id-c");
    write_article(root.path(), "c", "# 新条目\n\n关于银杏的文章。\n");
    std::fs::remove_dir_all(root.path().join("b")).unwrap();
    let status = get(&client, format!("{base}/api/v1/search/status")).await;
    assert_eq!(
        status["orphan_items"],
        json!([]),
        "not visible before rescan"
    );
    let rescan = client
        .post(format!("{base}/api/v1/library/rescan"))
        .send()
        .await
        .unwrap();
    assert_eq!(rescan.status(), 200);
    let status = get(&client, format!("{base}/api/v1/search/status")).await;
    assert_eq!(status["up_to_date"], true);
    assert_eq!(status["indexed_items"], 2);
    assert_eq!(
        hit_ids(&get(&client, format!("{base}/api/v1/search?q=银杏")).await),
        vec![("id-c".into(), "article".into())]
    );
    assert!(hit_ids(&get(&client, format!("{base}/api/v1/search?q=蒲公英")).await).is_empty());
    handle.abort();
}

#[tokio::test]
async fn search_input_is_safe_and_question_mode_strips_scaffolding() {
    let root = library();
    let (base, handle) = spawn_server(root.path()).await;
    let client = reqwest::Client::new();
    for q in ["\"", "*", "a OR", "NEAR(", "%22%29"] {
        let response = client
            .get(format!("{base}/api/v1/search?q={q}"))
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), 200, "query {q}");
    }
    let empty = get(&client, format!("{base}/api/v1/search?q=")).await;
    assert_eq!(empty["hits"], json!([]));
    let question = get(
        &client,
        format!("{base}/api/v1/search?q=请问播放器是什么？&mode=question"),
    )
    .await;
    assert_eq!(question["effective_query"], "播放器");
    assert_eq!(hit_ids(&question), vec![("id-b".into(), "article".into())]);

    let ocr = get(&client, format!("{base}/api/v1/items/id-b/ocr")).await;
    assert_eq!(ocr["images"][0]["text"], "图片里的独特水印文字");
    let missing = client
        .get(format!("{base}/api/v1/items/id-a/ocr"))
        .send()
        .await
        .unwrap();
    assert_eq!(missing.status(), 404);
    handle.abort();
}
