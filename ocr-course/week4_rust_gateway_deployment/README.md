# 🛠️ Deployment Lifecycle & Operations Guide: Rust API Gateway & vLLM on AKS

This guide provides end-to-end instructions for deploying the **Rust API Gateway**, alongside the **Baidu Unlimited-OCR (`baidu/Unlimited-OCR`) vLLM inference engine** on Azure Kubernetes Service (AKS).

---

## 🏗️ Architecture Topology

The deployment utilizes a **Two-Tier Isolated Node Pool Architecture** on AKS to strictly isolate compute-bound vector rasterization from memory-bound GPU inference:

1. **System Node Pool (`nodepool1` - Default)**:
   * **Role**: Runs cluster foundational services (CoreDNS, CSI storage drivers, KEDA operator, Prometheus/Grafana).
   * **Isolation**: Untainted general-purpose node pool.
2. **Dedicated Compute-Optimized CPU Node Pool (`cpunp`)**:
   * **VM SKU**: `Standard_D8ds_v6` (8 vCPUs, 32 GiB RAM, 4 GiB/core, local NVMe scratch disk).
   * **Workload**: **Rust API Gateway** (`ocr-gateway` - Axum / Tokio / `poppler-utils`).
   * **Role**: Handles client connection pooling, Base64 decoding, MIME magic-byte validation, `PDF_MAX_SIZE` enforcement, multi-threaded vector PDF rasterization at 150 DPI, multi-image batch prompt assembly, and regex grounding tag sanitization.
   * **Isolation**: Protected with node taint `sku=cpunp:NoSchedule`.
3. **Dedicated GPU Node Pool (`gpunpa100`)**:
   * **VM SKU**: `Standard_NC24ads_A100_v4` (1x NVIDIA A100 80GB HBM2e).
   * **Workload**: **vLLM Inference Server** (`baidu/Unlimited-OCR`).
   * **Role**: FlexAttention with Reference Sliding Window Attention (R-SWA) and chunked prefill across 32K context windows.
   * **Isolation**: Protected with node taint `sku=gpunpa100:NoSchedule`.

---

## 0. Prerequisites, Quotas & Environment Setup

> 💡 **Automated Workflow with Makefile:**  
> A complete [Makefile](./Makefile) is provided in this directory. You can execute all tasks via `make` targets (e.g. `make infra-up`, `make nodepool-gpu`, `make nodepool-cpu`, `make build-all`, `make deploy`) or follow the CLI commands below.

```bash
# --- Core Identifiers ---
export LOCATION="francecentral"
export RESOURCE_GROUP="week3-dpl-rg"
export SUBSCRIPTION_ID="<YOUR_SUBSCRIPTION_ID>"
export AKS_NAME="akstnmweek3"
export ACR_NAME="acrtnmweek3"

# GPU Node Pool (vLLM Engine)
export GPU_NODEPOOL_NAME="gpunpa100"
export GPU_VM_SIZE="Standard_NC24ads_A100_v4"

# CPU Node Pool (Rust Gateway)
export CPU_NODEPOOL_NAME="cpunp"
export CPU_VM_SIZE="Standard_D8ds_v6"
```

### ⚠️ Preliminary: Azure Compute Quota Verification

Ensure your Azure subscription has sufficient quota allocated in your target region (`francecentral` or `eastus2`):
* **GPU**: `Standard NCADSA100v4 Family vCPUs` (at least 24 vCPUs for 1x `Standard_NC24ads_A100_v4`).
* **CPU**: `Standard Ddsv6 Family vCPUs` (at least 8 vCPUs for 1x `Standard_D8ds_v6`).

```bash
# ⚡ Check quotas via Makefile:
make check-quota

# Or via Azure CLI directly:
az vm list-usage --location "$LOCATION" \
  --query "[?contains(name.value, 'NC') || contains(name.value, 'StandardDdsv6') || contains(name.localizedValue, 'A100') || contains(name.value, 'StandardNC')]" \
  -o table
```

---

## 1. Infrastructure Setup: AKS Cluster & Node Pools

```bash
# ⚡ Automated via Makefile:
make infra-up
make nodepool-gpu
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

# 5. Add NVIDIA A100 GPU Node Pool (Standard_NC24ads_A100_v4)
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

# 6. Add Dedicated Compute-Optimized CPU Node Pool (Standard_D8ds_v6) for Rust API Gateway
az aks nodepool add \
  --resource-group $RESOURCE_GROUP \
  --cluster-name $AKS_NAME \
  --name $CPU_NODEPOOL_NAME \
  --node-vm-size $CPU_VM_SIZE \
  --node-count 1 \
  --enable-cluster-autoscaler \
  --min-count 0 \
  --max-count 4 \
  --node-taints sku=$CPU_NODEPOOL_NAME:NoSchedule

# 💡 Note on Node Isolation:
# - Rust API Gateway (deployment-gateway.yml) runs exclusively on `cpunp` via toleration `sku=cpunp:NoSchedule`.
```

---

## 2. Storage Setup: Datacenter Model Weight Ingestion

Ingest model weights directly from Hugging Face into the Azure Blob CSI PVC (`model-weights-pvc`):

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

---

## 3. Container Builds & ACR Push

Build the **vLLM Inference Server** and the **Rust API Gateway** container images directly inside ACR:

```bash
# ⚡ Automated via Makefile:
make build-all

# Or individually:
make build-image     # Builds vLLM container
make build-gateway   # Builds Rust API Gateway container

# -----------------------------------------------------------------------------
# Or Manual CLI Execution:
# -----------------------------------------------------------------------------
az acr build --registry $ACR_NAME --image ocr-vlm-unlimitedocr:latest ./server --no-wait
az acr build --registry $ACR_NAME --image ocr-gateway:latest ./gateway --no-wait
```

> [!WARNING]
> **Matching ACR Registry Name in Manifests:**  
> The deployment manifests (`k8s/apps/deployment-vlm.yml` and `k8s/apps/deployment-gateway.yml`) reference images at `acrtnmweek3.azurecr.io/...`. If you customized `$ACR_NAME`, update the registry hostname in all YAML manifests before applying.

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

# Deploy All Workloads (vLLM + Rust Gateway + Services + KEDA Scaler)
kubectl apply -k k8s/
```

### ⚡ Independent KEDA Autoscaling Specifications

The architecture implements two independent **KEDA `ScaledObject`** controllers defined in [`k8s/apps/keda-scaler.yml`](./k8s/apps/keda-scaler.yml):

| Workload | Target Deployment | Scale Range | Primary Scaling Indicators | Cooldown |
| :--- | :--- | :--- | :--- | :--- |
| **Rust API Gateway** | `ocr-gateway-deployment` | `1` ↔ `4` Pods | **1. CPU Utilization > 70%** (Poppler multi-core vector rasterization)<br>**2. Memory Utilization > 75%** (Raw uncompressed page buffers)<br>**3. Prometheus Container Core Usage >= 1.5 cores** | `60s` (Quick recovery after batch bursts) |
| **vLLM Inference Engine** | `ocr-vlm-deployment` | `0` ↔ `2` Pods | **1. Business-Hours Cron Warm Start** (Mon-Fri 08:00–18:00 EST: min 1 replica)<br>**2. GPU Queue Depth** (`vllm:num_requests_waiting >= 1`) | `300s` (5 min cooldown to protect cold-starts and weights cache) |

---

## 5. Rust API Gateway Specification & Ingress Usage

The Rust API Gateway simplifies all model interactions into a single, synchronous endpoint:

### Endpoint: `POST /process`

#### Request Payload (JSON)
```json
{
  "file": "<base64_string_or_data_uri>",
  "batch_size": 4
}
```

* **`file`** *(string, required)*: Base64-encoded string of the document (PNG, JPEG, WebP, TIFF, BMP, or PDF) or Data URI (e.g. `data:image/png;base64,...`).
* **`batch_size`** *(integer, optional, default: 4, clamp: 1..10)*: Number of pages grouped into each multi-image prompt sent to vLLM when processing PDF documents. (Ignored for single images).
* **`concurrency`** *(integer, optional, default: 1, clamp: 1..8)*: Number of parallel concurrent batch requests dispatched to the vLLM server to maximize throughput across continuous batching slots. Automatically clamped to 1 for single images or when `batch_size >= total_pages`.

#### Response Payload (JSON)
```json
{
  "markdown": "# Document Title\n\nExtracted content...",
  "total_pages": 4,
  "batches_processed": 1,
  "latency_ms": 6742,
  "document_type": "application/pdf",
  "bboxes": [
    {
      "label": "title",
      "xmin": 48,
      "ymin": 85,
      "xmax": 388,
      "ymax": 127,
      "text": "2.4 Reasoning"
    }
  ],
  "pages": [
    {
      "page_number": 1,
      "image_data_uri": "data:image/png;base64,...",
      "markdown": "## 2.4 Reasoning\n...",
      "bboxes": [...]
    }
  ]
}
```

### Endpoint: `POST /process-stream` (Real-Time SSE Streaming)

Streams Server-Sent Events with online token sanitization and incremental bounding box detection:

* **`event: page_start`**: Emits `{ "page_number": 1, "total_pages": 4, "image_data_uri": "..." }`.
* **`event: bbox`**: Emits bounding boxes as soon as the `<|det|>` tag closes (`{ "page_number": 1, "box_id": 1, "label": "title", "xmin": 48, "ymin": 85, "xmax": 388, "ymax": 127 }`).
* **`event: token`**: Emits real-time cleaned markdown tokens (`{ "page_number": 1, "box_id": 1, "text": "2.4 " }`).
* **`event: page_done`**: Emits completed page markdown.
* **`event: done`**: Emits summary metadata (`{ "status": "complete", "total_pages": 4, "latency_ms": 4120 }`).

#### Error Handling & Guardrails
* If a PDF contains more pages than **`PDF_MAX_SIZE`** (default `40`), the gateway returns `413 Payload Too Large`:
  ```json
  {
    "error": "Processing failed",
    "details": "PDF contains 52 pages, which exceeds the maximum allowable limit of 40 pages (PDF_MAX_SIZE)"
  }
  ```
* If the GPU vLLM engine is still loading weights into A100 VRAM, the endpoints return `503 Service Unavailable`:
  ```json
  {
    "error": "Processing failed",
    "details": "GPU Inference Server (baidu/Unlimited-OCR on NVIDIA A100) is currently initializing model weights. Please wait a moment until the GPU engine is ready."
  }
  ```
* If the payload is unparseable or an unsupported format, it returns `400 Bad Request`.

---

## 6. Client Invocation & Access Recipes

### 🔒 Ingress Topology & Zero-Trust Access Model
* **100% Private ClusterIP Topology**: All services—**Rust API Gateway** (`ocr-gateway-service:80`) and **vLLM Inference Engine** (`ocr-vlm-service:8000`)—are deployed as private **`ClusterIP`** services with **no public IPs or external Azure LoadBalancers**.
* **Zero Public Attack Surface**: No endpoints are exposed to the public internet. All traffic into the cluster is tunneled securely via authenticated `kubectl port-forward` sessions.

---

### 1. Tunneling to Private Backend Services (Port-Forwarding)

To run direct Python scripts or `curl` commands against the private microservices, establish a local port-forward:

```bash
# ⚡ Port-forward Rust API Gateway (ClusterIP) to http://localhost:3000:
make port-forward-gateway
# Or directly: kubectl port-forward svc/ocr-gateway-service 3000:80

# ⚡ Port-forward vLLM Inference Engine (ClusterIP) to http://localhost:8000:
make port-forward-vlm
# Or directly: kubectl port-forward svc/ocr-vlm-service 8000:8000
```

---

### 2. Single Image Ingestion with Python (via Rust Gateway)

```python
import base64
import json
import urllib.request
import time

# 1. Read and base64-encode local image
with open("sample_image.png", "rb") as f:
    b64_str = base64.b64encode(f.read()).decode("utf-8")

payload = {
    "file": b64_str,
    "batch_size": 4  # Ignored for single images
}

req = urllib.request.Request(
    "http://localhost:3000/process",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

start = time.time()
with urllib.request.urlopen(req, timeout=300) as response:
    result = json.loads(response.read().decode("utf-8"))
    print(f"⚡ Inferred in {time.time()-start:.2f}s")
    print(f"📊 Document Type: {result['document_type']} | Pages: {result['total_pages']} | Gateway Latency: {result['latency_ms']}ms")
    print(f"📦 Bounding Boxes Detected: {len(result.get('bboxes', []))}")
    print(f"📄 Markdown Output:\n\n{result['markdown']}")
```

---

### 3. Multi-Page PDF Ingestion with Python (via Rust Gateway)

```python
import base64
import json
import urllib.request
import time

# 1. Encode multi-page PDF document
with open("quarterly_financial_report.pdf", "rb") as f:
    b64_pdf = base64.b64encode(f.read()).decode("utf-8")

payload = {
    "file": b64_pdf,
    "batch_size": 4  # Sends 4 pages per vLLM multi-image prompt
}

req = urllib.request.Request(
    "http://localhost:3000/process",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

start = time.time()
with urllib.request.urlopen(req, timeout=600) as response:
    result = json.loads(response.read().decode("utf-8"))
    print(f"✅ Processed {result['total_pages']} pages in {result['batches_processed']} vLLM batches")
    print(f"⚡ End-to-End Latency: {result['latency_ms']/1000:.2f}s")
    print("=" * 80)
    print(result["markdown"])
```

---

### 4. Direct CLI Invocations with `curl`

```bash
# 1. Health check Rust Gateway (via port-forward)
curl http://localhost:3000/health

# 2. Synchronous Image OCR
curl -X POST http://localhost:3000/process \
  -H "Content-Type: application/json" \
  -d "{\"file\": \"$(base64 -i sample_image.png)\"}"

# 3. Real-Time SSE Stream
curl -N -X POST http://localhost:3000/process-stream \
  -H "Content-Type: application/json" \
  -d "{\"file\": \"$(base64 -i sample_image.png)\"}"
```

---

## 7. Cost Optimization: Scaling Both Node Pools to Zero

When development is paused, scale both the GPU and CPU node pools to zero to eliminate idle VM billing:

```bash
# ⚡ Via Makefile:
make scale-to-zero

# Or manually:
az aks nodepool update -g $RESOURCE_GROUP --cluster-name $AKS_NAME -n $GPU_NODEPOOL_NAME --update-cluster-autoscaler --min-count 0 --max-count 4
az aks nodepool update -g $RESOURCE_GROUP --cluster-name $AKS_NAME -n $CPU_NODEPOOL_NAME --update-cluster-autoscaler --min-count 0 --max-count 4
```
