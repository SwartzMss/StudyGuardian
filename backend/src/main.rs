use std::{fs::OpenOptions, net::SocketAddr, path::Path, sync::OnceLock};

use anyhow::{Context, Result};
use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use chrono::NaiveDateTime;
use serde::{Deserialize, Serialize};
use sqlx::{postgres::PgPoolOptions, FromRow, Pool, Postgres};
use tower_http::{cors::CorsLayer, trace::TraceLayer};
use tracing_subscriber::{fmt, layer::SubscriberExt, prelude::*, util::SubscriberInitExt};

static FILE_GUARD: OnceLock<tracing_appender::non_blocking::WorkerGuard> = OnceLock::new();

#[derive(Clone)]
struct AppState {
    pool: Pool<Postgres>,
}

#[derive(Debug, Deserialize)]
struct ListParams {
    limit: Option<i64>,
}

#[derive(Debug, Serialize, FromRow)]
struct FaceCapture {
    id: i32,
    identity: String,
    group_tag: String,
    frame_path: Option<String>,
    face_distance: Option<f64>,
    timestamp: NaiveDateTime,
}

#[derive(Debug, Serialize)]
struct ApiErrorBody {
    message: String,
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

    let state = AppState { pool };
    let app = Router::new()
        .route("/api/face-captures", get(list_face_captures))
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

async fn list_face_captures(
    State(state): State<AppState>,
    Query(params): Query<ListParams>,
) -> Result<Json<Vec<FaceCapture>>, ApiError> {
    let limit = params.limit.unwrap_or(40).clamp(1, 200);
    let rows = sqlx::query_as::<_, FaceCapture>(
        r#"
        SELECT
            id,
            identity,
            group_tag,
            frame_path,
            face_distance,
            (timestamp AT TIME ZONE 'UTC') as timestamp
        FROM face_captures
        ORDER BY timestamp DESC
        LIMIT $1
        "#,
    )
    .bind(limit)
    .fetch_all(&state.pool)
    .await
    .map_err(|err| ApiError(err.into(), StatusCode::INTERNAL_SERVER_ERROR))?;

    Ok(Json(rows))
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

fn build_file_writer(path: &str) -> Option<tracing_appender::non_blocking::NonBlocking> {
    let path = Path::new(path);
    if let Some(parent) = path.parent() {
        if let Err(err) = std::fs::create_dir_all(parent) {
            eprintln!("创建日志目录失败（{}）: {}", parent.display(), err);
            return None;
        }
    }

    let file = OpenOptions::new().create(true).append(true).open(path);

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
