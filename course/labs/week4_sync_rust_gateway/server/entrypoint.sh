#!/bin/bash
set -e

###############################################################################
# 📝 CONFIGURATION: vLLM for SOTA OCR VLM (baidu/Unlimited-OCR)
###############################################################################

# --- Configuration & Defaults ---
: "${PORT:=8000}"
: "${SERVED_NAME:=baidu/Unlimited-OCR}"
: "${MODEL_ID:=baidu/Unlimited-OCR}"
: "${GPU_MEMORY:=0.85}"
: "${MAX_NUM_BATCHED_TOKENS:=65536}"
: "${MAX_NUM_SEQS:=64}"
: "${MAX_MODEL_LEN:=32768}"

# Determine weight path from persistent volume or default model ID
if [ -f "/mnt/models/${MODEL_ID}/config.json" ]; then
  MODEL_PATH="/mnt/models/${MODEL_ID}"
elif [ -f "/mnt/models/baidu/Unlimited-OCR/config.json" ]; then
  MODEL_PATH="/mnt/models/baidu/Unlimited-OCR"
elif [ -d "/mnt/models/${MODEL_ID}" ]; then
  MODEL_PATH="/mnt/models/${MODEL_ID}"
else
  MODEL_PATH="${MODEL_ID}"
fi

export PYTHONUNBUFFERED=1
export CUDA_VISIBLE_DEVICES=0

###############################################################################
# 🚀 Launch vLLM Server for Baidu Unlimited-OCR
###############################################################################
echo "🚀 Starting $MODEL_ID (served as '$SERVED_NAME') with vLLM on GPU 0..."

exec vllm serve "$MODEL_PATH" \
  --served-model-name "$SERVED_NAME" \
  --port "$PORT" \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization "$GPU_MEMORY" \
  --trust-remote-code \
  --logits_processors vllm.model_executor.models.unlimited_ocr:NGramPerReqLogitsProcessor \
  --no-enable-prefix-caching \
  --mm-processor-cache-gb 0 \
  --max-model-len "$MAX_MODEL_LEN" \
  --max-num-batched-tokens "$MAX_NUM_BATCHED_TOKENS" \
  --max-num-seqs "$MAX_NUM_SEQS" \
  --limit-mm-per-prompt '{"image":10, "video":0}' \
  --enable-chunked-prefill
