# Week 3: ⚡ Deploying the vLLM Inference Engine

> 📘 **Article:** _link coming soon_

<p align="center"><img src="../../images/week3_vllm_optimization.png" width="600" alt="Week 3"></p>

## 🎯 What You'll Learn

* How vLLM gets its throughput: **continuous batching**, **PagedAttention** and **chunked prefill**.
* What `MAX_NUM_BATCHED_TOKENS`, `MAX_NUM_SEQS`, `MAX_MODEL_LEN` and `GPU_MEMORY` actually control, and how they interact on an 80GB A100.
* How to get model weights onto a cluster once (blob-backed PVC + ingestion job) instead of on every pod start.

## 🔍 Where It Lives

| File | What to look at |
| :--- | :--- |
| [`services/server/entrypoint.sh`](../../services/server/entrypoint.sh) | The `vllm serve` flags |
| [`deploy/k8s/kustomization.yml`](../../deploy/k8s/kustomization.yml) | `ocr-pipeline-config`, where the vLLM knobs are set |
| [`deploy/k8s/infra/pvc.yaml`](../../deploy/k8s/infra/pvc.yaml), [`ingest-job.yaml`](../../deploy/k8s/infra/ingest-job.yaml) | Weight storage and download |
| [`docs/architecture.md`](../../docs/architecture.md) | *Throughput Optimization Matrix* and *vLLM Engine Optimizations* |

## 🛠️ Hands-On

```bash
make platform            # KEDA + Prometheus/Grafana (vLLM's scaler reads Prometheus)
make ingest-weights      # PVC + Qwen 3.5 2B / PP-DocLayoutV3 download job
make ingest-logs
make build-vlm
make deploy              # deploys the full stack; this week, focus on ocr-vlm
make logs-vlm
make port-forward-vlm    # OpenAI-compatible API on http://localhost:8000
```

## ✅ Deliverables

- [ ] `make ingest-logs` shows both models ingested.
- [ ] `curl http://localhost:8000/v1/models` lists `Qwen/Qwen3.5-2B`.
- [ ] A direct `/v1/chat/completions` request with `samples/sample_image.png` (base64 `image_url`) returns transcribed text.
- [ ] **Tuning experiment:** change `MAX_NUM_BATCHED_TOKENS` in `deploy/k8s/kustomization.yml`, run `make deploy restart`, and record the effect on throughput or latency (Grafana: `make port-forward-grafana`).
- [ ] *Stretch:* enable Multi-Token Prediction (speculative decoding) in `entrypoint.sh` and measure the speed-up.

## 🧪 Lab

[`labs/week3_unlimited_ocr_vllm`](../labs/week3_unlimited_ocr_vllm/README.md) serves the single-stage `baidu/Unlimited-OCR` model with the same vLLM setup. Run the same request against both and compare.

---

**← Previous:** [Week 2](../week2_ocr_approaches/README.md) · **Next:** [Week 4: Rust Ingest Gateway →](../week4_rust_gateway/README.md)
