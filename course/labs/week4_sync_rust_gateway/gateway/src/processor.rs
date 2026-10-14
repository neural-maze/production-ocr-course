use anyhow::{bail, Context, Result};
use base64::Engine;
use regex::Regex;
use reqwest::Client;
use std::sync::Arc;
use tokio_stream::Stream;
use tracing::{debug, info};

use crate::config::AppConfig;
use crate::models::{
    BoundingBox, OcrStreamEvent, PageResult, ProcessResponse, VllmChatMessage, VllmChatRequest,
    VllmChatResponse, VllmContentPart, VllmImageUrl, VllmXargs,
};
use crate::pdf::{clean_and_decode_base64, detect_document_type, rasterize_pdf_to_images, DocumentType};

/// Incremental State Machine for parsing bounding box tags & text in streaming chunks
pub struct StreamingGroundingParser {
    buffer: String,
    current_box_id: usize,
    in_det_tag: bool,
    num_regex: Regex,
}

impl StreamingGroundingParser {
    pub fn new() -> Self {
        Self {
            buffer: String::new(),
            current_box_id: 0,
            in_det_tag: false,
            num_regex: Regex::new(r"\d+").unwrap(),
        }
    }

    /// Feeds a new text delta chunk into the parser and returns any ready stream events
    pub fn feed(&mut self, chunk: &str, page_number: usize) -> Vec<OcrStreamEvent> {
        self.buffer.push_str(chunk);
        let mut events = Vec::new();

        loop {
            if !self.in_det_tag {
                // Search for "<|det|>" in buffer
                if let Some(pos) = self.buffer.find("<|det|>") {
                    let pre_text = &self.buffer[..pos];
                    let clean = pre_text.replace("<|ref|>", "").replace("<|/ref|>", "");
                    if !clean.is_empty() {
                        events.push(OcrStreamEvent::Token {
                            page_number,
                            box_id: self.current_box_id,
                            text: clean,
                        });
                    }
                    self.buffer.drain(..pos + 7);
                    self.in_det_tag = true;
                } else {
                    // Check if buffer ends with a partial "<|det|>" prefix to avoid splitting
                    let mut keep_len = 0;
                    for prefix in &["<", "<|", "<|d", "<|de", "<|det", "<|det|"] {
                        if self.buffer.ends_with(prefix) {
                            keep_len = prefix.len();
                            break;
                        }
                    }
                    let emit_len = self.buffer.len().saturating_sub(keep_len);
                    if emit_len > 0 {
                        let to_emit = self.buffer[..emit_len]
                            .replace("<|ref|>", "")
                            .replace("<|/ref|>", "");
                        if !to_emit.is_empty() {
                            events.push(OcrStreamEvent::Token {
                                page_number,
                                box_id: self.current_box_id,
                                text: to_emit,
                            });
                        }
                        self.buffer.drain(..emit_len);
                    }
                    break;
                }
            } else {
                // Inside <|det|> tag: look for "<|/det|>"
                if let Some(pos) = self.buffer.find("<|/det|>") {
                    let inside = &self.buffer[..pos];
                    let nums: Vec<u32> = self.num_regex
                        .find_iter(inside)
                        .filter_map(|n| n.as_str().parse::<u32>().ok())
                        .collect();

                    if nums.len() >= 4 {
                        let xmin = nums[0].min(1000);
                        let ymin = nums[1].min(1000);
                        let xmax = nums[2].min(1000);
                        let ymax = nums[3].min(1000);

                        let label_part = inside.split('[').next().unwrap_or("").trim();
                        let label = if label_part.is_empty() {
                            "text".to_string()
                        } else {
                            label_part.to_lowercase()
                        };

                        self.current_box_id += 1;
                        events.push(OcrStreamEvent::Bbox {
                            page_number,
                            box_id: self.current_box_id,
                            label,
                            xmin: xmin.min(xmax),
                            ymin: ymin.min(ymax),
                            xmax: xmax.max(xmin),
                            ymax: ymax.max(ymin),
                        });
                    }

                    self.buffer.drain(..pos + 8);
                    self.in_det_tag = false;
                } else {
                    break;
                }
            }
        }

        events
    }

    /// Flush any remaining buffer at end of stream
    pub fn flush(&mut self, page_number: usize) -> Vec<OcrStreamEvent> {
        let mut events = Vec::new();
        if !self.buffer.is_empty() && !self.in_det_tag {
            let clean = self.buffer.replace("<|ref|>", "").replace("<|/ref|>", "");
            if !clean.is_empty() {
                events.push(OcrStreamEvent::Token {
                    page_number,
                    box_id: self.current_box_id,
                    text: clean,
                });
            }
        }
        self.buffer.clear();
        events
    }
}

pub struct DocumentProcessor {
    config: Arc<AppConfig>,
    http_client: Client,
    det_regex: Regex,
    ref_regex: Regex,
    num_regex: Regex,
}

impl DocumentProcessor {
    pub fn new(config: Arc<AppConfig>, http_client: Client) -> Result<Self> {
        let det_regex = Regex::new(r"<\|det\|>[\s\S]*?<\|/det\|>")
            .context("Failed to compile det_regex")?;
        let ref_regex = Regex::new(r"<\|ref\|>(?s)(.*?)<\|/ref\|>")
            .context("Failed to compile ref_regex")?;
        let num_regex = Regex::new(r"\d+")
            .context("Failed to compile num_regex")?;

        Ok(Self {
            config,
            http_client,
            det_regex,
            ref_regex,
            num_regex,
        })
    }

    /// Strips `<|det|>` bounding boxes and unwraps `<|ref|>` text content.
    pub fn sanitize_ocr_markdown(&self, raw: &str) -> String {
        let stripped_det = self.det_regex.replace_all(raw, "");
        let unwrapped_ref = self.ref_regex.replace_all(&stripped_det, "$1");
        unwrapped_ref.trim().to_string()
    }

    /// Extracts structured bounding boxes from model completion string.
    pub fn extract_bboxes(&self, raw: &str) -> Vec<BoundingBox> {
        let mut bboxes = Vec::new();
        let matches: Vec<_> = self.det_regex.find_iter(raw).collect();

        for (i, m) in matches.iter().enumerate() {
            let full_match = m.as_str();
            let inside = if full_match.len() >= 15 {
                &full_match[7..full_match.len() - 8]
            } else {
                ""
            };

            let nums: Vec<u32> = self.num_regex
                .find_iter(inside)
                .filter_map(|n| n.as_str().parse::<u32>().ok())
                .collect();

            if nums.len() >= 4 {
                let xmin = nums[0].min(1000);
                let ymin = nums[1].min(1000);
                let xmax = nums[2].min(1000);
                let ymax = nums[3].min(1000);

                let label_part = inside.split('[').next().unwrap_or("").trim();
                let label = if label_part.is_empty() {
                    "text".to_string()
                } else {
                    label_part.to_lowercase()
                };

                let text_start = m.end();
                let text_end = if i + 1 < matches.len() {
                    matches[i + 1].start()
                } else {
                    raw.len()
                };

                let text_chunk = &raw[text_start..text_end];
                let clean_text = text_chunk
                    .replace("<|ref|>", "")
                    .replace("<|/ref|>", "")
                    .trim()
                    .to_string();

                bboxes.push(BoundingBox {
                    label,
                    xmin: xmin.min(xmax),
                    ymin: ymin.min(ymax),
                    xmax: xmax.max(xmin),
                    ymax: ymax.max(ymin),
                    text: clean_text,
                });
            }
        }

        bboxes
    }

    /// Primary processing pipeline handling images and multi-page PDFs (Synchronous).
    pub async fn process_document(
        &self,
        raw_b64_file: &str,
        requested_batch_size: Option<usize>,
        requested_concurrency: Option<usize>,
    ) -> Result<ProcessResponse> {
        let start_time = std::time::Instant::now();

        let file_bytes = clean_and_decode_base64(raw_b64_file)
            .context("Failed to decode input document as Base64")?;

        let doc_type = detect_document_type(&file_bytes);
        info!("Detected document type: {:?}", doc_type);

        match doc_type {
            DocumentType::Image(mime) => {
                let b64_encoded = base64::engine::general_purpose::STANDARD.encode(&file_bytes);
                let data_uri = format!("data:{};base64,{}", mime, b64_encoded);

                let raw_ocr = self.query_vllm_batch(&[data_uri.clone()], 128, 8192).await?;
                let clean_markdown = self.sanitize_ocr_markdown(&raw_ocr);
                let bboxes = self.extract_bboxes(&raw_ocr);

                let page_result = PageResult {
                    page_number: 1,
                    image_data_uri: Some(data_uri),
                    markdown: clean_markdown.clone(),
                    raw_text: raw_ocr,
                    bboxes: bboxes.clone(),
                };

                Ok(ProcessResponse {
                    markdown: clean_markdown,
                    total_pages: 1,
                    batches_processed: 1,
                    latency_ms: start_time.elapsed().as_millis(),
                    document_type: mime,
                    pages: vec![page_result],
                    bboxes,
                })
            }

            DocumentType::Pdf => {
                let batch_size = requested_batch_size
                    .unwrap_or(self.config.default_batch_size)
                    .clamp(1, 10);

                let page_images = rasterize_pdf_to_images(&file_bytes, self.config.pdf_max_size)?;
                let total_pages = page_images.len();

                if total_pages == 0 {
                    bail!("PDF contained no rasterizable pages");
                }

                let num_batches = (total_pages + batch_size - 1) / batch_size;
                let concurrency = if batch_size >= total_pages {
                    1
                } else {
                    requested_concurrency.unwrap_or(1).clamp(1, 8).min(num_batches)
                };

                info!(
                    "Processing PDF with total_pages={}, batch_size={}, num_batches={}, concurrency={}",
                    total_pages, batch_size, num_batches, concurrency
                );

                let chunks: Vec<(usize, Vec<String>, usize)> = page_images
                    .chunks(batch_size)
                    .enumerate()
                    .map(|(chunk_idx, chunk)| {
                        let data_uris: Vec<String> = chunk
                            .iter()
                            .map(|img_bytes| {
                                let b64 = base64::engine::general_purpose::STANDARD.encode(img_bytes);
                                format!("data:image/png;base64,{}", b64)
                            })
                            .collect();
                        (chunk_idx, data_uris, chunk.len())
                    })
                    .collect();

                use futures_util::stream::{self, StreamExt};

                let results: Vec<Result<(usize, Vec<String>, String, String, Vec<BoundingBox>)>> = stream::iter(chunks)
                    .map(|(chunk_idx, data_uris, chunk_len)| {
                        let data_uris_clone = data_uris.clone();
                        async move {
                            let max_tokens = if chunk_len > 1 { 16384 } else { 8192 };
                            let raw_chunk_ocr = self.query_vllm_batch(&data_uris_clone, 1024, max_tokens).await?;
                            let clean_chunk_md = self.sanitize_ocr_markdown(&raw_chunk_ocr);
                            let chunk_bboxes = self.extract_bboxes(&raw_chunk_ocr);
                            Ok((chunk_idx, data_uris, raw_chunk_ocr, clean_chunk_md, chunk_bboxes))
                        }
                    })
                    .buffered(concurrency)
                    .collect()
                    .await;

                let mut all_pages = Vec::with_capacity(total_pages);
                let mut batch_markdowns = Vec::new();
                let mut all_bboxes = Vec::new();
                let batches_count = results.len();

                for res in results {
                    let (chunk_idx, data_uris, raw_chunk_ocr, clean_chunk_md, chunk_bboxes) = res?;
                    if !clean_chunk_md.is_empty() {
                        batch_markdowns.push(clean_chunk_md.clone());
                    }

                    for (page_offset, data_uri) in data_uris.into_iter().enumerate() {
                        let page_num = chunk_idx * batch_size + page_offset + 1;
                        all_pages.push(PageResult {
                            page_number: page_num,
                            image_data_uri: Some(data_uri),
                            markdown: clean_chunk_md.clone(),
                            raw_text: raw_chunk_ocr.clone(),
                            bboxes: chunk_bboxes.clone(),
                        });
                    }

                    all_bboxes.extend(chunk_bboxes);
                }

                let full_markdown = batch_markdowns.join("\n\n---\n\n");

                Ok(ProcessResponse {
                    markdown: full_markdown,
                    total_pages,
                    batches_processed: batches_count,
                    latency_ms: start_time.elapsed().as_millis(),
                    document_type: "application/pdf".to_string(),
                    pages: all_pages,
                    bboxes: all_bboxes,
                })
            }

            DocumentType::Unknown => {
                bail!(
                    "Unsupported document format. Please supply a valid PDF, PNG, JPEG, WebP, TIFF, or BMP file."
                );
            }
        }
    }

    /// Real-Time Streaming OCR Pipeline using SSE with concurrent batch support
    pub fn process_document_stream(
        self: Arc<Self>,
        raw_b64_file: String,
        requested_batch_size: Option<usize>,
        requested_concurrency: Option<usize>,
    ) -> impl Stream<Item = OcrStreamEvent> + Send + 'static {
        async_stream::stream! {
            let start_time = std::time::Instant::now();

            let file_bytes = match clean_and_decode_base64(&raw_b64_file) {
                Ok(b) => b,
                Err(e) => {
                    yield OcrStreamEvent::Error { error: format!("Failed to decode base64: {:#}", e) };
                    return;
                }
            };

            let doc_type = detect_document_type(&file_bytes);
            match doc_type {
                DocumentType::Image(mime) => {
                    // Single image: concurrency is always 1
                    let b64_encoded = base64::engine::general_purpose::STANDARD.encode(&file_bytes);
                    let data_uri = format!("data:{};base64,{}", mime, b64_encoded);

                    yield OcrStreamEvent::PageStart {
                        page_number: 1,
                        total_pages: 1,
                        image_data_uri: Some(data_uri.clone()),
                    };

                    let mut parser = StreamingGroundingParser::new();
                    let mut page_markdown = String::new();

                    match self.stream_vllm_batch(&[data_uri], 128, 8192).await {
                        Ok(byte_stream) => {
                            use tokio_stream::StreamExt;
                            tokio::pin!(byte_stream);

                            while let Some(chunk_res) = byte_stream.next().await {
                                match chunk_res {
                                    Ok(chunk) => {
                                        let events = parser.feed(&chunk, 1);
                                        for ev in events {
                                            if let OcrStreamEvent::Token { ref text, .. } = ev {
                                                page_markdown.push_str(text);
                                            }
                                            yield ev;
                                        }
                                    }
                                    Err(e) => {
                                        yield OcrStreamEvent::Error { error: format!("vLLM stream error: {}", e) };
                                        return;
                                    }
                                }
                            }
                            for ev in parser.flush(1) {
                                if let OcrStreamEvent::Token { ref text, .. } = ev {
                                    page_markdown.push_str(text);
                                }
                                yield ev;
                            }

                            yield OcrStreamEvent::PageDone {
                                page_number: 1,
                                markdown: page_markdown,
                            };

                            yield OcrStreamEvent::Done {
                                status: "complete".to_string(),
                                total_pages: 1,
                                latency_ms: start_time.elapsed().as_millis(),
                            };
                        }
                        Err(e) => {
                            yield OcrStreamEvent::Error { error: format!("Failed to connect to vLLM: {:#}", e) };
                        }
                    }
                }

                DocumentType::Pdf => {
                    let batch_size = requested_batch_size
                        .unwrap_or(self.config.default_batch_size)
                        .clamp(1, 10);

                    let page_images = match rasterize_pdf_to_images(&file_bytes, self.config.pdf_max_size) {
                        Ok(imgs) => imgs,
                        Err(e) => {
                            yield OcrStreamEvent::Error { error: format!("PDF rasterization failed: {:#}", e) };
                            return;
                        }
                    };

                    let total_pages = page_images.len();
                    if total_pages == 0 {
                        yield OcrStreamEvent::Error { error: "PDF contains no pages".to_string() };
                        return;
                    }

                    let num_batches = (total_pages + batch_size - 1) / batch_size;
                    let concurrency = if batch_size >= total_pages {
                        1
                    } else {
                        requested_concurrency.unwrap_or(1).clamp(1, 8).min(num_batches)
                    };

                    info!(
                        "Streaming PDF with total_pages={}, batch_size={}, num_batches={}, concurrency={}",
                        total_pages, batch_size, num_batches, concurrency
                    );

                    let encoded_pages: Vec<(usize, String)> = page_images
                        .into_iter()
                        .enumerate()
                        .map(|(idx, img_bytes)| {
                            let page_num = idx + 1;
                            let b64 = base64::engine::general_purpose::STANDARD.encode(&img_bytes);
                            (page_num, format!("data:image/png;base64,{}", b64))
                        })
                        .collect();

                    for (page_num, page_data_uri) in &encoded_pages {
                        yield OcrStreamEvent::PageStart {
                            page_number: *page_num,
                            total_pages,
                            image_data_uri: Some(page_data_uri.clone()),
                        };
                    }

                    if concurrency <= 1 {
                        for (chunk_idx, chunk) in encoded_pages.chunks(batch_size).enumerate() {
                            let data_uris: Vec<String> = chunk.iter().map(|(_, uri)| uri.clone()).collect();
                            let start_page = chunk.first().map(|(p, _)| *p).unwrap_or(chunk_idx * batch_size + 1);
                            let mut parser = StreamingGroundingParser::new();
                            let mut page_markdown = String::new();

                            let max_tokens = if chunk.len() > 1 { 16384 } else { 8192 };
                            match self.stream_vllm_batch(&data_uris, 1024, max_tokens).await {
                                Ok(byte_stream) => {
                                    use tokio_stream::StreamExt;
                                    tokio::pin!(byte_stream);

                                    while let Some(chunk_res) = byte_stream.next().await {
                                        match chunk_res {
                                            Ok(chunk_text) => {
                                                let events = parser.feed(&chunk_text, start_page);
                                                for ev in events {
                                                    if let OcrStreamEvent::Token { ref text, .. } = ev {
                                                        page_markdown.push_str(text);
                                                    }
                                                    yield ev;
                                                }
                                            }
                                            Err(e) => {
                                                yield OcrStreamEvent::Error { error: format!("vLLM stream error on page {}: {}", start_page, e) };
                                            }
                                        }
                                    }
                                    for ev in parser.flush(start_page) {
                                        if let OcrStreamEvent::Token { ref text, .. } = ev {
                                            page_markdown.push_str(text);
                                        }
                                        yield ev;
                                    }

                                    yield OcrStreamEvent::PageDone {
                                        page_number: start_page,
                                        markdown: page_markdown,
                                    };
                                }
                                Err(e) => {
                                    yield OcrStreamEvent::Error { error: format!("vLLM connection failed on page {}: {:#}", start_page, e) };
                                }
                            }
                        }
                    } else {
                        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
                        let sem = Arc::new(tokio::sync::Semaphore::new(concurrency));
                        let mut join_handles = Vec::new();

                        let batches: Vec<Vec<(usize, String)>> = encoded_pages
                            .chunks(batch_size)
                            .map(|c| c.to_vec())
                            .collect();

                        for chunk in batches {
                            let processor = self.clone();
                            let tx = tx.clone();
                            let sem = sem.clone();

                            let handle = tokio::spawn(async move {
                                let _permit = match sem.acquire().await {
                                    Ok(p) => p,
                                    Err(_) => return,
                                };

                                let data_uris: Vec<String> = chunk.iter().map(|(_, uri)| uri.clone()).collect();
                                let start_page = chunk.first().map(|(p, _)| *p).unwrap_or(1);
                                let mut parser = StreamingGroundingParser::new();
                                let mut page_markdown = String::new();

                                let max_tokens = if chunk.len() > 1 { 16384 } else { 8192 };
                                match processor.stream_vllm_batch(&data_uris, 1024, max_tokens).await {
                                    Ok(byte_stream) => {
                                        use tokio_stream::StreamExt;
                                        tokio::pin!(byte_stream);

                                        while let Some(chunk_res) = byte_stream.next().await {
                                            match chunk_res {
                                                Ok(chunk_text) => {
                                                    let events = parser.feed(&chunk_text, start_page);
                                                    for ev in events {
                                                        if let OcrStreamEvent::Token { ref text, .. } = ev {
                                                            page_markdown.push_str(text);
                                                        }
                                                        let _ = tx.send(ev);
                                                    }
                                                }
                                                Err(e) => {
                                                    let _ = tx.send(OcrStreamEvent::Error { error: format!("vLLM stream error on page {}: {}", start_page, e) });
                                                }
                                            }
                                        }
                                        for ev in parser.flush(start_page) {
                                            if let OcrStreamEvent::Token { ref text, .. } = ev {
                                                page_markdown.push_str(text);
                                            }
                                            let _ = tx.send(ev);
                                        }

                                        let _ = tx.send(OcrStreamEvent::PageDone {
                                            page_number: start_page,
                                            markdown: page_markdown,
                                        });
                                    }
                                    Err(e) => {
                                        let _ = tx.send(OcrStreamEvent::Error { error: format!("vLLM connection failed on page {}: {:#}", start_page, e) });
                                    }
                                }
                            });
                            join_handles.push(handle);
                        }

                        drop(tx);

                        while let Some(ev) = rx.recv().await {
                            yield ev;
                        }

                        for handle in join_handles {
                            let _ = handle.await;
                        }
                    }

                    yield OcrStreamEvent::Done {
                        status: "complete".to_string(),
                        total_pages,
                        latency_ms: start_time.elapsed().as_millis(),
                    };
                }

                DocumentType::Unknown => {
                    yield OcrStreamEvent::Error {
                        error: "Unsupported document format. Please supply a valid PDF, PNG, JPEG, WebP, TIFF, or BMP file.".to_string(),
                    };
                }
            }
        }
    }

    /// Dispatches a multi-modal chat completion request to the vLLM server (Synchronous).
    async fn query_vllm_batch(
        &self,
        image_data_uris: &[String],
        window_size: usize,
        max_tokens: usize,
    ) -> Result<String> {
        let endpoint = format!("{}/v1/chat/completions", self.config.vllm_base_url);

        let mut content_parts = vec![VllmContentPart::Text {
            text: "<image>document parsing.".to_string(),
        }];

        for uri in image_data_uris {
            content_parts.push(VllmContentPart::ImageUrl {
                image_url: VllmImageUrl { url: uri.clone() },
            });
        }

        let request_payload = VllmChatRequest {
            model: self.config.model_id.clone(),
            messages: vec![VllmChatMessage {
                role: "user".to_string(),
                content: content_parts,
            }],
            max_tokens,
            temperature: 0.0,
            stream: false,
            skip_special_tokens: false,
            vllm_xargs: VllmXargs {
                ngram_size: 35,
                window_size,
            },
        };

        let response = self
            .http_client
            .post(&endpoint)
            .json(&request_payload)
            .send()
            .await
            .with_context(|| format!("Failed to connect to vLLM endpoint at {}", endpoint))?;

        if !response.status().is_success() {
            let status = response.status();
            let error_text = response.text().await.unwrap_or_default();
            bail!("vLLM server returned error {}: {}", status, error_text);
        }

        let chat_response: VllmChatResponse = response
            .json()
            .await
            .context("Failed to deserialize vLLM chat response JSON")?;

        let text = chat_response
            .choices
            .first()
            .map(|c| c.message.content.clone())
            .unwrap_or_default();

        Ok(text)
    }

    /// Dispatches a streaming multi-modal chat completion request to vLLM.
    async fn stream_vllm_batch(
        &self,
        image_data_uris: &[String],
        window_size: usize,
        max_tokens: usize,
    ) -> Result<impl Stream<Item = Result<String, anyhow::Error>>> {
        let endpoint = format!("{}/v1/chat/completions", self.config.vllm_base_url);

        let mut content_parts = vec![VllmContentPart::Text {
            text: "<image>document parsing.".to_string(),
        }];

        for uri in image_data_uris {
            content_parts.push(VllmContentPart::ImageUrl {
                image_url: VllmImageUrl { url: uri.clone() },
            });
        }

        let request_payload = VllmChatRequest {
            model: self.config.model_id.clone(),
            messages: vec![VllmChatMessage {
                role: "user".to_string(),
                content: content_parts,
            }],
            max_tokens,
            temperature: 0.0,
            stream: true,
            skip_special_tokens: false,
            vllm_xargs: VllmXargs {
                ngram_size: 35,
                window_size,
            },
        };

        let response = self
            .http_client
            .post(&endpoint)
            .json(&request_payload)
            .send()
            .await
            .with_context(|| format!("Failed to connect to vLLM endpoint at {}", endpoint))?;

        if !response.status().is_success() {
            let status = response.status();
            let error_text = response.text().await.unwrap_or_default();
            bail!("vLLM server returned error {}: {}", status, error_text);
        }

        let bytes_stream = response.bytes_stream();

        let text_stream = async_stream::stream! {
            use tokio_stream::StreamExt;
            let mut line_buffer = String::new();
            let mut stream = bytes_stream;

            while let Some(chunk_res) = stream.next().await {
                match chunk_res {
                    Ok(bytes) => {
                        let text = String::from_utf8_lossy(&bytes);
                        line_buffer.push_str(&text);

                        while let Some(pos) = line_buffer.find('\n') {
                            let line = line_buffer[..pos].trim().to_string();
                            line_buffer.drain(..pos + 1);

                            if line.starts_with("data: ") {
                                let data_str = line[6..].trim();
                                if data_str == "[DONE]" {
                                    break;
                                }
                                if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(data_str) {
                                    if let Some(choices) = parsed.get("choices").and_then(|c| c.as_array()) {
                                        if let Some(delta) = choices.first().and_then(|c| c.get("delta")) {
                                            if let Some(content) = delta.get("content").and_then(|c| c.as_str()) {
                                                yield Ok(content.to_string());
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Err(e) => {
                        yield Err(anyhow::anyhow!("Byte stream error: {}", e));
                        break;
                    }
                }
            }
        };

        Ok(text_stream)
    }
}
