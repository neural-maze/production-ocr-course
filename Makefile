# ==============================================================================
# 🚀 THE NEURAL MAZE — LOCAL ASYNCHRONOUS OCR PIPELINE (INNGEST + MLX / LLAMA.CPP)
# ==============================================================================
# Stack: Inngest Dev Server (:8288) + PostgreSQL (:5432) + Adminer (:8081)
#        + FastAPI & Minimalist Visualizer (:8000)
#        + MLX (mlx-community/Unlimited-OCR-2-8bit on macOS)
#        + llama.cpp Server (:8080 on Linux / Docker Desktop)
# ==============================================================================

SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

-include .env
export

COMPOSE_FILE      := infra/docker-compose.yml
APP_HOST          ?= 0.0.0.0
APP_PORT          ?= 8000
INNGEST_PORT      ?= 8288
ADMINER_PORT      ?= 8081
POSTGRES_PORT     ?= 5432
LLAMACPP_PORT     ?= 8080
MLX_MODEL_ID      ?= mlx-community/Unlimited-OCR-2-8bit
MLX_MODEL_DIR     ?= ./models/Unlimited-OCR-2-8bit
HF_HOME           ?= ./models/hf_cache

# Auto-detect OS/Arch if OCR_BACKEND is not explicitly set
UNAME_S := $(shell uname -s)
UNAME_M := $(shell uname -m)
ifeq ($(UNAME_S)-$(UNAME_M),Darwin-arm64)
    OCR_BACKEND ?= mlx
else
    OCR_BACKEND ?= llamacpp
endif

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
	@echo -e "$(CYAN)$(BOLD)==============================================================================$(RESET)"
	@echo -e "$(CYAN)$(BOLD) 🚀 The Neural Maze — Local Durable OCR Pipeline (Inngest + MLX / llama.cpp)$(RESET)"
	@echo -e "$(CYAN)$(BOLD)==============================================================================$(RESET)"
	@echo -e "Detected platform: $(YELLOW)$(UNAME_S)/$(UNAME_M)$(RESET) -> Default backend: $(GREEN)$(BOLD)$(OCR_BACKEND)$(RESET)"
	@echo -e ""
	@echo -e "Usage: $(YELLOW)make [target]$(RESET)"
	@echo -e ""
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  $(CYAN)%-20s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo -e ""

# ==============================================================================
# 🛠️ 1. SETUP & DEPENDENCIES
# ==============================================================================

.PHONY: env
env: ## Create .env from .env.example if it does not exist
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo -e "$(GREEN)✅ Created .env from .env.example$(RESET)"; \
	fi

.PHONY: sync
sync: env ## Install Python dependencies via uv
	@echo -e "$(YELLOW)📦 Syncing Python environment with uv...$(RESET)"
	uv sync
	@echo -e "$(GREEN)✅ Dependencies installed.$(RESET)"

.PHONY: preload-model
preload-model: sync ## Check and download mlx-community/Unlimited-OCR-2-8bit weights into ./models/Unlimited-OCR-2-8bit if missing
	@if [ -f "$(MLX_MODEL_DIR)/config.json" ] && ls "$(MLX_MODEL_DIR)"/*.safetensors >/dev/null 2>&1; then \
		echo -e "$(GREEN)✅ Model weights already present in $(MLX_MODEL_DIR)$(RESET)"; \
	else \
		echo -e "$(YELLOW)📥 Model weights not found locally. Downloading $(MLX_MODEL_ID) into $(MLX_MODEL_DIR)...$(RESET)"; \
		HF_HOME=$(HF_HOME) uv run python -c "from huggingface_hub import snapshot_download; path = snapshot_download(repo_id='$(MLX_MODEL_ID)', local_dir='$(MLX_MODEL_DIR)'); print(f'✅ Model cached locally at: {path}')"; \
	fi

# ==============================================================================
# 🚀 2. APPLICATION & INFRASTRUCTURE LIFECYCLE
# ==============================================================================

.PHONY: ensure-docker
ensure-docker: ## Ensure Docker Desktop daemon is running (auto-starts Docker on macOS if stopped)
	@if ! docker info >/dev/null 2>&1; then \
		echo -e "$(YELLOW)🐳 Docker daemon is not running. Launching Docker Desktop...$(RESET)"; \
		if [ "$(UNAME_S)" = "Darwin" ]; then open -a Docker; fi; \
		echo -e "$(YELLOW)⏳ Waiting for Docker Desktop daemon to become ready...$(RESET)"; \
		until docker info >/dev/null 2>&1; do sleep 2; done; \
		echo -e "$(GREEN)✅ Docker daemon is up!$(RESET)"; \
	fi

.PHONY: infra-up
infra-up: env ensure-docker ## Start Docker backing services (PostgreSQL, Adminer, Inngest Dev Server)
	@echo -e "$(YELLOW)🐳 Starting PostgreSQL, Adminer, and Inngest Dev Server...$(RESET)"
	@docker compose -f $(COMPOSE_FILE) up -d postgres adminer inngest || ( \
		echo -e "$(YELLOW)⚠️  Recreating stale container instances...$(RESET)" && \
		docker rm -f ocr-postgres ocr-adminer ocr-inngest >/dev/null 2>&1 || true && \
		docker compose -f $(COMPOSE_FILE) up -d --force-recreate postgres adminer inngest \
	)
	@echo -e "$(YELLOW)⏳ Waiting for PostgreSQL to become healthy...$(RESET)"
	@until docker compose -f $(COMPOSE_FILE) exec -T postgres pg_isready -U $${POSTGRES_USER:-ocr} -d $${POSTGRES_DB:-ocr_db} >/dev/null 2>&1; do \
		sleep 1; \
	done
	@echo -e "$(GREEN)✅ Backing infrastructure ready:$(RESET)"
	@echo -e "   • Inngest Dev UI : $(CYAN)http://localhost:$(INNGEST_PORT)$(RESET)"
	@echo -e "   • Adminer DB UI  : $(CYAN)http://localhost:$(ADMINER_PORT)$(RESET)"
	@echo -e "   • PostgreSQL     : $(CYAN)localhost:$(POSTGRES_PORT)$(RESET)"

.PHONY: db-init
db-init: infra-up ## Initialize PostgreSQL schema and tables
	@echo -e "$(YELLOW)🗄️  Ensuring database tables exist...$(RESET)"
	uv run python -m ocr_pipeline.db.init_db
	@echo -e "$(GREEN)✅ Database schema initialized.$(RESET)"

.PHONY: start
start: sync db-init ## Start full stack (auto-selects MLX on macOS or llama.cpp on Linux)
	@if [ "$(OCR_BACKEND)" = "llamacpp" ]; then \
		$(MAKE) start-linux; \
	else \
		$(MAKE) start-mlx; \
	fi

.PHONY: start-mlx
start-mlx: sync preload-model db-init ## Start full stack on macOS with Apple Silicon MLX (downloads weights if missing + runs sample warmup)
	@echo -e "$(GREEN)$(BOLD)🚀 Launching FastAPI + Inngest Worker + MLX Engine ($(MLX_MODEL_ID))...$(RESET)"
	@echo -e "   • Web Visualizer : $(CYAN)http://localhost:$(APP_PORT)$(RESET)"
	@echo -e "   • Inngest Dev UI : $(CYAN)http://localhost:$(INNGEST_PORT)$(RESET)"
	@echo -e "   • Adminer DB UI  : $(CYAN)http://localhost:$(ADMINER_PORT)$(RESET)"
	INNGEST_DEV=1 OCR_BACKEND=mlx uv run uvicorn ocr_pipeline.main:app --host $(APP_HOST) --port $(APP_PORT) --reload

.PHONY: start-linux
start-linux: sync db-init ## Start full stack with containerized llama.cpp server (Linux / Docker Desktop)
	@echo -e "$(YELLOW)🐳 Starting llama.cpp server container on port $(LLAMACPP_PORT)...$(RESET)"
	docker compose -f $(COMPOSE_FILE) --profile linux up -d llamacpp
	@echo -e "$(GREEN)$(BOLD)🚀 Launching FastAPI + Inngest Worker (OCR_BACKEND=llamacpp)...$(RESET)"
	@echo -e "   • Web Visualizer : $(CYAN)http://localhost:$(APP_PORT)$(RESET)"
	@echo -e "   • Inngest Dev UI : $(CYAN)http://localhost:$(INNGEST_PORT)$(RESET)"
	@echo -e "   • llama.cpp API  : $(CYAN)http://localhost:$(LLAMACPP_PORT)$(RESET)"
	INNGEST_DEV=1 OCR_BACKEND=llamacpp uv run uvicorn ocr_pipeline.main:app --host $(APP_HOST) --port $(APP_PORT) --reload

# ==============================================================================
# 🧪 3. QUALITY, TESTING & DIAGNOSTICS
# ==============================================================================

.PHONY: check
check: ## Run code quality checks (ruff linter, ruff formatter check, mypy, pytest)
	@echo -e "$(YELLOW)🔍 Running Ruff linter...$(RESET)"
	uv run ruff check src tests
	@echo -e "$(YELLOW)🔍 Running mypy type checker...$(RESET)"
	uv run mypy src
	@echo -e "$(YELLOW)🧪 Running unit tests...$(RESET)"
	uv run pytest -q
	@echo -e "$(GREEN)✅ All checks passed!$(RESET)"

.PHONY: test
test: ## Submit sample_image.png to the running local pipeline and poll until completion
	@echo -e "$(YELLOW)🧪 Submitting ocr-course/week4_rust_gateway_deployment/sample_image.png to http://localhost:$(APP_PORT)/api/documents...$(RESET)"
	uv run python scripts/test_pipeline.py

# ==============================================================================
# 🧹 4. TEARDOWN, RESET & DATABASE MAINTENANCE
# ==============================================================================

.PHONY: stop
stop: ## Stop Docker Compose containers while preserving PostgreSQL & Inngest data volumes
	@echo -e "$(YELLOW)🛑 Stopping Docker Compose containers (preserving data volumes)...$(RESET)"
	docker compose -f $(COMPOSE_FILE) --profile linux down
	@echo -e "$(GREEN)✅ Containers stopped.$(RESET)"

.PHONY: db-clear
db-clear: ## Empty all PostgreSQL tables (documents, pages, batches) while keeping the schema intact
	@echo -e "$(YELLOW)🧹 Clearing all rows from PostgreSQL tables...$(RESET)"
	uv run python -m ocr_pipeline.db.clear_db
	@echo -e "$(GREEN)✅ Database tables emptied.$(RESET)"

.PHONY: reset
reset: ## Destructive reset: stop containers and drop all PostgreSQL & Inngest volumes
	@echo -e "$(RED)🗑️  Stopping containers and removing persistent volumes...$(RESET)"
	docker compose -f $(COMPOSE_FILE) --profile linux down -v
	@echo -e "$(GREEN)✅ Volumes and local container state wiped.$(RESET)"

.PHONY: nuke
nuke: reset ## Full reset + kill any lingering local Python server on port 8000 (without killing Docker Desktop)
	@echo -e "$(RED)💥 Killing any lingering app processes on port $(APP_PORT)...$(RESET)"
	@pids=$$(lsof -ti :$(APP_PORT) 2>/dev/null || true); \
	if [ -n "$$pids" ]; then \
		echo "Killing PID(s) $$pids on port $(APP_PORT)"; \
		kill -9 $$pids 2>/dev/null || true; \
	fi
	@echo -e "$(GREEN)✅ Nuke complete.$(RESET)"
