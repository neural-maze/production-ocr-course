# 📚 Course Guide: How to Follow the Production OCR Course

This folder is the **learning path**. The code you run lives in one place, the final system at the repo root (`services/`, `deploy/`, `Makefile`). Each week below explains one layer of that system: you read the article, run the `make` targets for that layer, and hand in the deliverables.

```text
course/
├── week0_cloud_setup/          # Azure account, credits, GPU quota
├── week1_kubernetes_for_ai/    # AKS, node pools, taints, GPU scheduling
├── week2_ocr_approaches/       # Single-stage vs. layout-first OCR, GLM-OCR SDK
├── week3_vllm_inference/       # vLLM engine, batching knobs, model weights
├── week4_rust_gateway/         # Axum ingest gateway in front of Redis
├── week5_async_architecture/   # Redis queue, dynamic batching worker, KEDA
├── week6_apim_mcp/             # APIM perimeter + MCP server for Claude Code
└── labs/                       # Optional standalone variants built during the course
```

## 🔁 The Weekly Loop

Every week has the same four steps:

1. **📘 Read the article** (published Wednesdays on [The Neural Maze](https://theneuralmaze.substack.com/)). It covers the systems design and the "why".
2. **🔍 Read the code** listed in the week's *Where it lives* table. Each week touches only a few files.
3. **🛠️ Run the hands-on** `make` targets from the repo root. Weeks build on each other, so the cluster you create in Week 1 is the one you deploy to in Week 6.
4. **✅ Complete the deliverables.** Each one is a concrete, checkable output (a command result, a measurement, a short write-up). Bring them to **🎙️ Friday office hours**.

> 💡 **In a hurry?** You don't need the course to run the system. `make up && make test` from the repo root deploys the whole thing. The course explains what you just deployed.

## 🗺️ Syllabus

| Week | Topic | You build | Key deliverable |
| :--- | :--- | :--- | :--- |
| [0](week0_cloud_setup/README.md) | ☁️ Cloud setup | Azure subscription with GPU quota | `make check-quota` shows A100 + T4 quota |
| [1](week1_kubernetes_for_ai/README.md) | ⛵ Kubernetes for AI systems | AKS cluster with CPU, T4 and A100 pools | `make verify-gpu` shows allocatable GPUs |
| [2](week2_ocr_approaches/README.md) | 🧠 SOTA OCR approaches & VDU | Understanding the layout-first pipeline | Annotated walkthrough of the worker config |
| [3](week3_vllm_inference/README.md) | ⚡ vLLM inference engine | Qwen 3.5 2B served on the A100 | A direct OpenAI-API OCR call + a tuning experiment |
| [4](week4_rust_gateway/README.md) | 🦀 Rust ingest gateway | Axum Producer writing tasks to Redis | `POST /process` returns a `task_id`; 10MB limit enforced |
| [5](week5_async_architecture/README.md) | 🔄 Async architecture & zero-copy ingestion | Batching worker + KEDA autoscaling | `make test` round trip + observed scale-from-zero |
| [6](week6_apim_mcp/README.md) | 🛡️ Enterprise gateways & Claude Code MCP | APIM perimeter + MCP server | Claude Code parsing a document through your pipeline |

## 🧪 Labs

The [`labs/`](labs/) folder holds two self-contained variants that aren't part of the final system but are worth building to compare against it:

* [`labs/week3_unlimited_ocr_vllm`](labs/week3_unlimited_ocr_vllm/README.md): serving the single-stage `baidu/Unlimited-OCR` model on vLLM.
* [`labs/week4_sync_rust_gateway`](labs/week4_sync_rust_gateway/README.md): a synchronous Rust gateway that rasterizes PDFs and calls vLLM directly (no queue).

Each lab has its own `Makefile` and is run from inside its folder.

## 💸 Before You Start: Cost

A100 and T4 nodes are expensive. Two habits will save you money:

* Run `make scale-to-zero` whenever you stop for the day. The GPU pools then drop to zero nodes when idle.
* Run `make down` when you finish the course. It deletes the whole resource group.
