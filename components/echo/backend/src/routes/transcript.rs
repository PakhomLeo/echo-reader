use axum::Json;
use axum::extract::{Path, State};
use axum::http::StatusCode;
use serde::Deserialize;
use serde_json::Value;

use crate::app_state::AppState;
use crate::error::ApiError;
use crate::library::paths::resolve_target_in;
use crate::models::api::{SaveResult, TranscriptResponse};
use crate::models::item::ValidItem;
use crate::models::transcript::TranscriptJson;
use crate::storage::atomic_write::atomic_write;
use crate::storage::version::version_of;

pub async fn get_transcript(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<TranscriptResponse>, ApiError> {
    let index = state.index.read().await;
    let item = index
        .get(&id)
        .ok_or_else(|| ApiError::not_found("该内容不存在或已被移除"))?;

    let bytes = std::fs::read(item.transcript_path())
        .map_err(|_| ApiError::not_found("该内容没有转录数据"))?;
    let transcript: TranscriptJson = serde_json::from_slice(&bytes).map_err(|e| {
        ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "TRANSCRIPT_JSON_INVALID",
            format!("transcript.json 解析失败: {e}"),
        )
    })?;
    transcript.validate().map_err(|e| {
        ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "TRANSCRIPT_JSON_INVALID",
            e,
        )
    })?;

    Ok(Json(TranscriptResponse {
        version: version_of(&bytes),
        language: transcript.language,
        segments: transcript.segments,
    }))
}

#[derive(Debug, Deserialize)]
pub struct TranscriptPatchRequest {
    expected_version: String,
    changes: Vec<Value>,
}

#[derive(Debug, Deserialize)]
struct ChangeBody {
    id: String,
    #[serde(default)]
    speaker: Option<String>,
    #[serde(default)]
    text: Option<String>,
}

pub fn now_rfc3339() -> String {
    chrono::Local::now().to_rfc3339()
}

pub fn touch_manifest(item: &ValidItem) -> Result<(), ApiError> {
    let manifest_path = resolve_target_in(&item.folder_path, "manifest.json")
        .map_err(|_| ApiError::internal("manifest 路径非法"))?;
    let bytes = std::fs::read(&manifest_path).map_err(ApiError::from)?;
    let mut manifest: crate::models::item::Manifest =
        serde_json::from_slice(&bytes).map_err(|e| {
            ApiError::new(
                StatusCode::UNPROCESSABLE_ENTITY,
                "MANIFEST_JSON_INVALID",
                format!("manifest.json 解析失败: {e}"),
            )
        })?;
    manifest.updated_at = now_rfc3339();
    let out =
        serde_json::to_vec_pretty(&manifest).map_err(|e| ApiError::internal(format!("{e}")))?;
    atomic_write(&manifest_path, &out).map_err(ApiError::from)
}

pub async fn patch_transcript(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(payload): Json<TranscriptPatchRequest>,
) -> Result<Json<SaveResult>, ApiError> {
    let index = state.index.read().await;
    let item = index
        .get(&id)
        .ok_or_else(|| ApiError::not_found("该内容不存在或已被移除"))?;

    let path = item.transcript_path();
    let bytes = std::fs::read(&path).map_err(ApiError::from)?;

    if version_of(&bytes) != payload.expected_version {
        return Err(ApiError::new(
            StatusCode::CONFLICT,
            "TRANSCRIPT_VERSION_CONFLICT",
            "转录已被其他进程修改。",
        ));
    }

    let mut transcript: TranscriptJson = serde_json::from_slice(&bytes).map_err(|e| {
        ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "TRANSCRIPT_JSON_INVALID",
            format!("transcript.json 解析失败: {e}"),
        )
    })?;
    transcript.validate().map_err(|e| {
        ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "TRANSCRIPT_JSON_INVALID",
            e,
        )
    })?;

    // 只允许修改 speaker / text；携带时间戳字段一律拒绝
    for change in &payload.changes {
        if change.get("start_ms").is_some() || change.get("end_ms").is_some() {
            return Err(ApiError::bad_request("不允许修改转录时间戳"));
        }
    }

    let mut by_index: std::collections::HashMap<String, usize> = Default::default();
    for (i, seg) in transcript.segments.iter().enumerate() {
        by_index.insert(seg.id.clone(), i);
    }

    for change in &payload.changes {
        let body: ChangeBody = serde_json::from_value(change.clone()).map_err(|_| {
            ApiError::new(
                StatusCode::UNPROCESSABLE_ENTITY,
                "TRANSCRIPT_CHANGE_INVALID",
                "修改项格式不合法",
            )
        })?;
        if body.speaker.is_none() && body.text.is_none() {
            return Err(ApiError::bad_request("修改项至少需要 speaker 或 text 之一"));
        }
        let Some(&idx) = by_index.get(&body.id) else {
            return Err(ApiError::bad_request(format!("转录段不存在: {}", body.id)));
        };
        let seg = &mut transcript.segments[idx];
        if let Some(speaker) = body.speaker {
            seg.speaker = speaker;
        }
        if let Some(text) = body.text {
            seg.text = text;
        }
    }

    let out =
        serde_json::to_vec_pretty(&transcript).map_err(|e| ApiError::internal(format!("{e}")))?;
    atomic_write(&path, &out).map_err(ApiError::from)?;
    touch_manifest(item)?;

    Ok(Json(SaveResult {
        version: version_of(&out),
        saved_at: now_rfc3339(),
    }))
}
