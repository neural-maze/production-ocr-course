# Week 4: 🦀 Rust Ingest Gateway

> 📘 **Article:** _link coming soon_

<p align="center"><img src="../../images/week4_rust_gateway.png" width="600" alt="Week 4"></p>

## 🎯 What You'll Learn

* Why the ingest path is a separate, CPU-only service written in Rust (Axum + Tokio).
* Ownership, borrowing and `Arc`-shared state in an async web server.
* Guarding the system at the edge: a **10MB request body limit** and an **atomic `HSET` + `LPUSH`** handoff to Redis.

## 🔍 Where It Lives

| File | What to look at |
| :--- | :--- |
| [`services/client_rt_producer/src/main.rs`](../../services/client_rt_producer/src/main.rs) | Router (`/process`, `/status/:task_id`, `/health`), `RequestBodyLimitLayer`, the Redis writes |
| [`deploy/k8s/apps/deployment-api.yml`](../../deploy/k8s/apps/deployment-api.yml) | `ocr-api-deployment` on the `cpunp` pool |
| [`docs/api.md`](../../docs/api.md) | The async API contract |

## 🛠️ Hands-On

```bash
make build-api deploy
make logs-api
make port-forward-api     # http://localhost:5000
curl -X POST http://localhost:5000/process -F "file=@samples/sample_image.png"
```

## ✅ Deliverables

- [ ] `POST /process` returns `{"task_id": ..., "status": "queued"}`.
- [ ] `redis-cli` inside the Redis pod shows the `task:<id>` hash and the id on the `ocr_tasks` list (`kubectl exec -it deploy/ocr-redis -- redis-cli`).
- [ ] A file larger than 10MB is rejected with `413 Payload Too Large`.
- [ ] One paragraph: why is the `HSET` written *before* the `LPUSH`, and what would break if the order were reversed?

## 🧪 Lab

[`labs/week4_sync_rust_gateway`](../labs/week4_sync_rust_gateway/README.md) is a *synchronous* gateway that rasterizes PDFs and calls vLLM in-request. Load-test both and explain why the final system puts a queue in between.

---

**← Previous:** [Week 3](../week3_vllm_inference/README.md) · **Next:** [Week 5: Async Architecture →](../week5_async_architecture/README.md)
