use crate::models::item::Manifest;

pub fn parse_manifest(bytes: &[u8]) -> Result<Manifest, String> {
    let manifest: Manifest =
        serde_json::from_slice(bytes).map_err(|e| format!("manifest.json 解析失败: {e}"))?;
    manifest.validate()?;
    Ok(manifest)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::item::Manifest;

    fn valid_manifest_json() -> serde_json::Value {
        serde_json::json!({
            "schema_version": 1,
            "id": "id-1",
            "created_at": "2026-08-06T20:00:00+08:00",
            "updated_at": "2026-08-06T20:00:00+08:00",
            "source": { "type": "video", "url": "https://example.com" },
            "article": { "path": "article.md", "content_source": "content.json" },
            "transcript": { "path": "transcript.json", "language": "zh-CN" },
            "primary_media": {
                "type": "video",
                "original_path": "media/original.mkv",
                "playback_path": "media/playback.mp4",
                "mime_type": "video/mp4",
                "title": "t"
            }
        })
    }

    #[test]
    fn parses_valid_manifest() {
        let m = parse_manifest(&serde_json::to_vec(&valid_manifest_json()).unwrap()).unwrap();
        assert_eq!(m.id, "id-1");
        assert_eq!(m.primary_media.as_ref().unwrap().mime_type, "video/mp4");
    }

    #[test]
    fn rejects_bad_json() {
        assert!(parse_manifest(b"not json").is_err());
    }

    #[test]
    fn rejects_wrong_schema_version() {
        let mut v = valid_manifest_json();
        v["schema_version"] = serde_json::json!(2);
        assert!(parse_manifest(&serde_json::to_vec(&v).unwrap()).is_err());
    }

    #[test]
    fn rejects_empty_id() {
        let mut v = valid_manifest_json();
        v["id"] = serde_json::json!("");
        assert!(parse_manifest(&serde_json::to_vec(&v).unwrap()).is_err());
    }

    #[test]
    fn primary_media_can_be_null() {
        let mut v = valid_manifest_json();
        v["primary_media"] = serde_json::Value::Null;
        let m: Manifest = serde_json::from_value(v).unwrap();
        assert!(m.primary_media.is_none());
    }

    #[test]
    fn source_metadata_fields_parsed() {
        let mut v = valid_manifest_json();
        v["source"] = serde_json::json!({
            "type": "video",
            "url": "https://example.com/source",
            "title": "Original English Title",
            "author": "频道名",
            "published_at": "2026-08-01T08:00:00Z"
            ,"description": "来源简介"
        });
        let m: Manifest = serde_json::from_value(v).unwrap();
        assert_eq!(m.source.title.as_deref(), Some("Original English Title"));
        assert_eq!(m.source.author.as_deref(), Some("频道名"));
        assert_eq!(
            m.source.published_at.as_deref(),
            Some("2026-08-01T08:00:00Z")
        );
        assert_eq!(m.source.description.as_deref(), Some("来源简介"));
    }

    #[test]
    fn source_fields_optional() {
        let mut v = valid_manifest_json();
        v["source"] = serde_json::json!({ "type": "article" });
        let m: Manifest = serde_json::from_value(v).unwrap();
        assert!(m.source.url.is_none());
        assert!(m.source.title.is_none());
        assert!(m.source.author.is_none());
        assert!(m.source.description.is_none());
    }

    #[test]
    fn parses_media_items_and_rejects_duplicate_ids() {
        let mut v = valid_manifest_json();
        let item = serde_json::json!({
            "id": "inline-1",
            "role": "inline",
            "type": "audio",
            "original_path": "media/inline-1.m4a",
            "playback_path": "media/inline-1.mp3",
            "mime_type": "audio/mpeg",
            "title": "插入音频",
            "description": "音频说明",
            "poster_path": null
        });
        v["media_items"] = serde_json::json!([item.clone()]);
        let parsed = parse_manifest(&serde_json::to_vec(&v).unwrap()).unwrap();
        assert_eq!(parsed.media_items.len(), 1);

        v["media_items"] = serde_json::json!([item.clone(), item]);
        let error = parse_manifest(&serde_json::to_vec(&v).unwrap()).unwrap_err();
        assert!(error.contains("重复"));
    }

    #[test]
    fn rejects_empty_media_id_and_unknown_role_or_type() {
        let mut v = valid_manifest_json();
        v["media_items"] = serde_json::json!([{
            "id": "",
            "role": "inline",
            "type": "video",
            "original_path": "media/a.mp4",
            "playback_path": "media/a.mp4",
            "mime_type": "video/mp4"
        }]);
        assert!(parse_manifest(&serde_json::to_vec(&v).unwrap()).is_err());

        v["media_items"][0]["id"] = serde_json::json!("a");
        v["media_items"][0]["role"] = serde_json::json!("other");
        assert!(parse_manifest(&serde_json::to_vec(&v).unwrap()).is_err());

        v["media_items"][0]["role"] = serde_json::json!("inline");
        v["media_items"][0]["type"] = serde_json::json!("image");
        assert!(parse_manifest(&serde_json::to_vec(&v).unwrap()).is_err());
    }
}
