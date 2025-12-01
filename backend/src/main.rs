use std::{
    fs::OpenOptions,
    net::SocketAddr,
    path::{Path, PathBuf},
    sync::OnceLock,
};

use anyhow::{Context, Result};
use axum::{
    body::Body,
    extract::{Path as AxumPath, Query, State},
    http::{header::AUTHORIZATION, Request, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use chrono::{DateTime, FixedOffset, Utc};
use jsonwebtoken::{decode, encode, DecodingKey, EncodingKey, Header, Validation};
use serde::{Deserialize, Serialize};
use sqlx::{postgres::PgPoolOptions, FromRow, Pool, Postgres, Row};
use tower_http::{cors::CorsLayer, trace::TraceLayer};
use tracing_subscriber::{fmt, layer::SubscriberExt, prelude::*, util::SubscriberInitExt};
use uuid::Uuid;

static FILE_GUARD: OnceLock<tracing_appender::non_blocking::WorkerGuard> = OnceLock::new();

#[derive(Clone)]
struct AppState {
    pool: Pool<Postgres>,
    capture_root: Option<PathBuf>,
    auth: AuthSettings,
}

#[derive(Clone)]
struct AuthSettings {
    username: String,
    password: String,
    session_minutes: i64,
    encoding: EncodingKey,
    decoding: DecodingKey,
}

#[derive(Debug, Deserialize)]
struct ListParams {
    limit: Option<i64>,
    group_tag: Option<String>,
}

#[derive(Debug, Serialize, FromRow)]
struct FaceCaptureRow {
    id: Uuid,
    identity: String,
    group_tag: String,
    frame_path: Option<String>,
    face_distance: Option<f64>,
    timestamp: DateTime<FixedOffset>,
}

#[derive(Debug, Serialize)]
struct FaceCapture {
    id: Uuid,
    identity: String,
    group_tag: String,
    face_distance: Option<f64>,
    timestamp: DateTime<FixedOffset>,
    image_url: Option<String>,
}

#[derive(Debug, Deserialize)]
struct PostureListParams {
    limit: Option<i64>,
    is_bad: Option<bool>,
}

#[derive(Debug, Serialize, FromRow)]
struct PostureRow {
    id: Uuid,
    identity: String,
    is_bad: bool,
    nose_drop: Option<f64>,
    neck_angle: Option<f64>,
    reasons: Option<String>,
    face_distance: Option<f64>,
    frame_path: Option<String>,
    face_capture_id: Option<Uuid>,
    timestamp: DateTime<FixedOffset>,
}

#[derive(Debug, Serialize)]
struct PostureEvent {
    id: Uuid,
    identity: String,
    is_bad: bool,
    nose_drop: Option<f64>,
    neck_angle: Option<f64>,
    reasons: Vec<String>,
    face_distance: Option<f64>,
    #[serde(skip_serializing)]
    #[allow(dead_code)]
    frame_path: Option<String>,
    #[serde(skip_serializing)]
    #[allow(dead_code)]
    face_capture_id: Option<Uuid>,
    timestamp: DateTime<FixedOffset>,
    image_url: Option<String>,
}

#[derive(Debug, Serialize)]
struct ApiErrorBody {
    message: String,
}

#[derive(Debug, Deserialize)]
struct LoginRequest {
    username: String,
    password: String,
}

#[derive(Debug, Serialize)]
struct LoginResponse {
    token: String,
    expires_at: i64,
    username: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
struct Claims {
    sub: String,
    exp: usize,
    iat: usize,
}

struct ApiError(anyhow::Error, StatusCode);

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let status = self.1;
        let body = Json(ApiErrorBody {
            message: self.0.to_string(),
        });
        (status, body).into_response()
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    dotenvy::dotenv().ok();
    init_tracing();

    let config = load_settings_multi(["config/settings.yaml", "../config/settings.yaml"]);
    let dsn = std::env::var("DATABASE_URL")
        .ok()
        .or_else(|| {
            config
                .as_ref()
                .and_then(|c| c.storage.as_ref())
                .and_then(|s| s.postgres_dsn.clone())
        })
        .context("DATABASE_URL 未配置，且 config/settings.yaml 未提供 storage.postgres_dsn")?;

    let pool = PgPoolOptions::new()
        .max_connections(5)
        .connect(&dsn)
        .await
        .context("无法连接数据库，请检查 DSN/网络")?;

    let capture_root = capture_root(config.as_ref());

    let auth = build_auth_settings(config.as_ref());

    let state = AppState {
        pool,
        capture_root,
        auth,
    };

    let protected_routes = Router::new()
        .route("/api/face-captures", get(list_face_captures))
        .route("/api/face-captures/:id/image", get(get_face_capture_image))
        .route("/api/posture-events", get(list_posture_events))
        .route(
            "/api/posture-events/:id/image",
            get(get_posture_event_image),
        )
        .with_state(state.clone())
        .route_layer(middleware::from_fn_with_state(state.clone(), require_auth));

    let app = Router::new()
        .route("/api/login", post(login))
        .merge(protected_routes)
        .with_state(state)
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http());

    let bind_address = std::env::var("BIND_ADDRESS")
        .ok()
        .or_else(|| {
            config
                .as_ref()
                .and_then(|c| c.server.as_ref())
                .and_then(|s| s.backend_bind.clone())
        })
        .unwrap_or_else(|| "0.0.0.0:8000".to_string());

    let addr: SocketAddr = bind_address
        .parse()
        .context("无效的 BIND_ADDRESS，示例：0.0.0.0:8000")?;

    tracing::info!("Listening on {}", addr);
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .context("监听地址失败，请检查端口占用")?;

    axum::serve(listener, app.into_make_service())
        .await
        .context("服务运行失败")?;

    Ok(())
}

async fn login(
    State(state): State<AppState>,
    Json(payload): Json<LoginRequest>,
) -> Result<Json<LoginResponse>, ApiError> {
    if payload.username != state.auth.username || payload.password != state.auth.password {
        return Err(ApiError(
            anyhow::anyhow!("用户名或密码错误"),
            StatusCode::UNAUTHORIZED,
        ));
    }

    let now = Utc::now();
    let exp = now + chrono::Duration::minutes(state.auth.session_minutes.max(1));
    let claims = Claims {
        sub: payload.username.clone(),
        iat: now.timestamp() as usize,
        exp: exp.timestamp() as usize,
    };

    let token = encode(&Header::default(), &claims, &state.auth.encoding)
        .map_err(|err| ApiError(err.into(), StatusCode::INTERNAL_SERVER_ERROR))?;

    Ok(Json(LoginResponse {
        token,
        expires_at: exp.timestamp(),
        username: payload.username,
    }))
}

async fn require_auth(
    State(state): State<AppState>,
    mut req: Request<Body>,
    next: Next,
) -> Result<Response, StatusCode> {
    let token = extract_token(&req).ok_or(StatusCode::UNAUTHORIZED)?;

    let claims = validate_token(token, &state.auth).map_err(|_| StatusCode::UNAUTHORIZED)?;
    req.extensions_mut().insert(claims);
    Ok(next.run(req).await)
}

async fn list_face_captures(
    State(state): State<AppState>,
    Query(params): Query<ListParams>,
) -> Result<Json<Vec<FaceCapture>>, ApiError> {
    let limit = params.limit.unwrap_or(40).clamp(1, 200);
    let group_tag = params
        .group_tag
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(|value| value.to_string());

    if let Some(tag) = group_tag.as_deref() {
        tracing::info!("GET /api/face-captures?limit={}&group_tag={}", limit, tag);
    } else {
        tracing::info!("GET /api/face-captures?limit={}", limit);
    }

    let rows = if let Some(group_tag) = group_tag {
        sqlx::query_as::<_, FaceCaptureRow>(
            r#"
            SELECT
                id,
                identity,
                group_tag,
                frame_path,
                face_distance,
                timestamp
            FROM face_captures
            WHERE group_tag = $1
            ORDER BY timestamp DESC
            LIMIT $2
            "#,
        )
        .bind(group_tag)
        .bind(limit)
        .fetch_all(&state.pool)
        .await
    } else {
        sqlx::query_as::<_, FaceCaptureRow>(
            r#"
            SELECT
                id,
                identity,
                group_tag,
                frame_path,
                face_distance,
                timestamp
            FROM face_captures
            ORDER BY timestamp DESC
            LIMIT $1
            "#,
        )
        .bind(limit)
        .fetch_all(&state.pool)
        .await
    }
    .map_err(|err| ApiError(err.into(), StatusCode::INTERNAL_SERVER_ERROR))?;

    let data = rows
        .into_iter()
        .map(|row| {
            let image_url = row
                .frame_path
                .as_ref()
                .map(|_| format!("/api/face-captures/{}/image", row.id));
            FaceCapture {
                id: row.id,
                identity: row.identity,
                group_tag: row.group_tag,
                face_distance: row.face_distance,
                timestamp: row.timestamp,
                image_url,
            }
        })
        .collect();

    Ok(Json(data))
}

async fn get_face_capture_image(
    AxumPath(id): AxumPath<Uuid>,
    State(state): State<AppState>,
) -> Result<Response, ApiError> {
    tracing::info!("GET /api/face-captures/{}/image", id);
    let capture_root = state.capture_root.clone().ok_or_else(|| {
        ApiError(
            anyhow::anyhow!("capture root not configured"),
            StatusCode::INTERNAL_SERVER_ERROR,
        )
    })?;

    let row = sqlx::query(r#"SELECT frame_path FROM face_captures WHERE id = $1"#)
        .bind(id)
        .fetch_optional(&state.pool)
        .await
        .map_err(|err| ApiError(err.into(), StatusCode::INTERNAL_SERVER_ERROR))?
        .ok_or_else(|| {
            ApiError(
                anyhow::anyhow!("face capture not found"),
                StatusCode::NOT_FOUND,
            )
        })?;

    let frame_path: Option<String> = row.try_get("frame_path").map_err(|err| {
        ApiError(
            anyhow::anyhow!("invalid frame_path data: {}", err),
            StatusCode::INTERNAL_SERVER_ERROR,
        )
    })?;

    let frame_path = frame_path.ok_or_else(|| {
        ApiError(
            anyhow::anyhow!("face capture has no associated frame path"),
            StatusCode::NOT_FOUND,
        )
    })?;

    let target_path = sanitize_capture_path(&capture_root, Path::new(&frame_path))
        .map_err(|err| ApiError(err, StatusCode::BAD_REQUEST))?;

    let data = tokio::fs::read(&target_path)
        .await
        .map_err(|err| ApiError(err.into(), StatusCode::NOT_FOUND))?;

    let content_type = mime_guess::from_path(&target_path)
        .first_or_octet_stream()
        .to_string();

    Ok(([(axum::http::header::CONTENT_TYPE, content_type)], data).into_response())
}

async fn list_posture_events(
    State(state): State<AppState>,
    Query(params): Query<PostureListParams>,
) -> Result<Json<Vec<PostureEvent>>, ApiError> {
    let limit = params.limit.unwrap_or(50).clamp(1, 200);
    let is_bad = params.is_bad;
    tracing::info!(
        "GET /api/posture-events?limit={}&is_bad={:?}",
        limit,
        is_bad
    );

    let query = if is_bad.is_some() {
        r#"
        SELECT
            id,
            identity,
            is_bad,
            nose_drop,
            neck_angle,
            reasons,
            face_distance,
            frame_path,
            face_capture_id,
            timestamp
        FROM posture_events
        WHERE is_bad = $2
        ORDER BY timestamp DESC
        LIMIT $1
        "#
    } else {
        r#"
        SELECT
            id,
            identity,
            is_bad,
            nose_drop,
            neck_angle,
            reasons,
            face_distance,
            frame_path,
            face_capture_id,
            timestamp
        FROM posture_events
        ORDER BY timestamp DESC
        LIMIT $1
        "#
    };

    let rows: Vec<PostureRow> = if let Some(flag) = is_bad {
        sqlx::query_as(query).bind(limit).bind(flag)
    } else {
        sqlx::query_as(query).bind(limit)
    }
    .fetch_all(&state.pool)
    .await
    .map_err(|err| ApiError(err.into(), StatusCode::INTERNAL_SERVER_ERROR))?;

    let data = rows
        .into_iter()
        .map(|row| {
            let reasons = row
                .reasons
                .as_ref()
                .map(|r| {
                    r.split(',')
                        .map(|part| part.trim().to_string())
                        .filter(|s| !s.is_empty())
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default();
            // Prefer the linked face capture image when available; otherwise fall back to posture image.
            let image_url = row
                .face_capture_id
                .map(|face_id| format!("/api/face-captures/{}/image", face_id))
                .or_else(|| {
                    row.frame_path
                        .as_ref()
                        .map(|_| format!("/api/posture-events/{}/image", row.id))
                });
            PostureEvent {
                id: row.id,
                identity: row.identity,
                is_bad: row.is_bad,
                nose_drop: row.nose_drop,
                neck_angle: row.neck_angle,
                reasons,
                face_distance: row.face_distance,
                frame_path: row.frame_path,
                face_capture_id: row.face_capture_id,
                timestamp: row.timestamp,
                image_url,
            }
        })
        .collect();

    Ok(Json(data))
}

async fn get_posture_event_image(
    AxumPath(id): AxumPath<Uuid>,
    State(state): State<AppState>,
) -> Result<Response, ApiError> {
    tracing::info!("GET /api/posture-events/{}/image", id);
    let capture_root = state.capture_root.clone().ok_or_else(|| {
        ApiError(
            anyhow::anyhow!("capture root not configured"),
            StatusCode::INTERNAL_SERVER_ERROR,
        )
    })?;

    let row = sqlx::query(r#"SELECT frame_path FROM posture_events WHERE id = $1"#)
        .bind(id)
        .fetch_optional(&state.pool)
        .await
        .map_err(|err| ApiError(err.into(), StatusCode::INTERNAL_SERVER_ERROR))?
        .ok_or_else(|| {
            ApiError(
                anyhow::anyhow!("posture event not found"),
                StatusCode::NOT_FOUND,
            )
        })?;

    let frame_path: Option<String> = row.try_get("frame_path").map_err(|err| {
        ApiError(
            anyhow::anyhow!("invalid frame_path data: {}", err),
            StatusCode::INTERNAL_SERVER_ERROR,
        )
    })?;

    let frame_path = frame_path.ok_or_else(|| {
        ApiError(
            anyhow::anyhow!("posture event has no associated frame path"),
            StatusCode::NOT_FOUND,
        )
    })?;

    let target_path = sanitize_capture_path(&capture_root, Path::new(&frame_path))
        .map_err(|err| ApiError(err, StatusCode::BAD_REQUEST))?;

    let data = tokio::fs::read(&target_path)
        .await
        .map_err(|err| ApiError(err.into(), StatusCode::NOT_FOUND))?;

    let content_type = mime_guess::from_path(&target_path)
        .first_or_octet_stream()
        .to_string();

    Ok(([(axum::http::header::CONTENT_TYPE, content_type)], data).into_response())
}

fn init_tracing() {
    let _config = load_settings_multi(["config/settings.yaml", "../config/settings.yaml"]);
    let log_level = std::env::var("RUST_LOG")
        .ok()
        .unwrap_or_else(|| "info,tower_http=info".to_string());

    let env_filter =
        tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| log_level.into());

    let stdout_layer = fmt::layer().with_writer(std::io::stderr).boxed();
    let file_layer = build_file_writer("logs/backend.log")
        .map(|writer| fmt::layer().with_ansi(false).with_writer(writer).boxed());

    tracing_subscriber::registry()
        .with(env_filter)
        .with(stdout_layer)
        .with(file_layer)
        .init();
}

#[derive(Debug, Deserialize, Default)]
struct Settings {
    #[serde(default)]
    storage: Option<StorageConfig>,
    #[serde(default)]
    server: Option<ServerConfig>,
    #[serde(default)]
    face_capture: Option<FaceCaptureConfig>,
    #[serde(default)]
    auth: Option<AuthConfig>,
}

#[derive(Debug, Deserialize)]
struct StorageConfig {
    #[serde(default)]
    postgres_dsn: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ServerConfig {
    #[serde(default)]
    backend_bind: Option<String>,
    #[serde(default)]
    #[allow(dead_code)]
    // retained for config compatibility (used by deploy/nginx, not backend runtime)
    external_port: Option<u16>,
}

#[derive(Debug, Deserialize)]
struct FaceCaptureConfig {
    #[serde(default)]
    root: Option<String>,
}

#[derive(Debug, Deserialize, Default)]
struct AuthConfig {
    #[serde(default)]
    username: Option<String>,
    #[serde(default)]
    password: Option<String>,
    #[serde(default)]
    secret: Option<String>,
    #[serde(default)]
    session_minutes: Option<i64>,
}

fn load_config(path: impl AsRef<Path>) -> Result<Settings> {
    let path = path.as_ref();
    let content = std::fs::read_to_string(path)
        .with_context(|| format!("无法读取配置文件 {}", path.display()))?;
    let parsed: Settings =
        serde_yaml::from_str(&content).with_context(|| format!("解析 {} 失败", path.display()))?;
    Ok(parsed)
}

fn load_settings_multi<const N: usize>(candidates: [&str; N]) -> Option<Settings> {
    for path in candidates {
        if Path::new(path).exists() {
            match load_config(path) {
                Ok(cfg) => return Some(cfg),
                Err(err) => {
                    tracing::warn!("读取配置 {} 失败: {}", path, err);
                }
            }
        }
    }
    None
}

fn capture_root(settings: Option<&Settings>) -> Option<PathBuf> {
    let repo_root = repo_root();
    let raw = settings?
        .face_capture
        .as_ref()
        .and_then(|c| c.root.as_ref())
        .map(PathBuf::from)
        .or_else(|| Some(PathBuf::from("data/captures")))?;

    Some(if raw.is_absolute() {
        raw
    } else {
        repo_root.join(raw)
    })
}

fn sanitize_capture_path(root: &Path, candidate: &Path) -> Result<PathBuf> {
    let root = root
        .canonicalize()
        .with_context(|| format!("无法解析捕获根目录 {}", root.display()))?;
    let full = if candidate.is_absolute() {
        candidate.to_path_buf()
    } else {
        root.join(candidate)
    }
    .canonicalize()
    .with_context(|| format!("无法解析捕获文件路径 {}", candidate.display()))?;

    if !full.starts_with(&root) {
        anyhow::bail!("捕获文件路径超出允许目录");
    }
    Ok(full)
}

fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(Path::to_path_buf)
        .expect("repo root")
}

fn build_file_writer(path: &str) -> Option<tracing_appender::non_blocking::NonBlocking> {
    let path = repo_root().join(path);
    if let Some(parent) = path.parent() {
        if let Err(err) = std::fs::create_dir_all(parent) {
            eprintln!("创建日志目录失败（{}）: {}", parent.display(), err);
            return None;
        }
    }

    let file = OpenOptions::new().create(true).append(true).open(&path);

    let file = match file {
        Ok(f) => f,
        Err(err) => {
            eprintln!("打开日志文件失败（{}）: {}", path.display(), err);
            return None;
        }
    };

    let (writer, guard) = tracing_appender::non_blocking(file);
    let _ = FILE_GUARD.set(guard); // keep guard alive for process lifetime

    Some(writer)
}

fn build_auth_settings(settings: Option<&Settings>) -> AuthSettings {
    let config_auth = settings.and_then(|s| s.auth.as_ref());

    let username = config_auth
        .and_then(|a| a.username.clone())
        .unwrap_or_else(|| "admin".to_string());

    let password = config_auth
        .and_then(|a| a.password.clone())
        .unwrap_or_else(|| "studyguardian".to_string());

    let secret = config_auth
        .and_then(|a| a.secret.clone())
        .unwrap_or_else(|| "change-me-please".to_string());

    let minutes = config_auth
        .and_then(|a| a.session_minutes)
        .unwrap_or(5)
        .max(1);

    AuthSettings {
        username,
        password,
        session_minutes: minutes,
        encoding: EncodingKey::from_secret(secret.as_bytes()),
        decoding: DecodingKey::from_secret(secret.as_bytes()),
    }
}

fn validate_token(token: &str, auth: &AuthSettings) -> Result<Claims> {
    let validation = Validation::default();
    let data = decode::<Claims>(token, &auth.decoding, &validation)?;
    Ok(data.claims)
}

fn extract_token(req: &Request<Body>) -> Option<&str> {
    if let Some(header) = req
        .headers()
        .get(AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .map(str::trim)
        .filter(|v| !v.is_empty())
    {
        return Some(header);
    }

    let query = req.uri().query().unwrap_or("");
    for pair in query.split('&') {
        let mut parts = pair.splitn(2, '=');
        if let (Some(key), Some(value)) = (parts.next(), parts.next()) {
            if key == "token" && !value.is_empty() {
                return Some(value);
            }
        }
    }
    None
}
