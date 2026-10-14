# ==============================================================================
# 🚀 THE NEURAL MAZE — AKS, REDIS QUEUE & GLM-OCR ASYNC PIPELINE AUTOMATION
# ==============================================================================
# Stack: Azure Kubernetes Service (AKS) + Redis + Rust (Axum) Producer
#        + GLM-OCR SDK (PP-DocLayoutV3) Python Consumer + vLLM (Qwen 3.5 2B)
# ==============================================================================

SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

# --- Configuration & Environment Variables ---
LOCATION          ?= francecentral
RESOURCE_GROUP    ?= week3-dpl-rg
SUBSCRIPTION_ID   ?= <YOUR_SUBSCRIPTION_ID>
AKS_NAME          ?= akstnmweek3
ACR_NAME          ?= acrtnmweek3

# GPU Node Pool (vLLM Qwen 3.5 2B Engine)
GPU_NODEPOOL_NAME ?= gpunpa100
GPU_VM_SIZE       ?= Standard_NC24ads_A100_v4

# T4 Node Pool (GLM-OCR Consumer Worker: PP-DocLayoutV3 + Dynamic Batching)
T4_NODEPOOL_NAME  ?= gpunpt4
T4_VM_SIZE        ?= Standard_NC16as_T4_v3

# CPU Node Pool (Rust Producer API & Redis Queue)
CPU_NODEPOOL_NAME ?= cpunp
CPU_VM_SIZE       ?= Standard_D8ds_v6

# Container Images
VLM_IMAGE_NAME    ?= ocr-vlm-qwen
API_IMAGE_NAME    ?= ocr-api-rust
WORKER_IMAGE_NAME ?= ocr-worker-rt
IMAGE_TAG         ?= latest
MODEL_IDS         ?= Qwen/Qwen3.5-2B,PaddlePaddle/PP-DocLayoutV3_safetensors

# Colors for terminal styling
CYAN   := \033[36m
GREEN  := \033[32m
YELLOW := \033[33m
RED    := \033[31m
RESET  := \033[0m
BOLD   := \033[1m

# ==============================================================================
# 📖 HELP / TARGET DISCOVERY
# ==============================================================================

.PHONY: help
help: ## Show this help message
	@echo -e ""
	@echo -e "$(CYAN)$(BOLD)==============================================================================$(RESET)"
	@echo -e "$(CYAN)$(BOLD) 🚀 The Neural Maze — AKS + Redis + GLM-OCR + vLLM Async Pipeline Makefile$(RESET)"
	@echo -e "$(CYAN)$(BOLD)==============================================================================$(RESET)"
	@echo -e "Usage: $(YELLOW)make [target]$(RESET) (e.g. $(YELLOW)make infra-up$(RESET) or $(YELLOW)make deploy$(RESET))"
	@echo -e ""
	@echo -e "$(BOLD)Available Targets:$(RESET)"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  $(CYAN)%-24s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo -e ""

# ==============================================================================
# 🔑 1. PREREQUISITES & AUTHENTICATION
# ==============================================================================

.PHONY: check-cli
check-cli: ## Verify required local CLI binaries (az, kubectl, helm)
	@echo -e "$(YELLOW)Checking required CLI tooling...$(RESET)"
	@command -v az >/dev/null 2>&1 || { echo -e "$(RED)Error: azure-cli is not installed.$(RESET)"; exit 1; }
	@command -v kubectl >/dev/null 2>&1 || { echo -e "$(RED)Error: kubectl is not installed.$(RESET)"; exit 1; }
	@command -v helm >/dev/null 2>&1 || { echo -e "$(RED)Error: helm is not installed.$(RESET)"; exit 1; }
	@echo -e "$(GREEN)✅ All CLI tools (az, kubectl, helm) are present.$(RESET)"

.PHONY: auth
auth: check-cli ## Authenticate Azure CLI and register Resource Providers
	@echo -e "$(YELLOW)🔑 Authenticating and setting subscription: $(SUBSCRIPTION_ID)...$(RESET)"
	az account set --subscription "$(SUBSCRIPTION_ID)"
	@echo -e "$(YELLOW)Registering required Azure resource providers...$(RESET)"
	az provider register --namespace Microsoft.ContainerRegistry
	az provider register --namespace Microsoft.ContainerService
	az provider register --namespace Microsoft.Storage
	@echo -e "$(GREEN)✅ Subscription active:$(RESET)"
	az account show --output table

.PHONY: check-quota
check-quota: ## Check current vCPU / GPU quota for A100 and T4 families in target region
	@echo -e "$(YELLOW)🔍 Checking compute quota for A100 & T4 GPU families in $(LOCATION)...$(RESET)"
	@az vm list-usage --location "$(LOCATION)" --query "[?contains(name.value, 'NC') || contains(name.localizedValue, 'NC') || contains(name.localizedValue, 'A100') || contains(name.localizedValue, 'T4') || contains(name.value, 'StandardNC')]" -o table || az vm list-usage --location "$(LOCATION)" -o table | head -n 30

# ==============================================================================
# 🏗️ 2. INFRASTRUCTURE SETUP (AKS, ACR, TRIPLE NODE POOLS)
# ==============================================================================

.PHONY: infra-up
infra-up: auth ## Create Resource Group, ACR, and AKS Cluster
	@echo -e "$(YELLOW)🏗️  Creating Resource Group: $(RESOURCE_GROUP)...$(RESET)"
	az group create --name $(RESOURCE_GROUP) --location $(LOCATION)
	@echo -e "$(YELLOW)📦 Creating Azure Container Registry: $(ACR_NAME)...$(RESET)"
	az acr create -g $(RESOURCE_GROUP) -n $(ACR_NAME) --sku Premium --location $(LOCATION)
	@echo -e "$(YELLOW)☸️  Creating AKS Cluster: $(AKS_NAME) with Blob Driver & Managed Identity...$(RESET)"
	az aks create \
		--resource-group $(RESOURCE_GROUP) \
		--location $(LOCATION) \
		--name $(AKS_NAME) \
		--attach-acr $(ACR_NAME) \
		--node-count 1 \
		--enable-managed-identity \
		--enable-blob-driver \
		--generate-ssh-keys
	@echo -e "$(YELLOW)📥 Downloading AKS credentials to local kubeconfig...$(RESET)"
	az aks get-credentials --resource-group $(RESOURCE_GROUP) --name $(AKS_NAME) --overwrite-existing
	@echo -e "$(GREEN)✅ Core infrastructure provisioned successfully.$(RESET)"

.PHONY: nodepool-gpu
nodepool-gpu: ## Add A100 GPU Node Pool (Standard_NC24ads_A100_v4) for vLLM
	@echo -e "$(YELLOW)⚡ Provisioning A100 GPU Node Pool: $(GPU_NODEPOOL_NAME)...$(RESET)"
	az aks nodepool add \
		--resource-group $(RESOURCE_GROUP) \
		--cluster-name $(AKS_NAME) \
		--name $(GPU_NODEPOOL_NAME) \
		--node-vm-size $(GPU_VM_SIZE) \
		--node-count 1 \
		--enable-cluster-autoscaler \
		--min-count 0 \
		--max-count 4 \
		--node-taints sku=$(GPU_NODEPOOL_NAME):NoSchedule \
		--tags EnableManagedGPUExperience=true
	@echo -e "$(GREEN)✅ GPU node pool $(GPU_NODEPOOL_NAME) created with native driver management.$(RESET)"

.PHONY: nodepool-t4
nodepool-t4: ## Add T4 GPU Node Pool (Standard_NC16as_T4_v3) for the GLM-OCR consumer worker
	@echo -e "$(YELLOW)⚡ Provisioning T4 GPU Node Pool: $(T4_NODEPOOL_NAME)...$(RESET)"
	az aks nodepool add \
		--resource-group $(RESOURCE_GROUP) \
		--cluster-name $(AKS_NAME) \
		--name $(T4_NODEPOOL_NAME) \
		--node-vm-size $(T4_VM_SIZE) \
		--node-count 1 \
		--enable-cluster-autoscaler \
		--min-count 0 \
		--max-count 10 \
		--node-taints sku=$(T4_NODEPOOL_NAME):NoSchedule \
		--tags EnableManagedGPUExperience=true
	@echo -e "$(GREEN)✅ T4 node pool $(T4_NODEPOOL_NAME) created with native driver management.$(RESET)"

.PHONY: nodepool-cpu
nodepool-cpu: ## Add Compute-Optimized CPU Node Pool (Standard_D8ds_v6) for the Rust Producer & Redis
	@echo -e "$(YELLOW)⚡ Provisioning Compute-Optimized CPU Node Pool: $(CPU_NODEPOOL_NAME)...$(RESET)"
	az aks nodepool add \
		--resource-group $(RESOURCE_GROUP) \
		--cluster-name $(AKS_NAME) \
		--name $(CPU_NODEPOOL_NAME) \
		--node-vm-size $(CPU_VM_SIZE) \
		--node-count 1 \
		--enable-cluster-autoscaler \
		--min-count 1 \
		--max-count 4 \
		--node-taints sku=$(CPU_NODEPOOL_NAME):NoSchedule
	@echo -e "$(GREEN)✅ CPU node pool $(CPU_NODEPOOL_NAME) created successfully.$(RESET)"

.PHONY: verify-gpu
verify-gpu: ## Check allocatable GPU capacity across cluster nodes
	@echo -e "$(YELLOW)🔍 Querying allocatable GPU capacity on cluster nodes...$(RESET)"
	kubectl get nodes "-o=custom-columns=NAME:.metadata.name,GPU_CAPACITY:.status.capacity.nvidia\.com/gpu,GPU_ALLOCATABLE:.status.allocatable.nvidia\.com/gpu"

.PHONY: scale-to-zero
scale-to-zero: ## Scale the A100 and T4 GPU node pools to 0 replicas to save cost
	@echo -e "$(YELLOW)🛑 Scaling A100 GPU node pool $(GPU_NODEPOOL_NAME) to zero min-replicas...$(RESET)"
	az aks nodepool update \
		--resource-group $(RESOURCE_GROUP) \
		--cluster-name $(AKS_NAME) \
		--name $(GPU_NODEPOOL_NAME) \
		--update-cluster-autoscaler \
		--min-count 0 \
		--max-count 4
	@echo -e "$(YELLOW)🛑 Scaling T4 GPU node pool $(T4_NODEPOOL_NAME) to zero min-replicas...$(RESET)"
	az aks nodepool update \
		--resource-group $(RESOURCE_GROUP) \
		--cluster-name $(AKS_NAME) \
		--name $(T4_NODEPOOL_NAME) \
		--update-cluster-autoscaler \
		--min-count 0 \
		--max-count 10
	@echo -e "$(GREEN)✅ Both GPU node pools set to scale-to-zero mode. (Redis + Rust API on $(CPU_NODEPOOL_NAME) stay warm.)$(RESET)"

# ==============================================================================
# 💾 3. STORAGE & MODEL WEIGHT INGESTION
# ==============================================================================

.PHONY: storage-pvc
storage-pvc: ## Apply Azure Blob PersistentVolumeClaim for model storage
	@echo -e "$(YELLOW)💾 Provisioning Blob Storage PVC (model-weights-pvc)...$(RESET)"
	kubectl apply -f k8s/infra/pvc.yaml

.PHONY: ingest-weights
ingest-weights: storage-pvc ## Trigger Datacenter Model Ingestion Job for Qwen 3.5 2B + PP-DocLayoutV3
	@echo -e "$(YELLOW)🚀 Triggering in-cluster ingestion job for $(MODEL_IDS)...$(RESET)"
	@kubectl delete job model-weight-ingest --ignore-not-found=true
	kubectl apply -f k8s/infra/ingest-job.yaml
	@echo -e "$(GREEN)✅ Ingestion job launched. Stream logs with: make ingest-logs$(RESET)"

.PHONY: ingest-logs
ingest-logs: ## Follow the model weight ingestion download logs
	kubectl logs -f job/model-weight-ingest

# ==============================================================================
# 🐳 4. CONTAINER BUILDS & ACR PUSH
# ==============================================================================

.PHONY: build-vlm
build-vlm: ## Build and push the vLLM (Qwen 3.5 2B) server container image via ACR
	@echo -e "$(YELLOW)🐳 Building vLLM container $(VLM_IMAGE_NAME):$(IMAGE_TAG) on ACR ($(ACR_NAME))...$(RESET)"
	az acr build --registry $(ACR_NAME) --image $(VLM_IMAGE_NAME):$(IMAGE_TAG) ./server
	@echo -e "$(GREEN)✅ vLLM container built and pushed.$(RESET)"

.PHONY: build-api
build-api: ## Build and push the Rust Producer API container image via ACR
	@echo -e "$(YELLOW)🦀 Building Rust Producer container $(API_IMAGE_NAME):$(IMAGE_TAG) on ACR ($(ACR_NAME))...$(RESET)"
	az acr build --registry $(ACR_NAME) --image $(API_IMAGE_NAME):$(IMAGE_TAG) ./client_rt_producer
	@echo -e "$(GREEN)✅ Rust Producer container built and pushed.$(RESET)"

.PHONY: build-worker
build-worker: ## Build and push the GLM-OCR Python Consumer Worker container image via ACR
	@echo -e "$(YELLOW)🐍 Building Consumer Worker container $(WORKER_IMAGE_NAME):$(IMAGE_TAG) on ACR ($(ACR_NAME))...$(RESET)"
	az acr build --registry $(ACR_NAME) --image $(WORKER_IMAGE_NAME):$(IMAGE_TAG) ./client_rt_consumer
	@echo -e "$(GREEN)✅ Consumer Worker container built and pushed.$(RESET)"

.PHONY: build-all
build-all: build-vlm build-api build-worker ## Build all container images (vLLM + Rust Producer + Python Consumer)

# ==============================================================================
# 🚀 5. FULL STACK DEPLOYMENT (KEDA, PROMETHEUS, WORKLOADS)
# ==============================================================================

.PHONY: install-keda
install-keda: ## Install or upgrade KEDA via Helm
	@echo -e "$(YELLOW)⚙️  Installing KEDA autoscaler...$(RESET)"
	helm repo add kedacore https://kedacore.github.io/charts --force-update
	helm upgrade --install keda kedacore/keda -n keda --create-namespace
	@echo -e "$(GREEN)✅ KEDA ready.$(RESET)"

.PHONY: install-monitoring
install-monitoring: ## Install Prometheus Stack & Grafana for GPU and vLLM metric scraping
	@echo -e "$(YELLOW)📊 Installing Prometheus & Grafana stack...$(RESET)"
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
	helm repo update
	@kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
	helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
		--namespace monitoring \
		--set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
		--set grafana.enabled=true
	@echo -e "$(GREEN)✅ Monitoring stack installed.$(RESET)"

.PHONY: deploy
deploy: ## Deploy full stack (Redis + Rust Producer + Worker + vLLM + KEDA) via Kustomize
	@echo -e "$(YELLOW)🚀 Deploying all workloads via Kustomize...$(RESET)"
	kubectl apply -k k8s/
	@echo -e "$(GREEN)✅ Manifests applied successfully.$(RESET)"

.PHONY: undeploy
undeploy: ## Remove all workloads from the cluster
	@echo -e "$(YELLOW)🗑️  Removing all workloads...$(RESET)"
	kubectl delete -k k8s/ --ignore-not-found=true
	@echo -e "$(GREEN)✅ Workloads removed.$(RESET)"

# ==============================================================================
# 📊 6. OBSERVABILITY & TESTING
# ==============================================================================

.PHONY: status
status: ## Check the status of all pods, services, and ScaledObjects
	@echo -e "$(CYAN)=== Pods ===$(RESET)"
	kubectl get pods -o wide
	@echo -e "\n$(CYAN)=== Services ===$(RESET)"
	kubectl get svc
	@echo -e "\n$(CYAN)=== KEDA ScaledObjects ===$(RESET)"
	kubectl get scaledobjects -A

.PHONY: logs-api
logs-api: ## Tail real-time logs from the Rust Producer API pod
	kubectl logs -l app=ocr-api --tail=100 -f

.PHONY: logs-worker
logs-worker: ## Tail real-time logs from the GLM-OCR Consumer Worker pod
	kubectl logs -l app=ocr-worker-rt --tail=100 -f

.PHONY: logs-vlm
logs-vlm: ## Tail real-time logs from the vLLM (Qwen 3.5 2B) server pod
	kubectl logs -l app=ocr-vlm --tail=100 -f

.PHONY: port-forward-api
port-forward-api: ## Port-forward the Rust Producer API locally to localhost:5000
	@echo -e "$(YELLOW)🔌 Port-forwarding Rust Producer API to http://localhost:5000...$(RESET)"
	kubectl port-forward svc/ocr-api-service 5000:80

.PHONY: port-forward-vlm
port-forward-vlm: ## Port-forward vLLM OCR server locally to localhost:8000
	@echo -e "$(YELLOW)🔌 Port-forwarding vLLM service to http://localhost:8000...$(RESET)"
	kubectl port-forward svc/ocr-vlm-service 8000:8000

.PHONY: port-forward-grafana
port-forward-grafana: ## Port-forward Grafana dashboard to http://localhost:3000
	@echo -e "$(YELLOW)📊 Port-forwarding Grafana to http://localhost:3000...$(RESET)"
	kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80

.PHONY: grafana-password
grafana-password: ## Retrieve the Grafana admin password
	@echo -n "Grafana Admin Password: "
	@kubectl get secret -n monitoring prometheus-grafana -o jsonpath="{.data.admin-password}" | base64 --decode
	@echo ""

.PHONY: test-submit
test-submit: ## Submit sample_image.png to the Rust Producer and poll until the async task completes
	@echo -e "$(YELLOW)🧪 Submitting sample_image.png to Rust Producer (http://localhost:5000/process)...$(RESET)"
	@python3 -c '\
import json, time, urllib.request;\
import subprocess;\
task = json.loads(subprocess.check_output(["curl", "-s", "-X", "POST", "http://localhost:5000/process", "-F", "file=@sample_image.png"]));\
task_id = task["task_id"];\
print(f"🚀 Task submitted: {task_id} (status: {task[\"status\"]})");\
start = time.time();\
while True:\
    status = json.loads(subprocess.check_output(["curl", "-s", f"http://localhost:5000/status/{task_id}"]));\
    print(f"⏳ status: {status[\"status\"]} (elapsed: {time.time()-start:.1f}s)");\
    if status["status"] == "done":\
        print("✅ OCR complete!");\
        print(status["result"]["markdown"][:1000] + "...");\
        break;\
    if status["status"] == "failed":\
        print(f"❌ Task failed: {status.get(\"error\")}");\
        break;\
    time.sleep(0.5);\
'

# ==============================================================================
# 🧹 7. CLEANUP & TEARDOWN
# ==============================================================================

.PHONY: clean-jobs
clean-jobs: ## Delete transient ingestion and inspection jobs
	@echo -e "$(YELLOW)🧹 Cleaning up completed jobs and inspector pods...$(RESET)"
	kubectl delete job model-weight-ingest --ignore-not-found=true

.PHONY: teardown
teardown: ## Fully delete the Azure Resource Group and all associated cloud resources
	@echo -e "$(RED)$(BOLD)⚠️  WARNING: This will permanently delete Resource Group $(RESOURCE_GROUP) and all its resources!$(RESET)"
	@read -p "Are you sure you want to proceed? [y/N]: " confirm && [[ $$confirm == [yY] || $$confirm == [yY][eE][sS] ]] || { echo "Aborted."; exit 1; }
	az group delete --name $(RESOURCE_GROUP) --yes --no-wait
	@echo -e "$(GREEN)✅ Resource group deletion initiated.$(RESET)"
