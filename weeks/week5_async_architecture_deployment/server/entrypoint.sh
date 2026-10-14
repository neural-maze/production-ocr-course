#!/bin/bash
set -e

###############################################################################
# 📝 CONFIGURATION: vLLM for Qwen 3.5
###############################################################################

# --- Configuration & Defaults ---
: "${PORT:=8000}"
: "${SERVED_NAME:=${MODEL_ID:-Qwen/Qwen3.5-2B}}"
: "${GPU_MEMORY:=0.9}"
: "${MAX_NUM_BATCHED_TOKENS:=262144}"
: "${MAX_NUM_SEQS:=256}"
: "${MAX_MODEL_LEN:=16384}"

if [ -d "/mnt/models" ]; then
  MODEL_ROOT="/mnt/models"
elif [ -d "/app/model-weights" ]; then
  MODEL_ROOT="/app/model-weights"
else
  MODEL_ROOT="/app/models"
fi

# Append the served model name to the root path to find the actual weights
MODEL_PATH="${MODEL_ROOT}/${SERVED_NAME}"

export PYTHONUNBUFFERED=1

###############################################################################
# 🚀 Launch vLLM Server
###############################################################################
echo "🚀 Starting $SERVED_NAME with vLLM..."

exec vllm serve "$MODEL_PATH" \
  --served-model-name "$SERVED_NAME" \
  --port "$PORT" \
  --gpu-memory-utilization "$GPU_MEMORY" \
  --max-model-len "$MAX_MODEL_LEN" \
  --max-num-batched-tokens "$MAX_NUM_BATCHED_TOKENS" \
  --max-num-seqs "$MAX_NUM_SEQS" \
  --limit-mm-per-prompt '{"image":1, "video":0}' \
  --mm-encoder-tp-mode data \
  --trust-remote-code \
  --load-format instanttensor \
  --reasoning-parser qwen3 \
  --default-chat-template-kwargs '{"enable_thinking": false}' \
  --enable-chunked-prefill

