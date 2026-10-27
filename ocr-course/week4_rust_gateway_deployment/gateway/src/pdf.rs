use anyhow::{bail, Context, Result};
use std::fs;
use std::io::Write;
use std::path::Path;
use std::process::Command;
use tempfile::tempdir;
use tracing::{debug, error, info};

#[derive(Debug, PartialEq, Eq)]
pub enum DocumentType {
    Pdf,
    Image(String), // e.g. "image/png", "image/jpeg", "image/webp"
    Unknown,
}

#[allow(dead_code)]
impl DocumentType {
    pub fn as_str(&self) -> &str {
        match self {
            DocumentType::Pdf => "application/pdf",
            DocumentType::Image(mime) => mime.as_str(),
            DocumentType::Unknown => "application/octet-stream",
        }
    }
}

/// Detects the file format from the raw binary magic bytes.
pub fn detect_document_type(bytes: &[u8]) -> DocumentType {
    if bytes.len() < 4 {
        return DocumentType::Unknown;
    }

    // PDF Magic Bytes: %PDF- (0x25 0x50 0x44 0x46 0x2D)
    if bytes.starts_with(b"%PDF-") {
        return DocumentType::Pdf;
    }

    // PNG Magic Bytes: \x89PNG\r\n\x1a\n (0x89 0x50 0x4E 0x47)
    if bytes.starts_with(&[0x89, 0x50, 0x4E, 0x47]) {
        return DocumentType::Image("image/png".to_string());
    }

    // JPEG Magic Bytes: \xFF\xD8\xFF (0xFF 0xD8 0xFF)
    if bytes.starts_with(&[0xFF, 0xD8, 0xFF]) {
        return DocumentType::Image("image/jpeg".to_string());
    }

    // WebP Magic Bytes: RIFF....WEBP (0x52 0x49 0x46 0x46 ... 0x57 0x45 0x42 0x50)
    if bytes.len() >= 12 && bytes.starts_with(b"RIFF") && &bytes[8..12] == b"WEBP" {
        return DocumentType::Image("image/webp".to_string());
    }

    // TIFF Magic Bytes: II*\0 or MM\0*
    if bytes.starts_with(&[0x49, 0x49, 0x2A, 0x00]) || bytes.starts_with(&[0x4D, 0x4D, 0x00, 0x2A]) {
        return DocumentType::Image("image/tiff".to_string());
    }

    // BMP Magic Bytes: BM
    if bytes.starts_with(b"BM") {
        return DocumentType::Image("image/bmp".to_string());
    }

    DocumentType::Unknown
}

/// Strips data URI prefixes (e.g., "data:image/png;base64,") and whitespace before decoding base64.
pub fn clean_and_decode_base64(raw_input: &str) -> Result<Vec<u8>> {
    let clean_str = if let Some(idx) = raw_input.find(";base64,") {
        &raw_input[idx + 8..]
    } else if let Some(idx) = raw_input.find(',') {
        // Fallback for data URIs without explicit ;base64
        &raw_input[idx + 1..]
    } else {
        raw_input
    };

    let trimmed = clean_str.trim().replace(['\r', '\n', ' '], "");
    use base64::Engine;
    base64::engine::general_purpose::STANDARD
        .decode(&trimmed)
        .or_else(|_| base64::engine::general_purpose::URL_SAFE.decode(&trimmed))
        .context("Failed to decode Base64 string")
}

/// Determines the total page count of a PDF using `pdfinfo` or structural parsing.
pub fn get_pdf_page_count(pdf_path: &Path) -> Result<usize> {
    let output = Command::new("pdfinfo")
        .arg(pdf_path)
        .output()
        .context("Failed to execute pdfinfo. Is poppler-utils installed?")?;

    if !output.status.success() {
        let err = String::from_utf8_lossy(&output.stderr);
        error!("pdfinfo error: {}", err);
        bail!("Failed to inspect PDF metadata: {}", err);
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    for line in stdout.lines() {
        if line.starts_with("Pages:") {
            let count_str = line.trim_start_matches("Pages:").trim();
            if let Ok(count) = count_str.parse::<usize>() {
                return Ok(count);
            }
        }
    }

    bail!("Could not determine page count from PDF metadata")
}

/// Rasterizes all pages of a PDF into memory as PNG buffers (ordered page 1..N).
pub fn rasterize_pdf_to_images(pdf_bytes: &[u8], max_pages: usize) -> Result<Vec<Vec<u8>>> {
    let temp_dir = tempdir().context("Failed to create temporary directory for PDF rasterization")?;
    let pdf_path = temp_dir.path().join("input.pdf");

    // Write PDF bytes to temp file
    let mut file = fs::File::create(&pdf_path).context("Failed to write temporary PDF file")?;
    file.write_all(pdf_bytes).context("Failed to flush PDF bytes")?;
    drop(file);

    // Verify page count against safety guardrail
    let page_count = get_pdf_page_count(&pdf_path)?;
    info!("PDF page count: {}", page_count);

    if page_count > max_pages {
        bail!(
            "PDF contains {} pages, which exceeds the maximum allowable limit of {} pages (PDF_MAX_SIZE)",
            page_count,
            max_pages
        );
    }

    if page_count == 0 {
        bail!("PDF has 0 pages");
    }

    // Execute pdftoppm to rasterize all pages to PNG at 150 DPI
    let output_prefix = temp_dir.path().join("page");
    let status = Command::new("pdftoppm")
        .arg("-png")
        .arg("-r")
        .arg("150") // 150 DPI balances OCR visual fidelity and token size
        .arg(&pdf_path)
        .arg(&output_prefix)
        .status()
        .context("Failed to execute pdftoppm. Is poppler-utils installed?")?;

    if !status.success() {
        bail!("pdftoppm rasterization failed with status: {:?}", status);
    }

    // Read generated PNG files in sorted sequential order (e.g. page-1.png, page-2.png, ...)
    let mut page_files: Vec<_> = fs::read_dir(temp_dir.path())
        .context("Failed to read temp directory")?
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.path())
        .filter(|path| {
            path.extension().and_then(|e| e.to_str()) == Some("png")
                && path.file_stem().and_then(|s| s.to_str()).unwrap_or("").starts_with("page-")
        })
        .collect();

    // Sort numerically by page index extracted from filename
    page_files.sort_by_key(|path| {
        path.file_stem()
            .and_then(|s| s.to_str())
            .and_then(|s| s.strip_prefix("page-"))
            .and_then(|s| s.parse::<usize>().ok())
            .unwrap_or(0)
    });

    debug!("Rasterized {} page image files", page_files.len());

    let mut images = Vec::with_capacity(page_files.len());
    for p in page_files {
        let img_bytes = fs::read(&p).with_context(|| format!("Failed to read image {}", p.display()))?;
        images.push(img_bytes);
    }

    Ok(images)
}
