import base64
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from ocr_pipeline.engine.base import BatchOcrResult, OcrEngine
from ocr_pipeline.engine.parser import parse_multi_page_output


class LlamaCppOcrEngine(OcrEngine):
    """
    OpenAI-compatible client for the containerized `llama.cpp` server (Linux / Docker Desktop).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        model_id: str = "unlimited-ocr-2.gguf",
        max_tokens: int = 8192,
        temperature: float = 0.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model_id = model_id
        self._max_tokens = max_tokens
        self._temperature = temperature

    @property
    def backend_name(self) -> str:
        return "llamacpp"

    @property
    def model_name(self) -> str:
        return self._model_id

    def infer_batch(self, image_paths: list[str]) -> BatchOcrResult:
        if not image_paths:
            raise ValueError("infer_batch requires at least one image path.")

        num_images = len(image_paths)
        prompt = "Multi page parsing."

        content_parts: list[dict[str, Any]] = []
        for path in image_paths:
            img_bytes = Path(path).read_bytes()
            b64_str = base64.b64encode(img_bytes).decode("ascii")
            content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64_str}"},
                }
            )
        content_parts.append({"type": "text", "text": prompt})

        payload = {
            "model": self._model_id,
            "messages": [{"role": "user", "content": content_parts}],
            "max_tokens": self._max_tokens if num_images == 1 else min(32768, self._max_tokens * num_images),
            "temperature": self._temperature,
            "stream": False,
        }

        endpoint = f"{self._base_url}/v1/chat/completions"
        logger.info(f"Sending batch of {num_images} page(s) to llama.cpp server at {endpoint}...")

        t0 = time.perf_counter()
        with httpx.Client(timeout=600.0) as client:
            resp = client.post(endpoint, json=payload)
            resp.raise_for_status()
            data = resp.json()

        latency_s = max(1e-6, time.perf_counter() - t0)
        latency_ms = latency_s * 1000.0

        choices = data.get("choices", [])
        raw_text = choices[0].get("message", {}).get("content", "") if choices else ""

        usage = data.get("usage", {})
        timings = data.get("timings", {})

        prompt_tokens = int(usage.get("prompt_tokens", timings.get("prompt_n", 0)) or 0)
        generation_tokens = int(usage.get("completion_tokens", timings.get("predicted_n", 0)) or 0)
        prompt_tps = float(timings.get("prompt_per_second", 0.0) or 0.0)
        generation_tps = float(timings.get("predicted_per_second", 0.0) or 0.0)

        if generation_tps <= 0.0 and generation_tokens > 0:
            generation_tps = generation_tokens / latency_s

        parsed_pages = parse_multi_page_output(raw_text, expected_pages=num_images)

        return BatchOcrResult(
            raw_output=raw_text,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            generation_tokens=generation_tokens,
            prompt_tps=prompt_tps,
            generation_tps=generation_tps,
            peak_memory_gb=0.0,
            pages=parsed_pages,
        )
