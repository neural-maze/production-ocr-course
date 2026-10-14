mod config;
mod models;
mod pdf;
mod processor;

use axum::{
    extract::{DefaultBodyLimit, Json, State},
    http::StatusCode,
    response::{
        sse::{Event, KeepAlive, Sse},
        IntoResponse,
    },
    routing::{get, post},
    Router,
};
use reqwest::Client;
use std::convert::Infallible;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;
use tokio_stream::StreamExt;
use tower_http::cors::{Any, CorsLayer};
use tower_http::trace::TraceLayer;
use tracing::{error, info};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

use crate::config::AppConfig;
use crate::models::{ErrorResponse, HealthResponse, OcrStreamEvent, ProcessRequest};
use crate::processor::DocumentProcessor;

#[derive(Clone)]
struct AppState {
    config: Arc<AppConfig>,
    processor: Arc<DocumentProcessor>,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // 1. Initialize structured logging
    tracing_subscriber::registry()
        .with(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "ocr_gateway=info,tower_http=info".into()),
        )
        .with(tracing_subscriber::fmt::layer())
        .init();

    // 2. Load application configuration
    let config = Arc::new(AppConfig::from_env());
    info!("Starting OCR API Gateway with config: {:?}", config);

    // 3. Build optimized HTTP client with connection pooling
    let http_client = Client::builder()
        .pool_max_idle_per_host(32)
        .pool_idle_timeout(Duration::from_secs(90))
        .tcp_keepalive(Duration::from_secs(60))
        .timeout(Duration::from_secs(config.request_timeout_secs))
        .build()?;

    // 4. Initialize processor
    let processor = Arc::new(DocumentProcessor::new(config.clone(), http_client)?);
    let state = AppState { config: config.clone(), processor };

    // 5. Build Axum router with CORS, Tracing, Body Limit (100MB), and SSE streaming
    let app = Router::new()
        .route("/health", get(health_handler))
        .route("/process", post(process_handler))
        .route("/process-stream", post(process_stream_handler))
        .layer(DefaultBodyLimit::max(100 * 1024 * 1024))
        .layer(TraceLayer::new_for_http())
        .layer(
            CorsLayer::new()
                .allow_origin(Any)
                .allow_methods(Any)
                .allow_headers(Any),
        )
        .with_state(state);

    // 6. Bind TCP listener
    let addr = SocketAddr::from(([0, 0, 0, 0], config.port));
    info!("🚀 Rust OCR API Gateway listening on http://{}", addr);

    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await?;

    info!("OCR Gateway shut down gracefully.");
    Ok(())
}

/// Health check handler for Kubernetes Liveness & Readiness Probes
async fn health_handler(State(state): State<AppState>) -> impl IntoResponse {
    let health = HealthResponse {
        status: "ok".to_string(),
        model: state.config.model_id.clone(),
        vllm_endpoint: state.config.vllm_base_url.clone(),
        pdf_max_size: state.config.pdf_max_size,
    };
    (StatusCode::OK, Json(health))
}

/// Main synchronous document processing handler
async fn process_handler(
    State(state): State<AppState>,
    Json(payload): Json<ProcessRequest>,
) -> impl IntoResponse {
    if payload.file.trim().is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(ErrorResponse {
                error: "Invalid request".to_string(),
                details: Some("The 'file' field cannot be empty. Please provide a Base64-encoded PDF or image.".to_string()),
            }),
        )
            .into_response();
    }

    match state.processor.process_document(&payload.file, payload.batch_size, payload.concurrency).await {
        Ok(response) => (StatusCode::OK, Json(response)).into_response(),
        Err(err) => {
            error!("Document processing failed: {:?}", err);
            let err_msg = err.to_string();
            let status = if err_msg.contains("exceeds the maximum allowable limit") {
                StatusCode::PAYLOAD_TOO_LARGE
            } else if err_msg.contains("Unsupported document format") || err_msg.contains("Base64") {
                StatusCode::BAD_REQUEST
            } else if err_msg.contains("connection") || err_msg.contains("refused") || err_msg.contains("vLLM") || err_msg.contains("503") {
                StatusCode::SERVICE_UNAVAILABLE
            } else {
                StatusCode::INTERNAL_SERVER_ERROR
            };

            (
                status,
                Json(ErrorResponse {
                    error: "Processing failed".to_string(),
                    details: Some(err_msg),
                }),
            )
                .into_response()
        }
    }
}

/// Real-Time Server-Sent Events (SSE) Streaming Handler
async fn process_stream_handler(
    State(state): State<AppState>,
    Json(payload): Json<ProcessRequest>,
) -> impl IntoResponse {
    if payload.file.trim().is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(ErrorResponse {
                error: "Invalid request".to_string(),
                details: Some("The 'file' field cannot be empty. Please provide a Base64-encoded PDF or image.".to_string()),
            }),
        )
            .into_response();
    }

    let stream = state
        .processor
        .clone()
        .process_document_stream(payload.file, payload.batch_size, payload.concurrency);

    let event_stream = stream.map(|event| {
        let event_type = match &event {
            OcrStreamEvent::PageStart { .. } => "page_start",
            OcrStreamEvent::Bbox { .. } => "bbox",
            OcrStreamEvent::Token { .. } => "token",
            OcrStreamEvent::PageDone { .. } => "page_done",
            OcrStreamEvent::Done { .. } => "done",
            OcrStreamEvent::Error { .. } => "error",
        };
        let data_str = serde_json::to_string(&event).unwrap_or_default();
        Ok::<_, Infallible>(Event::default().event(event_type).data(data_str))
    });

    Sse::new(event_stream)
        .keep_alive(KeepAlive::default())
        .into_response()
}

/// Graceful shutdown signal listener (SIGINT / SIGTERM)
async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("failed to install Ctrl+C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("failed to install signal handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => info!("Received Ctrl+C, initiating graceful shutdown..."),
        _ = terminate => info!("Received SIGTERM, initiating graceful shutdown..."),
    }
}
