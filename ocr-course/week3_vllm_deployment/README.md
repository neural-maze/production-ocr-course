## 🛠️ Deployment Lifecycle

### 0. Prerequisites, Quotas & Environment Setup

> 💡 **Automated Workflow with Makefile:**  
> A complete [Makefile](file:///Users/hedrergudene/Documents/GitHub/tnm-articles/articles/20260812/deployment/Makefile) is provided in this directory. You can run all steps via `make` targets (e.g. `make infra-up`, `make nodepool-gpu`, `make ingest-weights`, `make deploy`) or execute the raw CLI commands detailed below.

```bash
# --- Core Identifiers ---
export LOCATION="francecentral"
export RESOURCE_GROUP="week3-dpl-rg"
export SUBSCRIPTION_ID="<YOUR_SUBSCRIPTION_ID>"
export AKS_NAME="akstnmweek3"
export ACR_NAME="acrtnmweek3"
export GPU_NODEPOOL_NAME="gpunpa100"
export GPU_VM_SIZE="Standard_NC24ads_A100_v4"
```

#### ⚠️ Preliminary: Azure GPU Quota Request (`Standard_NC24ads_A100_v4`)

By default, all new Azure subscriptions have a **`0` vCPU limit for GPU families** (such as NVIDIA A100 / `Standard NCADSA100v4 Family`). Before provisioning the GPU node pool, you must ensure sufficient quota is allocated to your subscription in your target region (`francecentral` or `eastus2`).

##### 1. Check Current GPU Quota
```bash
# Via Makefile
make check-quota

# Or via Azure CLI directly
az vm list-usage --location "$LOCATION" \
  --query "[?contains(name.value, 'NC') || contains(name.localizedValue, 'NC') || contains(name.localizedValue, 'A100') || contains(name.value, 'StandardNC')]" \
  -o table
```

##### 2. Requesting Quota Increase (Azure Portal)
If your `CurrentValue / Limit` for `Standard NCADSA100v4 Family` is `0 / 0`:
1. Navigate to the **Azure Portal** $\rightarrow$ Search for **Quotas** (or go to **Subscriptions** $\rightarrow$ Select your Subscription $\rightarrow$ **Usage + quotas**).
2. Select Provider: **Microsoft.Compute** and Region: **`francecentral`** (or your chosen region).
3. Search for: **`Standard NCADSA100v4 Family vCPUs`** (or `Standard_NC24ads_A100_v4`).
4. Click **Request increase** $\rightarrow$ Set the new limit to at least **24 vCPUs** (1x `Standard_NC24ads_A100_v4` VM requires 24 vCPUs) or **48 vCPUs** (for scaling up to 2 nodes).
5. Standard automated quota approvals usually complete within 2–5 minutes.

---

### 🧰 Local CLI Tooling Prerequisites

Ensure `azure-cli`, `kubectl`, and `helm` are installed on your local machine:

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

# Register required Azure Resource Providers
az provider register --namespace Microsoft.ContainerRegistry
az provider register --namespace Microsoft.ContainerService
az provider register --namespace Microsoft.Storage
```

---

### 1. Infrastructure Setup: AKS & Storage

```bash
# ⚡ Automated via Makefile:
make infra-up
make nodepool-gpu

# -----------------------------------------------------------------------------
# Or Manual Step-by-Step CLI Execution:
# -----------------------------------------------------------------------------

# 1. Create Resource Group
az group create --name $RESOURCE_GROUP --location $LOCATION

# 2. Create Azure Container Registry (ACR)
az acr create -g $RESOURCE_GROUP -n $ACR_NAME --sku Premium --location $LOCATION

# 3. Download ACR credentials
az acr login --name $ACR_NAME

# 4. Create AKS Cluster with Blob CSI Driver and Managed Identity
az aks create \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --name $AKS_NAME \
  --attach-acr $ACR_NAME \
  --node-count 1 \
  --enable-managed-identity \
  --enable-blob-driver \
  --generate-ssh-keys

# 5. Download AKS credentials to local kubeconfig
az aks get-credentials \
  --resource-group $RESOURCE_GROUP \
  --name $AKS_NAME \
  --overwrite-existing

# 6. Add NVIDIA A100 GPU Node Pool (Standard_NC24ads_A100_v4)
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
```

#### 🛡️ Managed GPU Lifecycle on AKS

By specifying `--tags EnableManagedGPUExperience=true` without `--gpu-driver none`, AKS uses **Native Managed GPU Drivers**. AKS automatically installs NVIDIA CUDA/GRID drivers, configures `containerd` with GPU OCI runtime hooks, and runs the managed device plugin out of the box during VM provisioning.

#### 🔍 Verify GPU Schedulability

```bash
# Via Makefile
make verify-gpu

# Or directly:
kubectl get nodes "-o=custom-columns=NAME:.metadata.name,GPU_CAPACITY:.status.capacity.nvidia\.com/gpu,GPU_ALLOCATABLE:.status.allocatable.nvidia\.com/gpu"
```

---

### 2. Storage Setup: Datacenter Model Weight Ingestion

To serve model inference without incurring prohibitive download times upon pod initialization, we ingest weights directly from Hugging Face into an Azure Storage Account (Blob / NFS) shared volume.

```bash
# ⚡ Automated via Makefile:
make storage-pvc
make ingest-weights
make ingest-logs

# -----------------------------------------------------------------------------
# Or Manual Step-by-Step CLI Execution:
# -----------------------------------------------------------------------------

# 1. Create Storage PVC
kubectl apply -f k8s/infra/pvc.yaml

# 2. Deploy Datacenter Weight Ingestion Job (Deletes any previous job instance first)
kubectl delete job model-weight-ingest --ignore-not-found=true
kubectl apply -f k8s/infra/ingest-job.yaml

# 3. Stream download progress
kubectl logs -f job/model-weight-ingest
```

#### 2.4 Inspect PVC Contents

Because Azure Blob Storage (`azureblob-fuse-premium`) is an object store, generic POSIX directory listing (`ls /mnt/models`) does not index virtual directories. To verify downloaded model files and sizes on your PVC volume:

```bash
# Via Makefile:
make inspect-pvc

# Or via raw kubectl run:
kubectl run pvc-inspector --rm -i --tty --restart=Never --image=python:3.11-slim --overrides='
{
  "spec": {
    "containers": [{
      "name": "inspector",
      "image": "python:3.11-slim",
      "command": ["python3", "-c", "import os, urllib.request, json, ssl\nmodel_id = \"baidu/Unlimited-OCR\"\nctx = ssl.create_default_context()\nctx.check_hostname = False\nctx.verify_mode = ssl.CERT_NONE\nreq = urllib.request.Request(f\"https://huggingface.co/api/models/{model_id}\")\nwith urllib.request.urlopen(req, context=ctx) as r:\n    files = [s[\"rfilename\"] for s in json.loads(r.read().decode())[\"siblings\"]]\nprint(f\"Checking {len(files)} files on Blob PVC:\")\nfor f in files:\n    fp = f\"/mnt/models/{model_id}/{f}\"\n    if os.path.exists(fp):\n        sz = os.path.getsize(fp) / (1024*1024)\n        print(f\"  ✅ {f}: {sz:.2f} MB\")\n    else:\n        print(f\"  ❌ {f}: Missing\")\n"],
      "volumeMounts": [{
        "name": "weights",
        "mountPath": "/mnt/models"
      }]
    }],
    "volumes": [{
      "name": "weights",
      "persistentVolumeClaim": {
        "claimName": "model-weights-pvc"
      }
    }]
  }
}'
```

---

### 3. Build & Push

```bash
# ⚡ Automated via Makefile:
make build-image

# -----------------------------------------------------------------------------
# Or Manual CLI Execution:
# -----------------------------------------------------------------------------
az acr login --name $ACR_NAME

# Build the vLLM Server container image (Baidu Unlimited-OCR VLM Engine)
az acr build --registry $ACR_NAME --image ocr-vlm-unlimitedocr:latest ./server
```

> [!WARNING]
> **Matching ACR Name in Deployment Manifests:**  
> The Kubernetes deployment manifest [`k8s/apps/deployment-vlm.yml`](./k8s/apps/deployment-vlm.yml) specifies the container image path (`image: acrtnmweek3.azurecr.io/ocr-vlm-unlimitedocr:latest`).  
> If you customized `$ACR_NAME` in Section 0, make sure to update the image repository in [`k8s/apps/deployment-vlm.yml`](./k8s/apps/deployment-vlm.yml) (line 26) to match your ACR login server (`${ACR_NAME}.azurecr.io/ocr-vlm-unlimitedocr:latest`). Otherwise, Kubernetes will fail with an `ImagePullBackOff (401 Unauthorized)` error.

---

### 4. Deploy the Full Stack

```bash
# ⚡ Automated via Makefile:
make install-keda
make install-monitoring
make deploy

# -----------------------------------------------------------------------------
# Or Manual Step-by-Step CLI Execution:
# -----------------------------------------------------------------------------

# 1. Install KEDA
helm repo add kedacore https://kedacore.github.io/charts --force-update
helm upgrade --install keda kedacore/keda -n keda --create-namespace

# 2. Install Prometheus Stack (Required for Real-time Scaling)
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm repo update

kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set grafana.enabled=true

# 3. Deploy vLLM Workload Manifests
kubectl apply -k k8s/
```

#### 4.1 Under the Hood: The 7 Stages of AKS Lifecycle Upon `kubectl apply -k k8s/`

When you execute `kubectl apply -k k8s/` (or `make deploy`), the system triggers an asynchronous multi-layer orchestration sequence across the local client, Kubernetes control plane, Azure cloud fabric, and NVIDIA GPU runtime:

```
[1. Kustomize Build] ──> [2. API Server / ReplicaSet] ──> [3. Cluster Autoscaler (0->1)]
                                                                     │
[6. vLLM Engine Warmup] <── [5. Blob CSI & ACR Pull] <── [4. GPU Driver Bootstrap]
         │
         ▼
[7. Probes & Traffic Ready (1/1 Running)]
```

##### 1. Client-Side Manifest Synthesis (Kustomize Engine)
* Kustomize reads `k8s/kustomization.yml` and evaluates the `configMapGenerator`.
* It calculates a cryptographic SHA hash of the literal variables (`SERVED_NAME=baidu/Unlimited-OCR`, `MAX_MODEL_LEN=32768`, etc.) and generates a content-hashed ConfigMap: `ocr-pipeline-config-fk72m8gm58`. This ensures that any future configuration update automatically forces a graceful rolling update of the pods without configuration drift.
* It injects unified labels (`system: aks-ocr-unlimitedocr`, `managed-by: tnm-deployment-agent`) into all selector matchers and metadata blocks.
* It transmits the compiled YAML bundle (`ConfigMap`, `Deployment`, `Service`, `ScaledObject`) to the Kubernetes API Server.

##### 2. Kubernetes API Server Ingestion & ReplicaSet Creation
* The Kubernetes API Server validates resource schemas against cluster CRDs.
* The `DeploymentController` detects the desired target of `1` replica and creates an active `ReplicaSet` (`ocr-vlm-deployment-97546c649`).
* The `ReplicaSet` creates the Pod object in `Pending` state.

##### 3. Scheduler Evaluation & Cluster Autoscaler Reaction (Scale from 0 $\rightarrow$ 1)
* The `kube-scheduler` attempts to place the Pod. It verifies node affinities (`kubernetes.azure.com/agentpool: gpunpa100`), tolerations (`sku=gpunpa100:NoSchedule`, `nvidia.com/gpu:NoSchedule`), and resource requests (`nvidia.com/gpu: 1`, `memory: 64Gi`, `cpu: 8`).
* If the GPU node pool was scaled to zero to save costs, the pod cannot immediately be placed and emits a `FailedScheduling` event.
* The **AKS Cluster Autoscaler** intercepts the pending pod and triggers cloud-level VMSS scale-up:
  ```text
  Normal  TriggeredScaleUp  cluster-autoscaler  pod triggered scale-up: [{aks-gpunpa100-...-vmss 0->1 (max: 4)}]
  ```
* Azure Virtual Machine Scale Sets (VMSS) begins provisioning a dedicated `Standard_NC24ads_A100_v4` VM in the cluster subnet (~2–3 minutes).

##### 4. Node Bootstrapping & Native GPU Driver Injection
* The Azure VM boots with the optimized AKS Ubuntu image.
* Thanks to the `--tags EnableManagedGPUExperience=true` flag on the nodepool, AKS cloud-init automatically installs official NVIDIA GRID/CUDA drivers, configures `containerd` with NVIDIA Container Runtime (OCI hooks), and launches the NVIDIA Kubernetes Device Plugin daemonset.
* The node registers with the AKS control plane as `Ready`, reporting allocatable GPU capacity:
  ```text
  status.allocatable.nvidia.com/gpu: 1
  ```

##### 5. Pod Binding, Azure Blob CSI Mount & Container Image Pull
* `kube-scheduler` immediately assigns the pending pod to the newly registered GPU node (`aks-gpunpa100-...-vmss000001`).
* **Storage Mount**: The Azure Blob CSI driver (`azureblob-fuse-premium`) mounts the Persistent Volume Claim (`model-weights-pvc`) to `/mnt/models` inside the pod container.
* **ACR Image Pull**: Kubelet pulls the container image (`${ACR_NAME}.azurecr.io/ocr-vlm-unlimitedocr:latest`, ~12–15 GB) from Azure Container Registry over the private Azure datacenter backbone and unpacks container layers.

##### 6. vLLM Engine Initialization & In-Memory Model Loading
* The container executes `entrypoint.sh`:
  * **Model Resolution**: Resolves the model path at `/mnt/models/baidu/Unlimited-OCR` with architecture `UnlimitedOCRForCausalLM`.
  * **Weight Loading**: Loads 6.24 GB of BF16 safetensors weights directly from the local Blob PVC mount into the A100's 80GB HBM2e VRAM in **~12 seconds**.
  * **Attention Engine**: Auto-selects PyTorch `FlexAttention` with Triton block masks for Reference Sliding Window Attention (R-SWA).
  * **Logits Processor**: Registers `NGramPerReqLogitsProcessor` for n-gram repetition suppression.
  * **KV Cache Profiling**: Allocates **1,017,920 tokens** in GPU KV cache memory (~58 GB VRAM), providing 31x max concurrency for 32K context streams.
  * **CUDA Graph Capture**: Warms up PyTorch kernels and captures **30 CUDA graphs** (19 piecewise prefill graphs + 11 full decode graphs).

##### 7. Probes Activation, Traffic Ingress & KEDA Scaling
* The vLLM HTTP engine starts listening on `0.0.0.0:8000`.
* The Kubernetes `startupProbe` and `readinessProbe` poll `http://localhost:8000/health`. Upon receiving HTTP 200, the pod transitions to **`1/1 Running`**.
* Kubernetes Service endpoints (`ocr-vlm-service` on ClusterIP and `ocr-vlm-lb-service` on Azure Load Balancer) activate and begin routing requests to the pod.
* The **KEDA ScaledObject** (`ocr-vlm-scaler`) connects to the Prometheus metric endpoint (`sum(vllm:num_requests_waiting)`) and begins evaluating queue depth every 10 seconds to scale GPU replicas dynamically under load.

---

### 5. End-to-End Testing & Validation

#### Inspect Logs
To ensure the vLLM server is running correctly, tail the logs for the deployment pod:

```bash
# ⚡ Via Makefile:
make logs

# Or directly:
kubectl logs -l app=ocr-vlm --tail=100 -f
```

#### Verify Cluster Status & Workloads
```bash
# ⚡ Via Makefile:
make status

# Or directly:
kubectl get pods,svc,scaledobjects -o wide
```

#### Verify Metrics & Scaling
Check if the metrics are flowing to Prometheus (it may take 2-3 minutes for the first scrape):
```bash
# Check A100 GPU Utilization
kubectl exec -it -n monitoring prometheus-prometheus-0 -- \
  promtool query instant http://localhost:9090 "avg(DCGM_FI_DEV_GPU_UTIL)"

# Check A100 vLLM Waiting Requests
kubectl exec -it -n monitoring prometheus-prometheus-0 -- \
  promtool query instant http://localhost:9090 "sum(vllm:num_requests_waiting)"
```

### 6. Ingress & Perimeter Exposure (Preview: Week 5)

> ℹ️ **Enterprise Perimeter & APIM (Week 5 Focus):**  
> In enterprise multi-tenant architectures, exposing GPU inference endpoints directly to the public internet introduces security risks (DDoS, unauthenticated compute exhaustion) and financial risks (unthrottled request bursts triggering runaway node autoscaling).  
> In **Week 5**, we will build a zero-trust enterprise perimeter using **Azure API Management (APIM)** in Internal VNet mode with Azure Application Gateway (WAF), Entra ID JWT verification, and tiered token rate-limiting.  
> 
> For our **Week 3** deployment, the service is exposed directly as a standard Kubernetes cluster/public API endpoint for local integration and our custom Rust API Gateway.

---

### 7. Monitoring & Dashboards (Grafana)

To see the real-time scaling events and GPU utilization, you can access the Grafana dashboard:

```bash
# ⚡ Via Makefile:
make port-forward-grafana
make grafana-password
```

Or manually:
1. **Port-forward the Grafana service**:
   ```bash
   kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80
   ```
2. **Retrieve the admin password**:
   ```bash
   kubectl get secret -n monitoring prometheus-grafana -o jsonpath="{.data.admin-password}" | base64 --decode ; echo
   ```
3. **Access the UI**:
   - Open your browser to: `http://localhost:3000`
   - **Username**: `admin`
   - **Password**: (The string retrieved above)

#### Best practice: Scale to zero

As GPU clusters are the most expensive component of the stack, it's convenient to be cautious about their usage. Since we have the Cluster Autoscaler enabled, we scale to zero by updating the `min-count`:

```bash
# ⚡ Via Makefile:
make scale-to-zero

# Or manually:
az aks nodepool update \
  --resource-group $RESOURCE_GROUP \
  --cluster-name $AKS_NAME \
  --name $GPU_NODEPOOL_NAME \
  --update-cluster-autoscaler \
  --min-count 0 \
  --max-count 4
```

### 7. Client Integration: Invoking the Raw vLLM Endpoint with Python

To test or integrate directly with the vLLM OpenAI-compatible endpoint (either via local port-forwarding or private cluster networking), use the following Python patterns.

#### 1. Establish Port-Forward (Local IDE Testing)
```bash
# ⚡ Via Makefile:
make port-forward-vlm

# Or directly:
kubectl port-forward svc/ocr-vlm-service 8000:8000
```

#### 2. Single-Page OCR Invocation with `sample_image.png`

> **Mandatory Recipe Rules:**
> 1. Prompt text **MUST** start with `<image>` (e.g. `<image>document parsing.`).
> 2. Pass `"skip_special_tokens": False` in `extra_body`.
> 3. Pass `"vllm_xargs": {"ngram_size": 35, "window_size": 128}` (use `window_size: 1024` for multi-page).

```python
import base64
import re
import time
from pathlib import Path
from openai import OpenAI

# 1. Initialize OpenAI client against the port-forwarded vLLM server
client = OpenAI(
    api_key="EMPTY",
    base_url="http://localhost:8000/v1",
    timeout=300.0,
)

def encode_image(image_path: str) -> str:
    """Encode local image file to base64 data URI."""
    mime_type = "image/png" if image_path.endswith(".png") else "image/jpeg"
    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"

def postprocess_unlimited_ocr(raw_text: str) -> str:
    """
    Unwrap grounding reference tags (<|ref|>...<|/ref|>) and 
    strip coordinate detection bounding boxes (<|det|>...<|/det|>).
    """
    # Remove bounding box detection coordinates
    cleaned = re.sub(r"<\|det\|>.*?<\|/det\|>", "", raw_text, flags=re.DOTALL)
    # Unwrap reference text contents
    cleaned = re.sub(r"<\|ref\|>(.*?)<\|/ref\|>", r"\1", cleaned, flags=re.DOTALL)
    return cleaned.strip()

# 2. Encode local sample image
image_path = "sample_image.png"
image_uri = encode_image(image_path)

messages = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "<image>document parsing."},
            {"type": "image_url", "image_url": {"url": image_uri}},
        ],
    }
]

# 3. Dispatch synchronous inference request
start_time = time.perf_counter()
response = client.chat.completions.create(
    model="baidu/Unlimited-OCR",
    messages=messages,
    max_tokens=8192,
    temperature=0.0,
    extra_body={
        "skip_special_tokens": False,
        "vllm_xargs": {
            "ngram_size": 35,
            "window_size": 128,  # 128 for single page; 1024 for multi-page
        },
    },
)
latency = time.perf_counter() - start_time
raw_output = response.choices[0].message.content
clean_markdown = postprocess_unlimited_ocr(raw_output)

print(f"⚡ Inferred on NVIDIA A100 in {latency:.2f}s!")
print(f"📄 Clean Markdown Document:\n\n{clean_markdown}")
```

##### Expected Cleaned Markdown Output:
```markdown
2 TECHNICAL PERFORMANCE | AI INDEX REPORT 2026

2.4 Reasoning

Reasoning benchmarks assess whether models can solve problems that require abstraction and generalization across domains and formats. As performance has improved, newer benchmarks aim to distinguish genuine problem-solving from performance that is driven by memorization or prompt familiarity. However, because models can also produce errors in otherwise fluent responses, efforts are ramping up to measure these error rates alongside reasoning limitations. The AI Index tracks those benchmarks on factual reliability and error rates in Chapter 3. Across the benchmarks in this section, leading models perform well on many tasks but still show gaps on the more difficult items.

General Reasoning

General reasoning refers to a model's ability to solve unfamiliar problems by applying rules and combining evidence, rather than relying on domain knowledge or memorized patterns. The benchmarks discussed below span multiple domains and tasks and are designed to test multistep inference. One example is multidigit arithmetic, such as long integer multiplication, to test whether models can execute consistent stepwise computation rather than produce plausible-looking outputs. Other more complex benchmarks extend this idea to multimodal settings, where models must integrate text with diagrams or plots to reach the correct answer.

MMMU: A Massive Multi-discipline Multimodal Understanding and Reasoning Benchmark for Expert AGI

MMMU evaluates multimodal reasoning on college-level subject questions that combine text with visuals such as diagrams, charts, tables, and equations. Some example tasks include extracting constraints from a table and applying them to a word problem, or using a diagram to answer a domain-specific question in areas like engineering or medicine.

As of February 2026, the leading model, Gemini 3.1 Pro Preview, scored 88.2% on MMMU and within 0.4 percentage points of the best human expert reference (Figure 2.4.1). Other Gemini variants follow closely, including Gemini 3 Flash (87.6%) and Gemini 3 Pro (87.5%), while GPT-5.2 scores 86.7%. The 2026 models trail behind with Kimi K2.5 at 84.3% and Claude Opus 4.6 (Thinking) at 83.9%.

93
```

#### 3. Multi-Page / Long-Document Invocation (PDF / Batch of Pages)

For multi-page documents (where the model leverages its **Reference Sliding Window Attention** across up to 32K context), increase `window_size` to `1024` and supply all page images in the message array:

```python
import base64
from openai import OpenAI

client = OpenAI(api_key="EMPTY", base_url="http://localhost:8000/v1")

def run_multipage_ocr(page_image_paths: list[str]) -> str:
    content_payload = [{"type": "text", "text": "<image>document parsing."}]
    
    for path in page_image_paths:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        content_payload.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
        })
    
    response = client.chat.completions.create(
        model="baidu/Unlimited-OCR",
        messages=[{"role": "user", "content": content_payload}],
        max_tokens=16384,
        temperature=0.0,
        extra_body={
            "skip_special_tokens": False,
            "vllm_xargs": {
                "ngram_size": 35,
                "window_size": 1024,  # Expanded sliding window for cross-page coherence
            },
        },
    )
    return response.choices[0].message.content
```

#### 4. Raw HTTP Invocation with `httpx` / `curl`

If invoking via standard REST without the OpenAI SDK:

```python
import httpx

payload = {
    "model": "baidu/Unlimited-OCR",
    "messages": [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "<image>document parsing."},
                {
                    "type": "image_url",
                    "image_url": {"url": "https://huggingface.co/baidu/Unlimited-OCR/resolve/main/assets/baidu.png"}
                }
            ]
        }
    ],
    "max_tokens": 8192,
    "temperature": 0.0,
    "skip_special_tokens": False,
    "vllm_xargs": {"ngram_size": 35, "window_size": 128}
}

response = httpx.post("http://localhost:8000/v1/chat/completions", json=payload, timeout=300.0)
print(response.json()["choices"][0]["message"]["content"])
```

---

## 🔍 Monitoring & Resources
*   [Baidu Unlimited-OCR GitHub](https://github.com/baidu/Unlimited-OCR)
*   [HuggingFace: baidu/Unlimited-OCR](https://huggingface.co/baidu/Unlimited-OCR)
*   [vLLM Unlimited-OCR Serving Recipe](https://recipes.vllm.ai/baidu/Unlimited-OCR)
*   [vLLM Multimodal Inference Guide](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html)
*   [KEDA Azure Prometheus Scaler](https://keda.sh/docs/scalers/prometheus/)
*   [Azure AKS Managed GPU Drivers Guide](https://learn.microsoft.com/en-us/azure/aks/gpu-cluster)
