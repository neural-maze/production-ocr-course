# --- Stage 1: Build Recipe ---
FROM rust:1.88-alpine as chef
RUN apk add --no-cache musl-dev
WORKDIR /app
RUN cargo install cargo-chef --locked

# --- Stage 2: Prepare Planner ---
FROM chef as planner
COPY . .
RUN cargo chef prepare --recipe-path recipe.json

# --- Stage 3: Build Dependencies ---
FROM chef as builder 
COPY --from=planner /app/recipe.json recipe.json
RUN cargo chef cook --release --recipe-path recipe.json
COPY . .
RUN cargo build --release

# --- Stage 4: Final Image ---
FROM alpine:3.20
RUN apk add --no-cache libgcc
WORKDIR /app
COPY --from=builder /app/target/release/ocr-producer-rust /app/producer
EXPOSE 5000
CMD ["/app/producer"]

