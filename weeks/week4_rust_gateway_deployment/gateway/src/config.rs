use std::env;

#[derive(Clone, Debug)]
pub struct AppConfig {
    pub port: u16,
    pub vllm_base_url: String,
    pub model_id: String,
    pub pdf_max_size: usize,
    pub default_batch_size: usize,
    pub request_timeout_secs: u64,
}

impl AppConfig {
    pub fn from_env() -> Self {
        let port = env::var("PORT")
            .ok()
            .and_then(|p| p.parse::<u16>().ok())
            .unwrap_or(3000);

        let vllm_base_url = env::var("VLLM_BASE_URL")
            .unwrap_or_else(|_| "http://ocr-vlm-service:8000".to_string())
            .trim_end_matches('/')
            .to_string();

        let model_id = env::var("MODEL_ID")
            .unwrap_or_else(|_| "baidu/Unlimited-OCR".to_string());

        let pdf_max_size = env::var("PDF_MAX_SIZE")
            .ok()
            .and_then(|p| p.parse::<usize>().ok())
            .unwrap_or(40);

        let default_batch_size = env::var("DEFAULT_BATCH_SIZE")
            .ok()
            .and_then(|b| b.parse::<usize>().ok())
            .unwrap_or(4);

        let request_timeout_secs = env::var("REQUEST_TIMEOUT_SECS")
            .ok()
            .and_then(|t| t.parse::<u64>().ok())
            .unwrap_or(300);

        Self {
            port,
            vllm_base_url,
            model_id,
            pdf_max_size,
            default_batch_size,
            request_timeout_secs,
        }
    }
}
