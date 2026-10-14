<div align="center">
  <h1>📄 Production-Grade SLM-Powered OCR Course 📄</h1>
  <h3>Build a self-scaling, event-driven OCR pipeline on Azure Kubernetes Service with Qwen 3.5 + the GLM-OCR SDK</h3>
</div>

</br>

<p align="center">
    <img src="images/arch_overview.jpeg" alt="Architecture" width="700">
</p>

## Table of Contents

- [Course Overview](#course-overview)
- [Who is this course for?](#who-is-this-course-for)
- [🚀 Quickstart: Run the Whole System](#-quickstart-run-the-whole-system)
- [📚 Following the Course](#-following-the-course)
- [🗂️ Repository Structure](#️-repository-structure)
- [📖 Documentation](#-documentation)
- [The tech stack](#the-tech-stack)
- [Contributors](#contributors)
- [License](#license)

## Course Overview

Most OCR tutorials stop at "call an API and get some text back." This isn't that.

Instead, we're building a **production-grade, self-scaling Visual Document Understanding pipeline**, deployed for real on Kubernetes (AKS), that goes far beyond flat text extraction: it reasons about charts, tables, and layout the way a human reader would — powered by a Small Language Model (**Qwen 3.5**) instead of a bloated frontier model.

By the end of this course, you'll have your own event-driven OCR system capable of:

* 🧠 Understanding documents, not just transcribing them — charts, tables, handwriting, and contextual reasoning via **Qwen 3.5 (2B)**
* ⚡ Serving generative OCR at **1.86 pages/second** with vLLM's continuous batching, PagedAttention, and Multi-Token Prediction (MTP)
* 🦀 Ingesting files through a high-concurrency **Rust (Axum) gateway**, decoupled from GPU inference via Redis
* 🔄 Running a **zero-copy, `/dev/shm`-based** document handoff between the layout encoder and the inference engine
* ☸️ Auto-scaling T4 (layout) and A100 (inference) node pools independently with **KEDA**, from zero to bursting load
* 🔒 Locking the whole pipeline behind an **Internal Load Balancer + Azure API Management** gateway, with zero public exposure
* 🤖 Wrapping the pipeline as an **MCP server** for native use by AI agents, including Claude Code

Excited? Let's get started!

---

<table style="border-collapse: collapse; border: none;">
  <tr style="border: none;">
    <td width="20%" style="border: none;">
      <a href="https://theneuralmaze.substack.com/" aria-label="The Neural Maze">
        <img src="https://avatars.githubusercontent.com/u/151655127?s=400&u=2fff53e8c195ac155e5c8ee65c6ba683a72e655f&v=4" alt="The Neural Maze Logo" width="150"/>
      </a>
    </td>
    <td width="80%" style="border: none;">
      <div>
        <h2>📬 Stay Updated</h2>
        <p><b><a href="https://theneuralmaze.substack.com/">Join The Neural Maze</a></b> and learn to build AI Systems that actually work, from principles to production. Every Wednesday, directly to your inbox. Don't miss out!</p>
      </div>
    </td>
  </tr>
</table>

<p align="center">
  <a href="https://theneuralmaze.substack.com/">
    <img src="https://img.shields.io/static/v1?label&logo=substack&message=Subscribe%20Now&style=for-the-badge&color=black&scale=2" alt="Subscribe Now" height="40">
  </a>
</p>

---

## Who is this course for?

This course is for ML/AI Engineers and Platform Engineers who already know how to call an OCR API and want to know **what it takes to run one in production**: GPU node pools, autoscaling economics, network security, and the systems-design tradeoffs behind serving a generative model at throughput.

## 🚀 Quickstart: Run the Whole System

You need an Azure subscription with **A100 and T4 GPU quota** (see [Week 0](course/week0_cloud_setup/README.md)) and `az`, `kubectl`, `helm`, `curl` and `python3` installed locally.

```bash
cp .env.example .env    # set SUBSCRIPTION_ID and a globally unique ACR_NAME
make up                 # AKS + node pools + KEDA/Prometheus + weights + images + deploy
make test               # OCR samples/sample_image.png end to end
make down               # delete every Azure resource when you're done
```

`make up` is idempotent, so you can re-run it after a failure and it picks up where it left off. Run `make help` to list every target, grouped by stage:

| Stage | Targets |
| :--- | :--- |
| Setup | `check-cli`, `auth`, `check-quota` |
| Infrastructure | `infra`, `nodepools`, `verify-gpu` |
| Platform | `platform` (KEDA + Prometheus/Grafana) |
| Model weights | `ingest-weights`, `ingest-wait`, `ingest-logs` |
| Images | `build` (or `build-vlm`, `build-api`, `build-worker`, `build-mcp`) |
| Deploy | `deploy`, `undeploy`, `restart`, `render` |
| Observe | `status`, `logs-*`, `port-forward-*`, `grafana-password` |
| Claude Code | `port-forward-mcp`, `mcp-register`, `mcp-local` |
| API gateway (optional) | `apim` |
| Cost | `scale-to-zero`, `down` |

> 💸 GPU nodes are billed by the hour. Run `make scale-to-zero` when you pause and `make down` when you finish.

## 📚 Following the Course

The course is a 6-week, hands-on cohort (plus a Week 0 setup bonus). Each week combines a **deep-dive article** (Wednesdays), a short list of **code to read** in this repo, the **`make` targets** for that layer, and **deliverables** you bring to the **Friday office hours**.

👉 **Start here: [`course/README.md`](course/README.md)**

| Week | Focus | Hands-on |
| :--- | :--- | :--- |
| ☁️ [**0. Cloud Setup**](course/week0_cloud_setup/README.md) | Azure account, Pay-As-You-Go, GPU quota | `make auth`, `make check-quota` |
| ⛵ [**1. Kubernetes for AI Systems**](course/week1_kubernetes_for_ai/README.md) | Pods, Services, node pools, taints, GPU scheduling | AKS with CPU, T4 & A100 pools: `make infra nodepools` |
| 🧠 [**2. SOTA OCR Approaches & VDU**](course/week2_ocr_approaches/README.md) | Single-stage models vs. our layout-first pipeline | Dissecting the GLM-OCR SDK pipeline config |
| ⚡ [**3. vLLM Inference Engine**](course/week3_vllm_inference/README.md) | Continuous batching, PagedAttention, chunked prefill | Qwen 3.5 2B on vLLM and tuning `MAX_NUM_BATCHED_TOKENS` (+ [lab](course/labs/week3_unlimited_ocr_vllm/README.md)) |
| 🦀 [**4. Rust Ingest Gateway**](course/week4_rust_gateway/README.md) | High-concurrency gateways, async Rust | The Axum Producer: 10MB limit, atomic `HSET` to Redis (+ [lab](course/labs/week4_sync_rust_gateway/README.md)) |
| 🔄 [**5. Async Architectures & Zero-Copy Ingestion**](course/week5_async_architecture/README.md) | Queues, dynamic batching, RAM-disk handoff | The batching worker, `/dev/shm`, scale-to-zero with KEDA: `make test` |
| 🛡️ [**6. Enterprise Gateways & Claude Code MCP**](course/week6_apim_mcp/README.md) | Security boundaries, JWT, rate limiting, agents | APIM in front of the pipeline + an MCP server for Claude Code |

<p align="center">
  <img src="images/week1_infra_overview.png" width="32%" alt="Week 1">
  <img src="images/week2_ocr_approaches.png" width="32%" alt="Week 2">
  <img src="images/week3_vllm_optimization.png" width="32%" alt="Week 3">
</p>
<p align="center">
  <img src="images/week4_rust_gateway.png" width="32%" alt="Week 4">
  <img src="images/week5_async_architecture.png" width="32%" alt="Week 5">
  <img src="images/week6_apim_mcp.png" width="32%" alt="Week 6">
</p>

## 🗂️ Repository Structure

```text
.
├── Makefile                 # The single entrypoint: make up / test / down
├── .env.example             # Your configuration (copy to .env)
├── services/                # The four components of the final system
│   ├── server/              #   vLLM inference engine image (Qwen 3.5 2B)
│   ├── client_rt_producer/  #   Rust (Axum) ingest gateway → Redis
│   ├── client_rt_consumer/  #   Python batching worker (GLM-OCR SDK + PP-DocLayoutV3)
│   └── mcp_server/          #   MCP server exposing the pipeline to AI agents
├── deploy/
│   ├── k8s/                 # Kustomize manifests: apps, autoscaling, networking, weights
│   └── apim/                # Azure API Management policy (JWT + rate limiting)
├── scripts/                 # End-to-end test client
├── samples/                 # Sample documents
├── course/                  # The learning path: one page per week + optional labs
├── docs/                    # Setup, deployment, API and architecture references
└── images/                  # Diagrams and figures
```

## 📖 Documentation

| Doc | What it covers |
| :--- | :--- |
| [`docs/azure_onboarding.md`](docs/azure_onboarding.md) | Creating an Azure account, credits, CLI login |
| [`docs/azure_gpu_prereqs.md`](docs/azure_gpu_prereqs.md) | Pay-As-You-Go upgrade and A100/T4 quota requests |
| [`docs/deployment_guide.md`](docs/deployment_guide.md) | Every `make up` step as raw CLI commands, plus APIM and MCP |
| [`docs/api.md`](docs/api.md) | The async `POST /process` / `GET /status/{task_id}` contract |
| [`docs/architecture.md`](docs/architecture.md) | Design rationale: SLM advantage, throughput tuning, document workflow, scaling philosophy |

## The tech stack

| Technology | Description |
| :--- | :--- |
| **Qwen 3.5 (2B)** | The Small Language Model powering generative OCR and document reasoning. |
| **vLLM** | High-throughput inference engine — continuous batching, PagedAttention, chunked prefill, and Multi-Token Prediction (MTP). |
| **GLM-OCR SDK + PP-DocLayoutV3** | Deterministic layout detection layer that pre-encodes semantic regions before generative inference. |
| **Rust (Axum)** | The high-concurrency ingest gateway (`client_rt_producer`) that receives files and queues tasks. |
| **MCP (FastMCP)** | Exposes the pipeline as a `parse_document` tool for AI agents such as Claude Code. |
| **Redis** | Decoupled state store between the API and the GPU-heavy inference pipeline. |
| **Kubernetes (AKS)** | The orchestration layer for CPU, T4, and A100 node pools. |
| **KEDA** | Event-driven autoscaling — scale-on-queue-depth for the layout pool, scale-on-waiting-request for the inference pool. |
| **Azure API Management** | Enterprise API gateway enforcing JWT auth, rate limiting, and zero public exposure. |

## Contributors

<table>
  <tr>
    <td align="center"><img src="https://github.com/MichaelisTrofficus.png" width="100" style="border-radius:50%;"/></td>
    <td>
      <strong>Miguel Otero Pedrido | Senior ML / AI Engineer </strong><br />
      <i>Founder of The Neural Maze. Just a guy who builds AI Systems that actually work.</i><br /><br />
      <a href="https://www.linkedin.com/in/migueloteropedrido/">LinkedIn</a><br />
      <a href="https://www.youtube.com/@TheNeuralMaze">YouTube</a><br />
      <a href="https://theneuralmaze.substack.com/">The Neural Maze Newsletter</a>
    </td>
  </tr>
  <tr>
    <td align="center"><img src="https://github.com/hedrergudene.png" width="100" style="border-radius:50%;"/></td>
    <td>
      <strong>Antonio Zarauz Moreno | Cognitive-AI R&D / AI Engineer</strong><br />
      <i>Doesn't build AI wrappers — builds the infrastructure that makes them profitable, from PoC to thousands of concurrent users.</i><br /><br />
      <a href="https://www.linkedin.com/in/antonio-zarauz-moreno/">LinkedIn</a>
    </td>
  </tr>
</table>

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
