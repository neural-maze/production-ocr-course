# 🛠️ Deployment Lifecycle & Operations Guide: Redis Queue, Rust Producer & GLM-OCR Consumer on AKS

This guide provides end-to-end instructions for deploying the **asynchronous, event-driven OCR pipeline**: a Rust API Gateway that queues incoming documents into **Redis**, a Python **GLM-OCR SDK** consumer worker that performs zero-copy layout detection and dynamic batching, and the **Qwen 3.5 (2B)** vLLM inference engine, all on Azure Kubernetes Service (AKS).

---

## 🏗️ Architecture Topology

The deployment utilizes a **Three-Tier Isolated Node Pool Architecture** on AKS, decoupling the lightweight ingestion path from the two GPU-bound stages of the pipeline:

1. **System Node Pool (`nodepool1` - Default)**:
   * **Role**: Runs cluster foundational services (CoreDNS, CSI storage drivers, KEDA operator, Prometheus/Grafana).
   * **Isolation**: Untainted general-purpose node pool.
2. **Dedicated CPU Node Pool (`cpunp`)**:
   * **VM SKU**: `Standard_D8ds_v6` (8 vCPUs, 32 GiB RAM).
   * **Workload**: **Rust Producer API** (`ocr-api-rust` - Axum) and the **Redis** state store (`ocr-redis`).
   * **Role**: Accepts multipart file uploads, base64-encodes the payload, atomically `HSET`s task state into Redis, and `LPUSH`es the `task_id` onto the `ocr_tasks` queue. Redis decouples ingestion from GPU-bound processing entirely.
   * **Isolation**: Protected with node taint `sku=cpunp:NoSchedule`.
3. **Dedicated T4 GPU Node Pool (`gpunpt4`)**:
   * **VM SKU**: `Standard_NC16as_T4_v3` (16 vCPUs, 16GB VRAM).
   * **Workload**: **GLM-OCR Consumer Worker** (`ocr-worker-rt` - Python).
   * **Role**: Pops tasks from Redis using a **Dynamic Batching Collector** (waits up to `BATCH_WINDOW_MS` to fill a batch of up to `MAX_BATCH_SIZE` documents), writes binaries to a `/dev/shm` RAM-disk, runs **PP-DocLayoutV3** layout detection, and dispatches recognized regions to the vLLM engine.
   * **Isolation**: Protected with node taint `sku=gpunpt4:NoSchedule`.
4. **Dedicated GPU Node Pool (`gpunpa100`)**:
   * **VM SKU**: `Standard_NC24ads_A100_v4` (1x NVIDIA A100 80GB HBM2e).
   * **Workload**: **vLLM Inference Server** (`Qwen/Qwen3.5-2B`).
   * **Role**: High-throughput generative OCR/reasoning over the regions cropped by the layout detector.
   * **Isolation**: Protected with node taint `sku=gpunpa100:NoSchedule`.

---

## 0. Prerequisites, Quotas & Environment Setup

> 💡 **Automated Workflow with Makefile:**
> A complete [Makefile](./Makefile) is provided in this directory. You can execute all tasks via `make` targets (e.g. `make infra-up`, `make nodepool-t4`, `make build-all`, `make deploy`) or follow the CLI commands below.

```bash
# --- Core Identifiers ---
export LOCATION="francecentral"
export RESOURCE_GROUP="week3-dpl-rg"
export SUBSCRIPTION_ID="<YOUR_SUBSCRIPTION_ID>"
export AKS_NAME="akstnmweek3"
export ACR_NAME="acrtnmweek3"

# GPU Node Pool (vLLM Qwen 3.5 2B Engine)
export GPU_NODEPOOL_NAME="gpunpa100"
export GPU_VM_SIZE="Standard_NC24ads_A100_v4"

# T4 Node Pool (GLM-OCR Consumer Worker)
export T4_NODEPOOL_NAME="gpunpt4"
export T4_VM_SIZE="Standard_NC16as_T4_v3"

# CPU Node Pool (Rust Producer & Redis)
export CPU_NODEPOOL_NAME="cpunp"
export CPU_VM_SIZE="Standard_D8ds_v6"
```

> ℹ️ **Reusing the Course Cluster:**
> This walkthrough targets the same AKS cluster (`akstnmweek3` in `week3-dpl-rg`) provisioned in the [Week 3](../week3_vllm_deployment/README.md) and [Week 4](../week4_rust_gateway_deployment/README.md) walkthroughs. `az group create` / `az aks create` are idempotent, so running Section 1 below is safe even if the cluster already exists — it only adds the new `gpunpt4` node pool this week introduces.

### ⚠️ Preliminary: Azure Compute Quota Verification

Ensure your Azure subscription has sufficient quota allocated in your target region (`francecentral` or `eastus2`):
* **A100 GPU**: `Standard NCADSA100v4 Family vCPUs` (at least 24 vCPUs for 1x `Standard_NC24ads_A100_v4`).
* **T4 GPU**: `Standard NCASv3_T4 Family vCPUs` (at least 16 vCPUs for 1x `Standard_NC16as_T4_v3`).
* **CPU**: `Standard Ddsv6 Family vCPUs` (at least 8 vCPUs for 1x `Standard_D8ds_v6`).

```bash
# ⚡ Check quotas via Makefile:
make check-quota

# Or via Azure CLI directly:
az vm list-usage --location "$LOCATION" \
  --query "[?contains(name.value, 'NC') || contains(name.localizedValue, 'A100') || contains(name.localizedValue, 'T4') || contains(name.value, 'StandardNC')]" \
  -o table
```

---

### 🧰 Local CLI Tooling Prerequisites

```bash
# Via Makefile check
make check-cli

# Manual install (macOS via Homebrew)
brew install azure-cli kubernetes-cli helm
```

### 🔑 Authenticate Azure CLI Session

```bash
# Via Makefile
make auth

# Or manually:
az login
az account set --subscription "$SUBSCRIPTION_ID"
az account show --output table
```

---

## 1. Infrastructure Setup: AKS Cluster & Node Pools

```bash
# ⚡ Automated via Makefile:
make infra-up
make nodepool-gpu
make nodepool-t4
make nodepool-cpu

# -----------------------------------------------------------------------------
# Or Manual CLI Execution:
# -----------------------------------------------------------------------------

# 1. Create Resource Group
az group create --name $RESOURCE_GROUP --location $LOCATION

# 2. Create Azure Container Registry (ACR)
az acr create -g $RESOURCE_GROUP -n $ACR_NAME --sku Premium --location $LOCATION
az acr login --name $ACR_NAME

# 3. Create AKS Cluster with Blob CSI Driver and Managed Identity
az aks create \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --name $AKS_NAME \
  --attach-acr $ACR_NAME \
  --node-count 1 \
  --enable-managed-identity \
  --enable-blob-driver \
  --generate-ssh-keys

# 4. Download AKS credentials to local kubeconfig
az aks get-credentials \
  --resource-group $RESOURCE_GROUP \
  --name $AKS_NAME \
  --overwrite-existing

# 5. Add NVIDIA A100 GPU Node Pool (vLLM Qwen 3.5 2B)
az aks nodepool add \
  --resource-group $RESOURCE_GROUP \
  --cluster-name $AKS_NAME \
  --name $GPU_NODEPOOL_NAME \
  --node-vm-size $GPU_VM_SIZE \
  --node-count 1 \
  --enable-cluster-autoscaler \
  --min-count 0 \
  --max-count 4 \
  --node-taints sku=$GPU_NODEPOOL_NAME:NoSchedule \
  --tags EnableManagedGPUExperience=true

# 6. Add NVIDIA T4 GPU Node Pool (GLM-OCR Consumer Worker)
az aks nodepool add \
  --resource-group $RESOURCE_GROUP \
  --cluster-name $AKS_NAME \
  --name $T4_NODEPOOL_NAME \
  --node-vm-size $T4_VM_SIZE \
  --node-count 1 \
  --enable-cluster-autoscaler \
  --min-count 0 \
  --max-count 10 \
  --node-taints sku=$T4_NODEPOOL_NAME:NoSchedule \
  --tags EnableManagedGPUExperience=true

# 7. Add Dedicated CPU Node Pool (Rust Producer + Redis)
az aks nodepool add \
  --resource-group $RESOURCE_GROUP \
  --cluster-name $AKS_NAME \
  --name $CPU_NODEPOOL_NAME \
  --node-vm-size $CPU_VM_SIZE \
  --node-count 1 \
  --enable-cluster-autoscaler \
  --min-count 1 \
  --max-count 4 \
  --node-taints sku=$CPU_NODEPOOL_NAME:NoSchedule

# 💡 Note on Node Isolation:
# - Rust Producer API + Redis (deployment-api.yml, redis-deployment.yml) run exclusively on `cpunp`.
# - GLM-OCR Consumer Worker (deployment-api.yml: ocr-worker-rt-deployment) runs exclusively on `gpunpt4`.
```

---

## 2. Storage Setup: Datacenter Model Weight Ingestion

Ingest both model families directly from Hugging Face into the Azure Blob CSI PVC (`model-weights-pvc`): the vLLM generation model and the layout detector consumed by the GLM-OCR SDK.

```bash
# ⚡ Automated via Makefile:
make storage-pvc
make ingest-weights
make ingest-logs

# Or manual execution:
kubectl apply -f k8s/infra/pvc.yaml
kubectl delete job model-weight-ingest --ignore-not-found=true
kubectl apply -f k8s/infra/ingest-job.yaml
kubectl logs -f job/model-weight-ingest
```

The ingestion Job downloads `Qwen/Qwen3.5-2B` (vLLM generation) and `PaddlePaddle/PP-DocLayoutV3_safetensors` (GLM-OCR layout detection) side by side, writing a `.ingested_*` marker per model so re-running the job is a no-op once weights are present.

---

## 3. Container Builds & ACR Push

Build the **vLLM Inference Server**, the **Rust Producer API**, and the **GLM-OCR Consumer Worker** container images directly inside ACR:

```bash
# ⚡ Automated via Makefile:
make build-all

# Or individually:
make build-vlm      # Builds vLLM (Qwen 3.5 2B) container
make build-api      # Builds Rust Producer API container
make build-worker   # Builds GLM-OCR Consumer Worker container

# -----------------------------------------------------------------------------
# Or Manual CLI Execution:
# -----------------------------------------------------------------------------
az acr build --registry $ACR_NAME --image ocr-vlm-qwen:latest ./server --no-wait
az acr build --registry $ACR_NAME --image ocr-api-rust:latest ./client_rt_producer --no-wait
az acr build --registry $ACR_NAME --image ocr-worker-rt:latest ./client_rt_consumer --no-wait
```

> [!WARNING]
> **Matching ACR Registry Name in Manifests:**
> The deployment manifests (`k8s/apps/deployment-api.yml` and `k8s/apps/deployment-vlm.yml`) reference images at `acrtnmweek3.azurecr.io/...`. If you customized `$ACR_NAME`, update the registry hostname in both files before applying. Otherwise, Kubernetes will fail with an `ImagePullBackOff (401 Unauthorized)` error.

---

## 4. Deploy Full Stack via Kustomize

```bash
# ⚡ Automated via Makefile:
make install-keda
make install-monitoring
make deploy

# -----------------------------------------------------------------------------
# Or Manual CLI Execution:
# -----------------------------------------------------------------------------
helm repo add kedacore https://kedacore.github.io/charts --force-update
helm upgrade --install keda kedacore/keda -n keda --create-namespace

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm repo update
kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set grafana.enabled=true

# Deploy All Workloads (Redis + Rust Producer + Worker + vLLM + Services + KEDA Scalers)
kubectl apply -k k8s/
```

### ⚡ Independent KEDA Autoscaling Specifications

The architecture implements three independent **KEDA `ScaledObject`** controllers defined in [`k8s/apps/keda-scaler.yml`](./k8s/apps/keda-scaler.yml):

| Workload | Target Deployment | Scale Range | Primary Scaling Indicators | Cooldown |
| :--- | :--- | :--- | :--- | :--- |
| **Rust Producer API** | `ocr-api-deployment` | `1` ↔ `5` Pods | **CPU Utilization > 70%** (multipart upload bursts) | Default |
| **GLM-OCR Consumer Worker** | `ocr-worker-rt-deployment` | `0` ↔ `10` Pods | **1. Business-Hours Cron Warm Start** (Mon-Fri 08:00–19:00 Europe/Paris: min 1 replica)<br>**2. Redis Queue Length >= 1** (`ocr_tasks` list — scales 1 worker per pending task) | `300s` |
| **vLLM Inference Engine** | `ocr-vlm-deployment` | `0` ↔ `4` Pods | **1. Business-Hours Cron Warm Start** (Mon-Fri 08:00–19:00 Europe/Paris: min 1 replica)<br>**2. Prometheus vLLM Queue Depth** (`vllm:num_requests_waiting >= 1`) | `300s` |

---

## 5. Async API Specification & Endpoint Usage

The Rust Producer exposes a fire-and-poll interface: submission returns immediately with a `task_id`, and the actual OCR work happens asynchronously on the `gpunpt4` and `gpunpa100` pools.

### Endpoint: `POST /process`

Accepts a **multipart form upload** (not JSON) — this keeps the Rust gateway a thin, streaming pass-through that never buffers the whole request body as base64 in memory.

```bash
curl -X POST http://localhost:5000/process \
  -F "file=@sample_image.png"
```

Response (`202 Accepted`):
```json
{
  "task_id": "8a32b6e4-6a01-4475-9b22-83cfbc63a4dc",
  "status": "queued"
}
```

Internally, the Producer generates a `task_id`, base64-encodes the file, writes `status`/`filename`/`extension`/`data` into a Redis hash (`task:<task_id>`) via a single atomic `HSET`, then `LPUSH`es the `task_id` onto the `ocr_tasks` list that both the Consumer Worker and the KEDA Redis scaler watch.

### Endpoint: `GET /status/{task_id}`

```bash
curl http://localhost:5000/status/8a32b6e4-6a01-4475-9b22-83cfbc63a4dc
```

Response while queued/processing (`200 OK`):
```json
{ "task_id": "8a32b6e4-6a01-4475-9b22-83cfbc63a4dc", "status": "processing", "result": null, "error": null }
```

Response once complete (`200 OK`):
```json
{
  "task_id": "8a32b6e4-6a01-4475-9b22-83cfbc63a4dc",
  "status": "done",
  "result": {
    "markdown": "# Document Title\n\nRecognized text...",
    "layout": {
      "regions": [
        { "label": "title", "bbox_2d": [48, 85, 388, 127], "content": "Document Title" }
      ]
    }
  },
  "error": null
}
```

An unknown `task_id` returns `404 Not Found`; a failed batch sets `status: "failed"` with an `error` message.

---

## 6. Client Invocation & Access Recipes

### 🔒 Ingress Topology & Zero-Trust Access Model
* **Private ClusterIP/Internal-LB Topology**: `ocr-api-service` is annotated `service.beta.kubernetes.io/azure-load-balancer-internal: "true"`, so it never receives a public IP. `ocr-vlm-service` and `ocr-redis-service` are plain `ClusterIP` — reachable only from inside the cluster network.
* **Zero Public Attack Surface**: All traffic into the cluster during local development is tunneled via authenticated `kubectl port-forward` sessions.

### 1. Tunneling to the Producer API (Port-Forwarding)

```bash
# ⚡ Port-forward Rust Producer API to http://localhost:5000:
make port-forward-api
# Or directly: kubectl port-forward svc/ocr-api-service 5000:80
```

### 2. End-to-End Asynchronous Ingestion with Python

```python
import time
import requests

PRODUCER_URL = "http://localhost:5000"

# 1. Submit the document
with open("sample_image.png", "rb") as f:
    resp = requests.post(f"{PRODUCER_URL}/process", files={"file": f})
    resp.raise_for_status()
    task = resp.json()
    task_id = task["task_id"]
    print(f"🚀 Job submitted. Task ID: {task_id} (Status: {task['status']})")

# 2. Poll until completed
start_time = time.time()
while True:
    status_resp = requests.get(f"{PRODUCER_URL}/status/{task_id}").json()
    status = status_resp.get("status")
    print(f"⏳ Task {task_id} status: {status} (Elapsed: {time.time()-start_time:.1f}s)")

    if status == "done":
        result = status_resp["result"]
        print("\n✅ OCR Processing Finished!")
        print(f"📄 Markdown Output:\n{result['markdown']}")
        print(f"📦 Detected Layout Elements: {len(result.get('layout', {}).get('regions', []))}")
        break
    elif status == "failed":
        print(f"❌ Task failed: {status_resp.get('error')}")
        break

    time.sleep(0.5)
```

### 3. Direct CLI Invocation & Automated Test

```bash
# ⚡ Submit sample_image.png and poll to completion via Makefile:
make test-submit

# Or manually, submit then poll:
curl -X POST http://localhost:5000/process -F "file=@sample_image.png"
curl http://localhost:5000/status/<task_id>
```

### 4. Inspecting Each Stage in Real Time

```bash
# 1. Rust Producer API (cpunp)
make logs-api

# 2. GLM-OCR Consumer Worker (gpunpt4) — layout detection + dynamic batching
make logs-worker

# 3. vLLM Server (gpunpa100) — Qwen 3.5 2B inference
make logs-vlm
```

---

## 7. Cost Optimization: Scaling Both GPU Node Pools to Zero

When development is paused, scale the T4 and A100 node pools to zero to eliminate idle GPU billing. The `cpunp` pool (Redis + Rust Producer) is left running since these are cheap, always-on components (`ocr-api-scaler` keeps `minReplicaCount: 1`):

```bash
# ⚡ Via Makefile:
make scale-to-zero

# Or manually:
az aks nodepool update -g $RESOURCE_GROUP --cluster-name $AKS_NAME -n $GPU_NODEPOOL_NAME --update-cluster-autoscaler --min-count 0 --max-count 4
az aks nodepool update -g $RESOURCE_GROUP --cluster-name $AKS_NAME -n $T4_NODEPOOL_NAME --update-cluster-autoscaler --min-count 0 --max-count 10
```

---

## 🔍 Monitoring & Resources
*   [HuggingFace: Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B)
*   [HuggingFace: PaddleOCR-VL 1.5 / PP-DocLayoutV3](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.5)
*   [vLLM Inference Engine](https://docs.vllm.ai/)
*   [KEDA Redis Lists Scaler](https://keda.sh/docs/scalers/redis-lists/)
*   [KEDA Prometheus Scaler](https://keda.sh/docs/scalers/prometheus/)
*   [Azure AKS Managed GPU Drivers Guide](https://learn.microsoft.com/en-us/azure/aks/gpu-cluster)
