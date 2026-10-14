# Week 5: 🔄 Async Architectures & Zero-Copy Ingestion

> 📘 **Article:** _link coming soon_

<p align="center"><img src="../../images/week5_async_architecture.png" width="600" alt="Week 5"></p>

## 🎯 What You'll Learn

* Queues as shock absorbers: decoupling bursty ingestion from expensive GPU inference.
* The **dynamic batching collector**: wait up to `BATCH_WINDOW_MS` to fill a batch of up to `MAX_BATCH_SIZE`.
* **Zero-copy handoff** through a `/dev/shm` RAM-disk between the worker and the layout engine.
* Event-driven autoscaling with **KEDA**: Redis list length for the T4 worker, vLLM waiting requests for the A100, cron warm-starts, and scale-to-zero.

## 🔍 Where It Lives

| File | What to look at |
| :--- | :--- |
| [`services/client_rt_consumer/worker.py`](../../services/client_rt_consumer/worker.py) | Batch collection loop, `/dev/shm` writes, heartbeat |
| [`deploy/k8s/apps/deployment-api.yml`](../../deploy/k8s/apps/deployment-api.yml) | `ocr-worker-rt-deployment`, the `emptyDir: {medium: Memory}` mount |
| [`deploy/k8s/apps/keda-scaler.yml`](../../deploy/k8s/apps/keda-scaler.yml) | The three `ScaledObject`s |
| [`deploy/k8s/kustomization.yml`](../../deploy/k8s/kustomization.yml) | `MAX_BATCH_SIZE`, `BATCH_WINDOW_MS` |

## 🛠️ Hands-On

```bash
make build-worker deploy
make status              # pods + KEDA ScaledObjects
make test                # end-to-end: submit samples/sample_image.png and poll
make logs-worker
kubectl get scaledobjects -w
```

## ✅ Deliverables

- [ ] `make test` prints the OCR markdown for the sample image.
- [ ] With the GPU pools at zero (outside the cron window), submit a task and record the **cold-start timeline**: T4 node up → worker ready → A100 node up → result. Note how long each step took.
- [ ] Submit 20 tasks in parallel and capture the worker log lines showing batches larger than 1.
- [ ] One paragraph: what does `BATCH_WINDOW_MS` trade off, and what value would you pick for a latency-sensitive vs. throughput-oriented workload?

---

**← Previous:** [Week 4](../week4_rust_gateway/README.md) · **Next:** [Week 6: Enterprise Gateways & MCP →](../week6_apim_mcp/README.md)
