use axum::Json;
use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};

use crate::app_state::AppState;
use crate::error::ApiError;

pub fn require_internal_token(state: &AppState, headers: &HeaderMap) -> Result<(), ApiError> {
    let supplied = headers
        .get("X-Echo-Internal-Token")
        .and_then(|value| value.to_str().ok());
    if state.config.internal_token.as_deref().is_none()
        || supplied != state.config.internal_token.as_deref()
    {
        return Err(ApiError::new(
            StatusCode::UNAUTHORIZED,
            "UNAUTHORIZED",
            "invalid internal token",
        ));
    }
    Ok(())
}

pub async fn shutdown(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<(StatusCode, Json<serde_json::Value>), ApiError> {
    require_internal_token(&state, &headers)?;
    state.shutdown.notify_one();
    Ok((
        StatusCode::ACCEPTED,
        Json(serde_json::json!({"status": "shutting_down"})),
    ))
}
