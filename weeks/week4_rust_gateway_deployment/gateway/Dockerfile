# ==============================================================================
# 🦀 STAGE 1: Build the Rust Application Binary
# ==============================================================================
FROM rust:bookworm AS builder

WORKDIR /usr/src/ocr-gateway

# 1. Pre-fetch and cache dependencies
COPY Cargo.toml Cargo.lock* ./
RUN mkdir src && echo "fn main() {}" > src/main.rs
RUN cargo build --release
RUN rm -rf src

# 2. Copy application source code and compile optimized release binary
COPY src ./src
RUN touch src/main.rs
RUN cargo build --release
RUN strip target/release/ocr-gateway

# ==============================================================================
# 🚀 STAGE 2: Minimal Production Runtime
# ==============================================================================
FROM debian:bookworm-slim AS runtime

# Install runtime dependencies: poppler-utils for PDF rendering, SSL certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    ca-certificates \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root system user
RUN useradd -m -u 1000 -U appuser

WORKDIR /app
COPY --from=builder /usr/src/ocr-gateway/target/release/ocr-gateway /app/ocr-gateway

USER appuser

ENV PORT=3000 \
    VLLM_BASE_URL="http://ocr-vlm-service:8000" \
    MODEL_ID="baidu/Unlimited-OCR" \
    PDF_MAX_SIZE=40 \
    DEFAULT_BATCH_SIZE=4 \
    RUST_LOG="info,tower_http=info"

EXPOSE 3000

ENTRYPOINT ["/app/ocr-gateway"]
