import os
import asyncio
import json
import base64
import time
import redis
from glmocr import GlmOcr
from loguru import logger

# --- Redis Config ---
REDIS_HOST = os.getenv("REDIS_HOST", "ocr-redis-service")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)

# --- Dynamic Batching Config ---
# Max items in one GPU parse call
MAX_BATCH_SIZE = int(os.getenv("MAX_BATCH_SIZE", "4"))
# Time to wait (ms) to fill the batch if at least one task is present
BATCH_WINDOW_MS = int(os.getenv("BATCH_WINDOW_MS", "100"))

# --- GLM-OCR Config ---
CONFIG_PATH = os.getenv("GLMOCR_CONFIG_PATH", "config.yaml")
ocr_engine = GlmOcr(config_path=CONFIG_PATH, enable_layout=True)

async def process_batch(task_ids):
    """Processes a batch of tasks together via the GLM-OCR SDK."""
    temp_paths = []
    valid_task_ids = []

    try:
        # 1. Prepare Batch Data
        for task_id in task_ids:
            task_data = r.hgetall(f"task:{task_id}")
            if not task_data:
                logger.error(f"❌ Task {task_id} data not found in Redis.")
                continue

            # Update status to 'processing'
            r.hset(f"task:{task_id}", "status", "processing")
            
            # Save binary to /dev/shm for high-speed SDK access
            file_bytes = base64.b64decode(task_data['data'])
            ext = task_data.get('extension', 'jpg')
            temp_path = f"/dev/shm/{task_id}.{ext}"
            
            with open(temp_path, "wb") as f:
                f.write(file_bytes)
            os.chmod(temp_path, 0o644)
            
            temp_paths.append(temp_path)
            valid_task_ids.append(task_id)

        if not temp_paths:
            return

        logger.info(f"🚀 Dispatching Batch of {len(temp_paths)} to SDK Engine")

        # 2. Run SDK Pipeline for the entire batch
        # This allows SDK to batch layout detection and parallelize region recognition
        results = await asyncio.to_thread(ocr_engine.parse, temp_paths)

        # 3. Handle Results (SDK returns a list if input was a list)
        if not isinstance(results, list):
            results = [results]

        for i, task_id in enumerate(valid_task_ids):
            if i >= len(results):
                break
                
            res_obj = results[i]
            markdown = getattr(res_obj, "markdown_result", "")
            layout = getattr(res_obj, "json_result", {})

            # Update Redis with result
            final_result = {
                "markdown": markdown,
                "layout": layout
            }
            r.hset(f"task:{task_id}", mapping={
                "status": "done",
                "result": json.dumps(final_result),
                "data": "" # Clear binary data to save memory
            })
            logger.info(f"✅ Task {task_id} completed (Batch Member)")

    except Exception as e:
        logger.error(f"❌ Batch processing failed: {e}")
        # Mark all valid tasks in batch as failed
        for task_id in valid_task_ids:
            r.hset(f"task:{task_id}", mapping={"status": "failed", "error": str(e)})
    finally:
        # Cleanup temp files
        for path in temp_paths:
            if os.path.exists(path):
                os.remove(path)

async def worker_loop():
    """Main loop with Dynamic Batching / Collector pattern."""
    logger.info(f"🚀 Starting Dynamic Batching Worker (Max Batch: {MAX_BATCH_SIZE}, Window: {BATCH_WINDOW_MS}ms)...")
    
    while True:
        try:
            # 0. Broadcast worker heartbeat in Redis (10s TTL) for frontend readiness gating
            try:
                r.set("worker_heartbeat", int(time.time()), ex=10)
            except Exception:
                pass

            # 1. Wait for the first task (blocking)
            # We use brpop to avoid CPU spinning when idle
            task_info = r.brpop("ocr_tasks", timeout=2)
            
            if not task_info:
                continue

            _, first_task_id = task_info
            batch = [first_task_id]
            
            # 2. Collector Window: Try to fill the batch
            # If we have at least 1 task, wait BATCH_WINDOW_MS to see if more arrive
            deadline = time.time() + (BATCH_WINDOW_MS / 1000.0)
            
            while len(batch) < MAX_BATCH_SIZE:
                timeout = max(0, deadline - time.time())
                # Use LPOP for non-blocking check of more tasks
                next_task = r.lpop("ocr_tasks")
                if next_task:
                    batch.append(next_task)
                elif timeout > 0:
                    # Brief sleep to allow network/Redis latency for new tasks
                    await asyncio.sleep(0.01)
                else:
                    break

            # 3. Process the batch and WAIT for it to complete
            # This ensures only one batch hits the GPU at a time, preventing bottlenecks
            # and preserving VRAM for the layout model and SDK workers.
            await process_batch(batch)
                
        except Exception as e:
            logger.error(f"Worker loop error: {e}")
            await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(worker_loop())
