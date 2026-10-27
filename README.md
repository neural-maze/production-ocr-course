<div align="center">
  <h1>📄 Local Durable Multi-Page OCR Pipeline 📄</h1>
  <h3>Asynchronous Document Parsing with Inngest, PostgreSQL, and Apple Silicon MLX (<code>Unlimited-OCR-2-8bit</code>) / <code>llama.cpp</code></h3>
</div>

---

## Overview

This repository provides a **local-first, durable asynchronous OCR pipeline** engineered following the harness patterns from [`neural-maze/substack-brain-course`](https://github.com/neural-maze/substack-brain-course) and the asynchronous multi-page architecture of our Production OCR Course:

* **Multi-Page One-Shot OCR (`mlx-community/Unlimited-OCR-2-8bit`)**: Uses Baidu's `Unlimited-OCR-2` (with Page-Isolated 2D RoPE `page2d` and block-sparse `DASH` attention) to parse **multiple PDF pages in a single forward pass** on Apple Silicon via `mlx-vlm` (or via a `llama.cpp` server container on Linux / Docker Desktop).
* **Durable Orchestration (`Inngest Dev Server`)**: Every document upload triggers an `ocr/document.submitted` event. Rasterization (`150 DPI`), each multi-page LLM batch (`ocr-batch-1..N`), and final assembly run as independent, retryable durable steps with `concurrency=1` to protect unified memory/VRAM.
* **Persistent Storage (`PostgreSQL` + `Inngest SQLite Volume`)**: Both PostgreSQL (`:5432`, with Adminer on `:8081`) and the Inngest Dev Server (`:8288`) use persistent Docker volumes so documents, bounding boxes, step timelines, and LLM telemetry survive restarts.
* **Minimalist Web Visualizer & LLM Telemetry (`http://localhost:8000`)**: A clean white-background UI to upload PDFs/images, inspect `<BLOCK>label[x0, y0, x1, y1]<FORK>` layout bounding boxes per page, view rendered Markdown, and trace server speed (`pages/sec`, prefill `tok/s`, decode `tok/s`, peak unified memory `GB`, and per-batch latency).

> 📚 **Looking for the Cloud Kubernetes (AKS / GKE) Course?**  
> All weekly cloud deployments (`week3`–`week6`, Rust Axum gateway, KEDA autoscalers, and vLLM manifests) are preserved in [`ocr-course/`](ocr-course/README.md).

---

## Quick Start

### 1. Configure & Launch
Ensure **Docker Desktop** is running and [`uv`](https://docs.astral.sh/uv/) is installed:

```bash
cp .env.example .env
make start
```

`make start` automatically:
1. Installs the Python environment via `uv sync`.
2. Starts **PostgreSQL** (`:5432`), **Adminer** (`:8081`), and the **Inngest Dev Server** (`:8288`) via `infra/docker-compose.yml`.
3. Initializes the database schema (`documents`, `document_pages`, `ocr_batches`).
4. Launches the FastAPI + Inngest worker with **MLX (`mlx-community/Unlimited-OCR-2-8bit`)** on macOS Apple Silicon (or `llama.cpp` on Linux).

### 2. Open the Interfaces
* **Minimalist OCR & Metrics Visualizer**: [http://localhost:8000](http://localhost:8000)
* **Inngest Durable Workflow Dashboard**: [http://localhost:8288](http://localhost:8288)
* **Adminer Database Inspector**: [http://localhost:8081](http://localhost:8081) (System: `PostgreSQL`, Server: `postgres`, Username: `ocr`, Password: `ocr`, Database: `ocr_db`)

---

## Makefile Commands

| Command | Description |
| :--- | :--- |
| `make start` | Start backing containers (`postgres`, `adminer`, `inngest`), initialize DB, and launch the app (auto-selects `mlx` on macOS or `llamacpp` on Linux) |
| `make start-mlx` | Explicitly launch with native Apple Silicon MLX (`mlx-community/Unlimited-OCR-2-8bit`) |
| `make start-linux` | Launch with the containerized `llama.cpp` server (`--profile linux`) |
| `make preload-model` | Pre-download `mlx-community/Unlimited-OCR-2-8bit` weights to your local Hugging Face cache |
| `make test` | Submit `sample_image.png` to the running pipeline and stream status + LLM speed metrics in the terminal |
| `make check` | Run `ruff` linter, `mypy` type checking, and `pytest` unit tests |
| `make stop` | Stop Docker Compose containers while keeping PostgreSQL & Inngest data volumes intact |
| `make db-clear` | Truncate all PostgreSQL tables (`documents`, `document_pages`, `ocr_batches`) while keeping the schema |
| `make reset` | Destructive reset (`docker compose down -v`) dropping all database and Inngest volumes |
| `make nuke` | Full reset + kill any lingering processes on ports `8000`, `8080`, `8081`, `8288` |

