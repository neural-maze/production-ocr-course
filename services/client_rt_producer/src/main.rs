use axum::{
    extract::{DefaultBodyLimit, Multipart, Path, State},
    http::StatusCode,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use base64::{engine::general_purpose, Engine as _};
use redis::AsyncCommands;
use serde::Serialize;
use std::sync::Arc;
use uuid::Uuid;
use tower_http::limit::RequestBodyLimitLayer;

// Represents the shared state of our application.
// We use `Arc` (Atomic Reference Counted) when passing this state to our route handlers
// so that multiple requests can share the same Redis client concurrently and safely.
#[derive(Clone)]
struct AppState {
    redis_client: redis::Client,
}

// Defines the JSON response structure when a task is successfully submitted.
// `Serialize` allows the struct to be automatically converted to JSON.
#[derive(Serialize)]
struct TaskResponse {
    task_id: String, // The unique identifier for the OCR task
    status: String,  // The initial status, usually "queued"
}

// Defines the JSON response structure when querying the status of a task.
#[derive(Serialize)]
struct StatusResponse {
    task_id: String,
    status: String, // Current status (e.g., "queued", "processing", "done", "failed")
    // Option is used here because 'result' and 'error' might not be present 
    // depending on the current status of the task.
    result: Option<serde_json::Value>, 
    error: Option<String>,
}

// The `tokio::main` macro sets up the asynchronous runtime needed by Axum and Redis.
#[tokio::main]
async fn main() {
    // 1. Initialize Logging
    // Sets up formatted console logging for tracing events (like errors or info).
    tracing_subscriber::fmt::init();

    // 2. Redis Config
    // Read Redis host and port from environment variables, falling back to defaults
    // suitable for our Kubernetes environment ("ocr-redis-service").
    let redis_host = std::env::var("REDIS_HOST").unwrap_or_else(|_| "ocr-redis-service".to_string());
    let redis_port = std::env::var("REDIS_PORT").unwrap_or_else(|_| "6379".to_string());
    
    // Construct the Redis connection URL.
    let redis_url = format!("redis://{}:{}", redis_host, redis_port);
    
    // Create the Redis client. This doesn't connect yet, just prepares the client.
    let redis_client = redis::Client::open(redis_url).expect("Failed to create Redis client");

    // Wrap the client in an Arc to share it safely across multiple async request handlers.
    let state = Arc::new(AppState { redis_client });

    // 3. Define Router
    // Create the Axum application router.
    let app = Router::new()
        // Map the POST /process endpoint to the `submit_task` handler.
        .route("/process", post(submit_task))
        // Map the GET /status/:task_id endpoint to the `get_status` handler.
        // The `:task_id` part is a path parameter.
        .route("/status/:task_id", get(get_status))
        // Map the GET /health endpoint to a simple health check handler.
        .route("/health", get(health))
        // Disable Axum's default body limit (which is usually quite small, around 2MB).
        .layer(DefaultBodyLimit::disable())
        // Apply a custom body limit of 10MB to accommodate large PDFs and images.
        .layer(RequestBodyLimitLayer::new(10 * 1024 * 1024))
        // Provide the shared state to the router so handlers can extract it.
        .with_state(state);

    // Define the address and port to listen on (0.0.0.0 binds to all network interfaces).
    let addr = std::net::SocketAddr::from(([0, 0, 0, 0], 5000));
    println!("🚀 Rust Producer API listening on {}", addr);
    
    // Start the Axum server.
    axum::Server::bind(&addr)
        .serve(app.into_make_service())
        .await
        .unwrap();
}

// Handler for submitting a new OCR task.
// Expects a multipart form data upload containing a file.
async fn submit_task(
    // Extract the shared application state (which contains our Redis client).
    State(state): State<Arc<AppState>>,
    // Extract the multipart form data stream.
    mut multipart: Multipart,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    
    // Get an asynchronous connection to Redis. 
    // If it fails, map the error to an HTTP 500 Internal Server Error.
    let mut conn = state.redis_client.get_async_connection().await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    
    // Generate a unique UUID v4 for the new task.
    let task_id = Uuid::new_v4().to_string();

    // Iterate through the fields of the multipart upload stream.
    while let Some(field) = multipart.next_field().await
        .map_err(|e| (StatusCode::BAD_REQUEST, e.to_string()))? 
    {
        // We are only interested in the field named "file".
        if field.name() == Some("file") {
            // Extract the filename. If none is provided, default to "unknown.pdf".
            let filename = field.file_name().unwrap_or("unknown.pdf").to_string();
            
            // Extract the file extension to help the worker know what kind of file it is.
            let extension = filename.split('.').last().unwrap_or("pdf").to_string();
            
            // Read the binary data from the uploaded file field.
            let data = field.bytes().await
                .map_err(|e| (StatusCode::BAD_REQUEST, e.to_string()))?;
            
            // Encode the binary data into a base64 string.
            // This is required because Redis strings and hashes are better suited for text,
            // and the Python worker expects a base64 encoded string to decode.
            let base64_data = general_purpose::STANDARD.encode(&data);

            // The key used to store the task data in Redis (e.g., "task:1234-5678...").
            let task_key = format!("task:{}", task_id);
            
            // Atomic state setup via single HSET command.
            // We store the status, filename, extension, and the actual base64 data
            // in a Redis Hash. Using a single `HSET` command ensures all fields are
            // written simultaneously (atomically), avoiding race conditions where the
            // worker might read incomplete data.
            let _: () = redis::cmd("HSET")
                .arg(&task_key)
                .arg("status").arg("queued")
                .arg("filename").arg(&filename)
                .arg("extension").arg(&extension)
                .arg("data").arg(&base64_data)
                .query_async(&mut conn)
                .await
                .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

            // Push the task_id to the "ocr_tasks" list.
            // The Python worker listens (pops) from this list.
            // KEDA also monitors the length of this list to autoscale the worker pods.
            let _: () = conn.lpush("ocr_tasks", &task_id).await
                .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

            // Return a 202 Accepted response with the task_id and status as JSON.
            return Ok((StatusCode::ACCEPTED, Json(TaskResponse {
                task_id,
                status: "queued".to_string(),
            })));
        }
    }

    // If we looped through all fields and didn't find one named "file", return a 400 Bad Request.
    Err((StatusCode::BAD_REQUEST, "Missing 'file' field in multipart form".to_string()))
}

// Handler for retrieving the status and results of a task.
async fn get_status(
    State(state): State<Arc<AppState>>,
    // Extract the task_id from the URL path (e.g., /status/1234-5678...).
    Path(task_id): Path<String>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    
    // Get a Redis connection.
    let mut conn = state.redis_client.get_async_connection().await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    
    let task_key = format!("task:{}", task_id);

    // Check if the task key actually exists in Redis.
    let exists: bool = conn.exists(&task_key).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    
    if !exists {
        return Err((StatusCode::NOT_FOUND, "Task ID not found".to_string()));
    }

    // Fetch all fields and values from the task's Redis Hash.
    let data: std::collections::HashMap<String, String> = conn.hgetall(&task_key).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;

    // Extract the status. Default to "unknown" if missing.
    let status = data.get("status").cloned().unwrap_or_else(|| "unknown".to_string());
    
    // Extract the raw JSON result string, if it exists (set by the Python worker when done).
    let result_raw = data.get("result").cloned();
    
    // Extract the error message, if it exists (set by the Python worker on failure).
    let error = data.get("error").cloned();

    // Parse the raw JSON string back into a structured JSON Value (serde_json::Value).
    let result = result_raw.and_then(|r| serde_json::from_str(&r).ok());

    // Return a 200 OK response with the status, result, and/or error as JSON.
    Ok(Json(StatusResponse {
        task_id,
        status,
        result,
        error,
    }))
}

// Simple health check endpoint used by Kubernetes liveness/readiness probes.
async fn health() -> impl IntoResponse {
    StatusCode::OK
}
