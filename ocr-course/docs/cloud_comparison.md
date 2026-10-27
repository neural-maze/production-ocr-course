# ☁️ Cloud Provider Architecture Comparison: AKS vs. GKE

This document provides an architecture and operational comparison between the **Azure Kubernetes Service (AKS)** and **Google Kubernetes Engine (GKE)** implementations for this SLM-Powered OCR repository.

---

## 🎯 Architectural Overview: Shared Foundation

Both cloud deployments share an identical core application stack and metric-driven scaling philosophy:

* **Ingest Gateway**: High-concurrency Rust Producer API (`ocr-api-rust`) running on CPU-optimized nodes (`apinp`).
* **State Store**: High-memory Redis instance (`ocr-redis`) acting as a temporary document store and task queue.
* **Layout Engine**: Asynchronous Python Consumer Worker (`ocr-worker-rt`) running layout analysis (**PP-DocLayoutV3**) on T4 GPUs (`gpunpt4`) with dynamic collector batching.
* **SLM Inference Engine**: **vLLM** serving **Qwen 3.5 (4B)** on A100 80GB GPUs (`gpunpa100`) with Multi-Token Prediction (MTP) and continuous batching.
* **Autoscaling Mechanics**: **KEDA** scaled objects monitoring Redis list length (`ocr_tasks`) and Prometheus metrics (`vllm:num_requests_waiting`).
* **Zero-Copy Handoff**: Document buffers rasterized directly into `/dev/shm` Linux shared memory.

---

## 📊 Technical Discrepancies Matrix

The following table summarizes the provider-specific infrastructure configurations and manifest differences:

| Architectural Component | Azure Kubernetes Service (AKS) | Google Kubernetes Engine (GKE) | Technical Rationale & Impact |
| :--- | :--- | :--- | :--- |
| **CLI & Auth** | `az cli` / `az login` | `gcloud sdk` / `gcloud auth login` | Cloud-native CLI commands for cluster management and credential fetching. |
| **Container Registry** | **Azure Container Registry (ACR)**<br>`acrocrinference.azurecr.io` | **Google Artifact Registry (AR)**<br>`us-central1-docker.pkg.dev/...` | Regional image repository host per cloud ecosystem. |
| **GPU Driver Lifecycle** | Helm-installed **NVIDIA GPU Operator** (`k8s/aks/infra/gpu-operator-values.yaml`) | **Natively Managed GPU Drivers** (`gpu-driver-version=default` node pool flag) | GKE compiles drivers and manages device plugin daemonsets natively, removing manual Helm operator overhead. |
| **A100 GPU Machine Type** | `Standard_NC24ads_A100_v4` (24 vCPU, 220GB RAM, 1x A100 80GB) | `a2-ultragpu-1g` (12 vCPU, 170GB RAM, 1x A100 80GB) | Specialized compute instances tailored for 80GB VRAM requirements. |
| **T4 GPU Machine Type** | `Standard_NC16as_T4_v3` (16 vCPU, 64GB RAM, 1x T4 16GB) | `n1-standard-4` + `--accelerator type=nvidia-tesla-t4,count=1` | Modular accelerator attachment on GKE vs fixed GPU SKU on AKS. |
| **GPU Node Selection** | `kubernetes.azure.com/agentpool` | `cloud.google.com/gke-nodepool` | Cloud controller manager node labeling conventions. |
| **GPU Node Taints & Tolerations** | Custom taints: `sku=gpunpa100:NoSchedule` / `sku=gpunpt4:NoSchedule` | Standard GKE GPU taint: `nvidia.com/gpu=present:NoSchedule` | GKE automatically taints GPU nodes and handles scheduling when pods specify `nvidia.com/gpu` limits. |
| **Shared Storage Class (RWX)** | **Azure Blob CSI Driver**<br>`storageClassName: azureblob-fuse-premium` | **Google Cloud Filestore CSI Driver**<br>`storageClassName: standard-rwx` | Azure Blob Fuse CSI allows 300Gi allocations; GKE Filestore basic-hdd requires a minimum 1Ti allocation. |
| **Internal Load Balancer** | `service.beta.kubernetes.io/azure-load-balancer-internal: "true"` | `networking.gke.io/load-balancer-type: "Internal"` | Provider-specific cloud controller manager annotations for private IP provisioning. |
| **Enterprise Exposure & Gateway** | **Azure API Management (APIM)** in Internal VNet Mode with XML policies (`apim-policy.xml`) | **Google Cloud API Gateway** / **Cloud Armor** + Private Service Connect | Cloud-native API gateway, token validation (JWT), and rate-limiting at the network boundary. |

---

## 🔍 Deep-Dive Audit: Verification of GKE Implementation

### 1. Storage Provisioning (`pvc.yaml`)
* **AKS (`k8s/aks/infra/provisioning/pvc.yaml`)**:
  ```yaml
  storageClassName: azureblob-fuse-premium
  resources:
    requests:
      storage: 300Gi
  ```
* **GKE (`k8s/gke/infra/provisioning/pvc.yaml`)**:
  ```yaml
  storageClassName: standard-rwx
  resources:
    requests:
      storage: 1Ti
  ```
* **Audit Finding**: Correctly updated. GCP Filestore basic-hdd instances require a minimum capacity of 1Ti. The GKE PVC reflects this requirement while preserving `ReadWriteMany` (RWX) support.

### 2. Node Selection & GPU Tolerations (`deployment-vlm.yml` & `deployment-api.yml`)
* **AKS Target**: Uses `kubernetes.azure.com/agentpool: gpunpa100` and tolerates `sku=gpunpa100:NoSchedule`.
* **GKE Target**: Uses `cloud.google.com/gke-nodepool: gpunpa100` and tolerates GKE's default `nvidia.com/gpu=present:NoSchedule` taint.
* **Audit Finding**: Correctly updated. GKE worker pods schedule seamlessly onto tainted GPU node pools without scheduling deadlocks.

### 3. Service Exposure (`service.yml`)
* **AKS (`k8s/aks/networking/service.yml`)**:
  ```yaml
  annotations:
    service.beta.kubernetes.io/azure-load-balancer-internal: "true"
  ```
* **GKE (`k8s/gke/networking/service.yml`)**:
  ```yaml
  annotations:
    networking.gke.io/load-balancer-type: "Internal"
  ```
* **Audit Finding**: Correctly updated. Both services provision internal private IP addresses within their respective cloud virtual networks (VNet / VPC).

### 4. GPU Driver Management
* **AKS**: Requires declarative configuration of the NVIDIA GPU Operator via `k8s/aks/infra/gpu-operator-values.yaml` to handle kernel module compilation and driver loading on tainted nodes.
* **GKE**: Utilizes GKE's native managed driver installation (`gpu-driver-version=default`). The driver installer daemonsets are managed by Google Cloud, eliminating `gpu-operator-values.yaml` in the GKE manifests.

---

## 📁 Repository Manifest Mapping

```text
k8s/
├── aks/                         # Azure-Specific Manifest Overlays
│   ├── apps/
│   │   ├── deployment-api.yml   # AKS image tags & agentpool nodeSelectors
│   │   ├── deployment-vlm.yml   # AKS image tags & A100 agentpool nodeSelectors
│   │   ├── keda-scaler.yml      # KEDA autoscaling rules
│   │   └── redis-deployment.yml # Redis state store deployment
│   ├── infra/
│   │   ├── gpu-operator-values.yaml # NVIDIA GPU Operator tolerations for AKS
│   │   └── provisioning/
│   │       ├── ingest-job.yaml  # Model downloader job
│   │       └── pvc.yaml         # azureblob-fuse-premium PVC (300Gi)
│   ├── networking/
│   │   ├── apim-policy.xml      # Azure APIM JWT & rate-limit policies
│   │   └── service.yml          # Azure Internal Load Balancer service
│   └── kustomization.yml        # AKS Kustomize entrypoint
│
└── gke/                         # GCP-Specific Manifest Overlays
    ├── apps/
    │   ├── deployment-api.yml   # Artifact Registry tags & gke-nodepool selectors
    │   ├── deployment-vlm.yml   # Artifact Registry tags & gke-nodepool selectors
    │   ├── keda-scaler.yml      # KEDA autoscaling rules
    │   └── redis-deployment.yml # Redis state store deployment
    ├── infra/
    │   └── provisioning/
    │       ├── ingest-job.yaml  # Model downloader job
    │       └── pvc.yaml         # standard-rwx Filestore PVC (1Ti)
    ├── networking/
    │   └── service.yml          # GKE Internal Load Balancer service
    └── kustomization.yml        # GKE Kustomize entrypoint
```

---

## 📚 Deployment Guides Reference

* 📘 [Azure Kubernetes Service (AKS) Deployment Lifecycle](file:///Users/hedrergudene/Documents/GitHub/aks-ocr-rt-dpl/docs/aks_deployment.md)
* 📗 [Google Kubernetes Engine (GKE) Deployment Lifecycle](file:///Users/hedrergudene/Documents/GitHub/aks-ocr-rt-dpl/docs/gke_deployment.md)
