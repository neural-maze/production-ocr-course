# ==============================================================================
# 🚀 THE NEURAL MAZE — PRODUCTION OCR PIPELINE ON AKS
# ==============================================================================
# Stack: AKS + Redis + Rust (Axum) Producer + GLM-OCR SDK (PP-DocLayoutV3) Worker
#        + vLLM (Qwen 3.5 2B) + MCP Server + Azure APIM
#
# Quickstart:  cp .env.example .env   # fill in SUBSCRIPTION_ID and ACR_NAME
#              make up                # provision + build + deploy everything
#              make test              # send samples/sample_image.png end to end
#              make down              # delete every Azure resource
# ==============================================================================

SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

-include .env
export

# --- Configuration (override in .env or per command) ---
LOCATION          ?= francecentral
RESOURCE_GROUP    ?= ocr-course-rg
SUBSCRIPTION_ID   ?=
AKS_NAME          ?= aks-ocr-course
ACR_NAME          ?=

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
MCP_IMAGE_NAME    ?= ocr-mcp-server
IMAGE_TAG         ?= latest

# Enterprise Gateway (Azure API Management)
APIM_NAME            ?= apim-ocr-service
APIM_SKU             ?= Developer
APIM_PUBLISHER_EMAIL ?= admin@yourcompany.com
APIM_PUBLISHER_NAME  ?= OCR Team
APIM_SUBNET_NAME     ?= apim-subnet
APIM_SUBNET_PREFIX   ?= 10.225.0.0/27

K8S_DIR  := deploy/k8s
REGISTRY := $(ACR_NAME).azurecr.io
RENDER   := kubectl kustomize $(K8S_DIR) | sed -e "s\#__REGISTRY__\#$(REGISTRY)\#g" -e "s\#__TAG__\#$(IMAGE_TAG)\#g"

# Colors for terminal styling
CYAN   := \033[36m
GREEN  := \033[32m
YELLOW := \033[33m
RED    := \033[31m
RESET  := \033[0m
BOLD   := \033[1m

# ==============================================================================
# 📖 HELP
# ==============================================================================

.PHONY: help
help: ## Show this help message
	@echo -e ""
	@echo -e "$(CYAN)$(BOLD) 🚀 The Neural Maze — Production OCR Pipeline$(RESET)"
	@echo -e ""
	@echo -e "Usage: $(YELLOW)make [target]$(RESET)   Quickstart: $(YELLOW)make up$(RESET) → $(YELLOW)make test$(RESET) → $(YELLOW)make down$(RESET)"
	@awk 'BEGIN {FS = ":.*?## "} \
		/^# [0-9]+\. / {printf "\n$(BOLD)%s$(RESET)\n", substr($$0, 3)} \
		/^[a-zA-Z0-9_-]+:.*?## / {printf "  $(CYAN)%-22s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo -e ""

# ==============================================================================
# 0. ONE-SHOT LIFECYCLE
# ==============================================================================

.PHONY: up
up: infra nodepools platform ingest-weights build ingest-wait deploy ## Provision, build and deploy the whole pipeline (idempotent)
	@echo -e "$(GREEN)$(BOLD)✅ Pipeline is up. Run 'make status', then 'make test'.$(RESET)"

.PHONY: down
down: teardown ## Delete the Resource Group and every resource in it

# ==============================================================================
# 1. PREREQUISITES & AUTHENTICATION
# ==============================================================================

.PHONY: check-config
check-config: ## Verify SUBSCRIPTION_ID and ACR_NAME are set (see .env.example)
	@[[ -n "$(SUBSCRIPTION_ID)" && "$(SUBSCRIPTION_ID)" != "<"* ]] || { echo -e "$(RED)SUBSCRIPTION_ID is not set. Run: cp .env.example .env and fill it in.$(RESET)"; exit 1; }
	@[[ -n "$(ACR_NAME)" && "$(ACR_NAME)" != "<"* ]] || { echo -e "$(RED)ACR_NAME is not set. Run: cp .env.example .env and fill it in.$(RESET)"; exit 1; }

.PHONY: check-cli
check-cli: ## Verify required local CLI binaries (az, kubectl, helm, curl, python3)
	@for bin in az kubectl helm curl python3; do \
		command -v $$bin >/dev/null 2>&1 || { echo -e "$(RED)Error: $$bin is not installed.$(RESET)"; exit 1; }; \
	done
	@echo -e "$(GREEN)✅ All CLI tools are present.$(RESET)"

.PHONY: auth
auth: check-config check-cli ## Select the subscription and register Resource Providers
	az account set --subscription "$(SUBSCRIPTION_ID)"
	@for ns in ContainerRegistry ContainerService Storage ApiManagement; do \
		az provider register --namespace Microsoft.$$ns >/dev/null; \
	done
	@echo -e "$(GREEN)✅ Subscription active:$(RESET)"
	az account show --output table

.PHONY: check-quota
check-quota: ## Show current vCPU / GPU quota for the NC (A100, T4) families in LOCATION
	az vm list-usage --location "$(LOCATION)" --query "[?contains(name.value, 'NC')]" -o table

# ==============================================================================
# 2. INFRASTRUCTURE (AKS, ACR, NODE POOLS)
# ==============================================================================

.PHONY: infra
infra: auth ## Create Resource Group, ACR and AKS cluster, then fetch credentials
	az group create --name $(RESOURCE_GROUP) --location $(LOCATION) -o none
	@az acr show -n $(ACR_NAME) -o none 2>/dev/null || \
		az acr create -g $(RESOURCE_GROUP) -n $(ACR_NAME) --sku Premium --location $(LOCATION) -o none
	@az aks show -g $(RESOURCE_GROUP) -n $(AKS_NAME) -o none 2>/dev/null || \
		az aks create \
			--resource-group $(RESOURCE_GROUP) \
			--location $(LOCATION) \
			--name $(AKS_NAME) \
			--attach-acr $(ACR_NAME) \
			--node-count 1 \
			--enable-managed-identity \
			--enable-blob-driver \
			--generate-ssh-keys
	az aks get-credentials --resource-group $(RESOURCE_GROUP) --name $(AKS_NAME) --overwrite-existing
	@echo -e "$(GREEN)✅ Core infrastructure ready.$(RESET)"

# add-nodepool <name> <vm-size> <min> <max> [extra flags]
define add-nodepool
	@if az aks nodepool show -g $(RESOURCE_GROUP) --cluster-name $(AKS_NAME) -n $(1) -o none 2>/dev/null; then \
		echo -e "$(GREEN)✅ Node pool $(1) already exists.$(RESET)"; \
	else \
		echo -e "$(YELLOW)⚡ Provisioning node pool $(1) ($(2))...$(RESET)"; \
		az aks nodepool add \
			--resource-group $(RESOURCE_GROUP) \
			--cluster-name $(AKS_NAME) \
			--name $(1) \
			--node-vm-size $(2) \
			--node-count 1 \
			--enable-cluster-autoscaler \
			--min-count $(3) \
			--max-count $(4) \
			--node-taints sku=$(1):NoSchedule $(5); \
	fi
endef

.PHONY: nodepools
nodepools: nodepool-cpu nodepool-t4 nodepool-gpu ## Add all three node pools (CPU, T4, A100)

.PHONY: nodepool-cpu
nodepool-cpu: ## Add the CPU node pool for the Rust Producer & Redis
	$(call add-nodepool,$(CPU_NODEPOOL_NAME),$(CPU_VM_SIZE),1,4)

.PHONY: nodepool-t4
nodepool-t4: ## Add the T4 GPU node pool for the GLM-OCR worker
	$(call add-nodepool,$(T4_NODEPOOL_NAME),$(T4_VM_SIZE),0,10,--tags EnableManagedGPUExperience=true)

.PHONY: nodepool-gpu
nodepool-gpu: ## Add the A100 GPU node pool for vLLM
	$(call add-nodepool,$(GPU_NODEPOOL_NAME),$(GPU_VM_SIZE),0,4,--tags EnableManagedGPUExperience=true)

.PHONY: verify-gpu
verify-gpu: ## Show allocatable GPU capacity on every node
	kubectl get nodes "-o=custom-columns=NAME:.metadata.name,GPU_CAPACITY:.status.capacity.nvidia\.com/gpu,GPU_ALLOCATABLE:.status.allocatable.nvidia\.com/gpu"

# ==============================================================================
# 3. CLUSTER PLATFORM (KEDA, PROMETHEUS, GRAFANA)
# ==============================================================================

.PHONY: platform
platform: install-keda install-monitoring ## Install KEDA and the Prometheus/Grafana stack

.PHONY: install-keda
install-keda: ## Install or upgrade KEDA via Helm
	helm repo add kedacore https://kedacore.github.io/charts --force-update
	helm upgrade --install keda kedacore/keda -n keda --create-namespace --wait
	@echo -e "$(GREEN)✅ KEDA ready.$(RESET)"

.PHONY: install-monitoring
install-monitoring: ## Install kube-prometheus-stack (Prometheus + Grafana) for vLLM metric scraping
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
	helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
		--namespace monitoring --create-namespace \
		--set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
		--set grafana.enabled=true
	@echo -e "$(GREEN)✅ Monitoring stack installed.$(RESET)"

# ==============================================================================
# 4. MODEL WEIGHTS (BLOB-BACKED PVC)
# ==============================================================================

.PHONY: ingest-weights
ingest-weights: ## Create the model PVC and launch the Qwen 3.5 2B + PP-DocLayoutV3 download job
	kubectl apply -f $(K8S_DIR)/infra/pvc.yaml
	@kubectl delete job model-weight-ingest --ignore-not-found=true
	kubectl apply -f $(K8S_DIR)/infra/ingest-job.yaml
	@echo -e "$(GREEN)✅ Ingestion job launched. Follow it with: make ingest-logs$(RESET)"

.PHONY: ingest-wait
ingest-wait: ## Block until the weight ingestion job has completed
	@echo -e "$(YELLOW)⏳ Waiting for model weights to finish downloading...$(RESET)"
	kubectl wait --for=condition=complete job/model-weight-ingest --timeout=60m

.PHONY: ingest-logs
ingest-logs: ## Follow the model weight ingestion logs
	kubectl logs -f job/model-weight-ingest

# ==============================================================================
# 5. CONTAINER IMAGES (BUILT REMOTELY ON ACR)
# ==============================================================================

.PHONY: build
build: build-vlm build-api build-worker build-mcp ## Build and push all four images

.PHONY: build-vlm
build-vlm: check-config ## Build the vLLM (Qwen 3.5 2B) server image
	az acr build --registry $(ACR_NAME) --image $(VLM_IMAGE_NAME):$(IMAGE_TAG) services/server

.PHONY: build-api
build-api: check-config ## Build the Rust Producer API image
	az acr build --registry $(ACR_NAME) --image $(API_IMAGE_NAME):$(IMAGE_TAG) services/client_rt_producer

.PHONY: build-worker
build-worker: check-config ## Build the GLM-OCR Python Consumer Worker image
	az acr build --registry $(ACR_NAME) --image $(WORKER_IMAGE_NAME):$(IMAGE_TAG) services/client_rt_consumer

.PHONY: build-mcp
build-mcp: check-config ## Build the MCP server image
	az acr build --registry $(ACR_NAME) --image $(MCP_IMAGE_NAME):$(IMAGE_TAG) services/mcp_server

# ==============================================================================
# 6. DEPLOYMENT (KUSTOMIZE)
# ==============================================================================

.PHONY: render
render: check-config ## Print the fully rendered Kubernetes manifests
	@$(RENDER)

.PHONY: deploy
deploy: check-config ## Deploy Redis + Producer + Worker + vLLM + MCP server + KEDA scalers
	$(RENDER) | kubectl apply -f -
	@echo -e "$(GREEN)✅ Manifests applied. Watch rollout with: make status$(RESET)"

.PHONY: undeploy
undeploy: check-config ## Remove all pipeline workloads from the cluster
	$(RENDER) | kubectl delete --ignore-not-found=true -f -

.PHONY: restart
restart: ## Rolling-restart every pipeline deployment (picks up new :latest images)
	kubectl rollout restart deployment -l system=aks-ocr-glmocr

# ==============================================================================
# 7. TEST & OBSERVE
# ==============================================================================

.PHONY: test
test: ## Port-forward the Producer and run an end-to-end OCR request on samples/sample_image.png
	@kubectl port-forward svc/ocr-api-service 5000:80 >/dev/null 2>&1 & PF=$$!; \
		trap "kill $$PF" EXIT; \
		for _ in $$(seq 1 20); do curl -s localhost:5000/health >/dev/null && break; sleep 0.5; done; \
		python3 scripts/test_submit.py samples/sample_image.png --api-url http://localhost:5000

.PHONY: status
status: ## Show pods, services and KEDA ScaledObjects
	@echo -e "$(CYAN)=== Pods ===$(RESET)"
	@kubectl get pods -o wide
	@echo -e "\n$(CYAN)=== Services ===$(RESET)"
	@kubectl get svc
	@echo -e "\n$(CYAN)=== KEDA ScaledObjects ===$(RESET)"
	@kubectl get scaledobjects

.PHONY: logs-api
logs-api: ## Tail the Rust Producer API logs
	kubectl logs -l app=ocr-api --tail=100 -f

.PHONY: logs-worker
logs-worker: ## Tail the GLM-OCR Consumer Worker logs
	kubectl logs -l app=ocr-worker-rt --tail=100 -f

.PHONY: logs-vlm
logs-vlm: ## Tail the vLLM server logs
	kubectl logs -l app=ocr-vlm --tail=100 -f

.PHONY: logs-mcp
logs-mcp: ## Tail the MCP server logs
	kubectl logs -l app=ocr-mcp-server --tail=100 -f

.PHONY: port-forward-api
port-forward-api: ## Expose the Rust Producer API on http://localhost:5000
	kubectl port-forward svc/ocr-api-service 5000:80

.PHONY: port-forward-vlm
port-forward-vlm: ## Expose the vLLM OpenAI-compatible API on http://localhost:8000
	kubectl port-forward svc/ocr-vlm-service 8000:8000

.PHONY: port-forward-grafana
port-forward-grafana: ## Expose Grafana on http://localhost:3000 (password: make grafana-password)
	kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80

.PHONY: grafana-password
grafana-password: ## Print the Grafana admin password
	@kubectl get secret -n monitoring prometheus-grafana -o jsonpath="{.data.admin-password}" | base64 --decode; echo

# ==============================================================================
# 8. CLAUDE CODE MCP
# ==============================================================================

.PHONY: port-forward-mcp
port-forward-mcp: ## Expose the in-cluster MCP server (SSE) on http://localhost:8000
	kubectl port-forward svc/ocr-mcp-service 8000:8000

.PHONY: mcp-register
mcp-register: ## Register the port-forwarded MCP server with Claude Code
	claude mcp add tnm-ocr --transport sse http://localhost:8000/sse

.PHONY: mcp-local
mcp-local: ## Install the MCP server locally and print the Stdio registration command
	pip install -r services/mcp_server/requirements.txt
	@echo -e "$(GREEN)✅ Register it with: claude mcp add tnm-ocr -- python3 $$(pwd)/services/mcp_server/server.py$(RESET)"

# ==============================================================================
# 9. ENTERPRISE GATEWAY (AZURE API MANAGEMENT, OPTIONAL)
# ==============================================================================

.PHONY: apim
apim: apim-create apim-api apim-policy apim-key ## Provision APIM in front of the pipeline (30-45 min)

.PHONY: apim-create
apim-create: check-config ## Create an APIM instance in Internal VNet mode inside the AKS VNet
	@if az apim show -g $(RESOURCE_GROUP) -n $(APIM_NAME) -o none 2>/dev/null; then \
		echo -e "$(GREEN)✅ APIM $(APIM_NAME) already exists.$(RESET)"; exit 0; \
	fi; \
	NODE_RG=$$(az aks show -g $(RESOURCE_GROUP) -n $(AKS_NAME) --query nodeResourceGroup -o tsv); \
	VNET_NAME=$$(az network vnet list -g $$NODE_RG --query "[0].name" -o tsv); \
	az network vnet subnet show -g $$NODE_RG --vnet-name $$VNET_NAME -n $(APIM_SUBNET_NAME) -o none 2>/dev/null || \
		az network vnet subnet create -g $$NODE_RG --vnet-name $$VNET_NAME -n $(APIM_SUBNET_NAME) \
			--address-prefixes $(APIM_SUBNET_PREFIX) -o none; \
	echo -e "$(YELLOW)🛡️  Creating APIM $(APIM_NAME) in $$VNET_NAME/$(APIM_SUBNET_NAME) (30-45 min)...$(RESET)"; \
	az apim create \
		--name $(APIM_NAME) \
		--resource-group $(RESOURCE_GROUP) \
		--location $(LOCATION) \
		--publisher-email "$(APIM_PUBLISHER_EMAIL)" \
		--publisher-name "$(APIM_PUBLISHER_NAME)" \
		--sku-name $(APIM_SKU) \
		--virtual-network Internal \
		--vnet-id "/subscriptions/$(SUBSCRIPTION_ID)/resourceGroups/$$NODE_RG/providers/Microsoft.Network/virtualNetworks/$$VNET_NAME" \
		--subnet-name $(APIM_SUBNET_NAME)

.PHONY: apim-api
apim-api: ## Register the internal Producer as backend and define the OCR API operations
	@INTERNAL_IP=$$(kubectl get svc ocr-api-service -o jsonpath='{.status.loadBalancer.ingress[0].ip}'); \
	[[ -n "$$INTERNAL_IP" ]] || { echo -e "$(RED)ocr-api-service has no internal IP yet. Run 'make deploy' and retry.$(RESET)"; exit 1; }; \
	az apim backend create -g $(RESOURCE_GROUP) --service-name $(APIM_NAME) \
		--backend-id ocr-api-backend --url "http://$$INTERNAL_IP" --protocol http -o none
	az apim api create -g $(RESOURCE_GROUP) --service-name $(APIM_NAME) \
		--api-id ocr-api --path ocr --display-name "High-Performance OCR API" -o none
	az apim api operation create -g $(RESOURCE_GROUP) --service-name $(APIM_NAME) --api-id ocr-api \
		--operation-id post-process --url-template "/process" --method POST --display-name "Submit OCR Task" -o none
	az apim api operation create -g $(RESOURCE_GROUP) --service-name $(APIM_NAME) --api-id ocr-api \
		--operation-id get-status --url-template "/status/{task_id}" --method GET --display-name "Get OCR Task Status" \
		--template-parameters name=task_id type=string required=true -o none

.PHONY: apim-policy
apim-policy: ## Apply the JWT + rate-limit policy (deploy/apim/apim-policy.xml)
	az apim api operation policy set -g $(RESOURCE_GROUP) --service-name $(APIM_NAME) \
		--api-id ocr-api --operation-id post-process --policy-value "$$(cat deploy/apim/apim-policy.xml)"
	@echo -e "$(GREEN)✅ APIM governance policy applied.$(RESET)"

.PHONY: apim-key
apim-key: ## Create a partner product + subscription and print its API key
	@az apim product show -g $(RESOURCE_GROUP) --service-name $(APIM_NAME) --product-id ocr-partner-tier -o none 2>/dev/null || { \
		az apim product create -g $(RESOURCE_GROUP) --service-name $(APIM_NAME) --product-id ocr-partner-tier \
			--product-name "OCR Partner Tier" --subscription-required true --state published -o none; \
		az apim product api add -g $(RESOURCE_GROUP) --service-name $(APIM_NAME) \
			--product-id ocr-partner-tier --api-id ocr-api -o none; \
	}
	@az apim subscription show -g $(RESOURCE_GROUP) --service-name $(APIM_NAME) --subscription-id partner-abc-key -o none 2>/dev/null || \
		az apim subscription create -g $(RESOURCE_GROUP) --service-name $(APIM_NAME) --subscription-id partner-abc-key \
			--display-name "Partner ABC Subscription" --product-id ocr-partner-tier --state active -o none
	@echo -n "APIM subscription key: "
	@az apim subscription show -g $(RESOURCE_GROUP) --service-name $(APIM_NAME) --subscription-id partner-abc-key --query primaryKey -o tsv

# ==============================================================================
# 10. COST CONTROL & TEARDOWN
# ==============================================================================

.PHONY: scale-to-zero
scale-to-zero: ## Let both GPU node pools scale down to zero nodes when idle
	az aks nodepool update -g $(RESOURCE_GROUP) --cluster-name $(AKS_NAME) -n $(GPU_NODEPOOL_NAME) \
		--update-cluster-autoscaler --min-count 0 --max-count 4
	az aks nodepool update -g $(RESOURCE_GROUP) --cluster-name $(AKS_NAME) -n $(T4_NODEPOOL_NAME) \
		--update-cluster-autoscaler --min-count 0 --max-count 10
	@echo -e "$(GREEN)✅ GPU node pools set to scale-to-zero. Redis, Producer and MCP stay warm.$(RESET)"

.PHONY: clean-jobs
clean-jobs: ## Delete the finished weight ingestion job
	kubectl delete job model-weight-ingest --ignore-not-found=true

.PHONY: teardown
teardown: check-config ## Permanently delete the Resource Group (asks for confirmation)
	@echo -e "$(RED)$(BOLD)⚠️  This permanently deletes Resource Group $(RESOURCE_GROUP) and everything in it.$(RESET)"
	@read -p "Type the resource group name to confirm: " confirm && [[ $$confirm == "$(RESOURCE_GROUP)" ]] || { echo "Aborted."; exit 1; }
	az group delete --name $(RESOURCE_GROUP) --yes --no-wait
	@echo -e "$(GREEN)✅ Resource group deletion initiated.$(RESET)"
