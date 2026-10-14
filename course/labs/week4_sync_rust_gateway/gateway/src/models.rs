use serde::{Deserialize, Serialize};

/// Client Request payload sent to POST /process
#[derive(Debug, Deserialize, Serialize)]
pub struct ProcessRequest {
    /// Base64-encoded string of the document (PDF or Image) or Data URI
    pub file: String,
    /// Optional batch size for multi-page PDF processing (default: 4)
    pub batch_size: Option<usize>,
    /// Optional parallel concurrent batch requests to vLLM (default: 1, clamp: 1..8)
    pub concurrency: Option<usize>,
}

/// Grounded layout bounding box
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BoundingBox {
    /// Detected label (e.g. "header", "title", "text", "table", "image")
    pub label: String,
    /// Coordinates normalized to 0..1000 scale (xmin, ymin, xmax, ymax)
    pub xmin: u32,
    pub ymin: u32,
    pub xmax: u32,
    pub ymax: u32,
    /// Associated text content or caption
    pub text: String,
}

/// Page-level OCR output with rasterized page image data and bounding boxes
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PageResult {
    /// 1-based page number
    pub page_number: usize,
    /// Optional Data URI of the rasterized page image (e.g. data:image/png;base64,...)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub image_data_uri: Option<String>,
    /// Sanitized clean markdown for this page
    pub markdown: String,
    /// Raw unscrubbed model output with grounding tags
    pub raw_text: String,
    /// Visual grounding bounding boxes for this page
    pub bboxes: Vec<BoundingBox>,
}

/// Client Response returned by POST /process
#[derive(Debug, Serialize, Deserialize)]
pub struct ProcessResponse {
    /// Extracted, post-processed clean markdown document
    pub markdown: String,
    /// Total number of pages processed (1 for single images)
    pub total_pages: usize,
    /// Number of vLLM batch inferences executed
    pub batches_processed: usize,
    /// End-to-end processing latency in milliseconds
    pub latency_ms: u128,
    /// Inferred input document type ("image/png", "image/jpeg", "application/pdf", etc.)
    pub document_type: String,
    /// Per-page details containing rasterized images, bounding boxes, and markdown
    pub pages: Vec<PageResult>,
    /// Flattened list of all detected bounding boxes across the document
    pub bboxes: Vec<BoundingBox>,
}

/// Real-time Server-Sent Event (SSE) DTO emitted during streaming
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "event", content = "data")]
pub enum OcrStreamEvent {
    #[serde(rename = "page_start")]
    PageStart {
        page_number: usize,
        total_pages: usize,
        image_data_uri: Option<String>,
    },
    #[serde(rename = "bbox")]
    Bbox {
        page_number: usize,
        box_id: usize,
        label: String,
        xmin: u32,
        ymin: u32,
        xmax: u32,
        ymax: u32,
    },
    #[serde(rename = "token")]
    Token {
        page_number: usize,
        box_id: usize,
        text: String,
    },
    #[serde(rename = "page_done")]
    PageDone {
        page_number: usize,
        markdown: String,
    },
    #[serde(rename = "done")]
    Done {
        status: String,
        total_pages: usize,
        latency_ms: u128,
    },
    #[serde(rename = "error")]
    Error {
        error: String,
    },
}

/// Error response structure
#[derive(Debug, Serialize, Deserialize)]
pub struct ErrorResponse {
    pub error: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub details: Option<String>,
}

/// Health check response
#[derive(Debug, Serialize, Deserialize)]
pub struct HealthResponse {
    pub status: String,
    pub model: String,
    pub vllm_endpoint: String,
    pub pdf_max_size: usize,
}

// ==============================================================================
// vLLM OpenAI-Compatible Chat Completion DTOs
// ==============================================================================

#[derive(Debug, Serialize)]
pub struct VllmChatRequest {
    pub model: String,
    pub messages: Vec<VllmChatMessage>,
    pub max_tokens: usize,
    pub temperature: f32,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub stream: bool,
    pub skip_special_tokens: bool,
    pub vllm_xargs: VllmXargs,
}

#[derive(Debug, Serialize)]
pub struct VllmXargs {
    pub ngram_size: usize,
    pub window_size: usize,
}

#[derive(Debug, Serialize)]
pub struct VllmChatMessage {
    pub role: String,
    pub content: Vec<VllmContentPart>,
}

#[derive(Debug, Serialize)]
#[serde(tag = "type")]
pub enum VllmContentPart {
    #[serde(rename = "text")]
    Text { text: String },
    #[serde(rename = "image_url")]
    ImageUrl { image_url: VllmImageUrl },
}

#[derive(Debug, Serialize)]
pub struct VllmImageUrl {
    pub url: String,
}

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
pub struct VllmChatResponse {
    pub id: Option<String>,
    pub choices: Vec<VllmChoice>,
}

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
pub struct VllmChoice {
    pub index: Option<usize>,
    pub message: VllmResponseMessage,
}

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
pub struct VllmResponseMessage {
    pub role: Option<String>,
    pub content: String,
}
