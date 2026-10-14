# 📡 Async API Reference

The Rust Producer exposes a fire-and-poll interface: submission returns immediately with a `task_id`, and the actual OCR work happens asynchronously on the `gpunpt4` and `gpunpa100` pools.

### Endpoint: `POST /process`

Accepts a **multipart form upload** (not JSON) — this keeps the Rust gateway a thin, streaming pass-through that never buffers the whole request body as base64 in memory.

```bash
curl -X POST http://localhost:5000/process \
  -F "file=@samples/sample_image.png"
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

Run the whole round trip automatically with `make test` (see [`scripts/test_submit.py`](../scripts/test_submit.py)).
