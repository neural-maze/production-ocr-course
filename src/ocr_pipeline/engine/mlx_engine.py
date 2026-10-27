import os
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

from ocr_pipeline.engine.base import BatchOcrResult, OcrEngine
from ocr_pipeline.engine.parser import parse_multi_page_output


class MlxOcrEngine(OcrEngine):
    """
    Apple Silicon MLX inference engine using `mlx-vlm` and `mlx-community/Unlimited-OCR-2-8bit`.
    Supports processing multiple document pages in a single forward pass via page2d RoPE & DASH attention.
    """

    def __init__(
        self,
        model_id: str = "mlx-community/Unlimited-OCR-2-8bit",
        local_model_dir: str = "./models/Unlimited-OCR-2-8bit",
        hf_home: str = "./models/hf_cache",
        max_tokens: int = 8192,
        temperature: float = 0.0,
    ) -> None:
        self._model_id = model_id
        self._local_model_dir = Path(local_model_dir).resolve()
        self._hf_home = Path(hf_home).resolve()
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._lock = threading.Lock()
        self._model: Any = None
        self._processor: Any = None
        self._config: Any = None

    @property
    def backend_name(self) -> str:
        return "mlx"

    @property
    def model_name(self) -> str:
        return self._model_id

    def _resolve_model_path(self) -> Path:
        self._hf_home.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(self._hf_home))

        config_file = self._local_model_dir / "config.json"
        has_weights = any(self._local_model_dir.glob("*.safetensors"))
        if config_file.exists() and has_weights:
            return self._local_model_dir

        from huggingface_hub import snapshot_download

        self._local_model_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"Downloading '{self._model_id}' into repo-local folder '{self._local_model_dir}'..."
        )
        snapshot_download(
            repo_id=self._model_id,
            local_dir=str(self._local_model_dir),
        )
        return self._local_model_dir

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            from mlx_vlm import load
            from mlx_vlm.utils import load_config

            model_path = str(self._resolve_model_path())
            logger.info(
                f"Loading MLX VLM model '{self._model_id}' from '{model_path}' into Unified Memory..."
            )
            t0 = time.perf_counter()
            self._model, self._processor = load(model_path)
            self._config = load_config(model_path)
            elapsed = time.perf_counter() - t0
            logger.info(f"MLX model '{self._model_id}' loaded in {elapsed:.2f}s.")

    def warmup(self, sample_image_path: str | None = None) -> None:
        """Preloads weights and runs a short warmup pass on a sample image to compile Metal kernels."""
        self._ensure_loaded()
        if not sample_image_path or not Path(sample_image_path).exists():
            return

        import mlx.core as mx
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        logger.info(f"🔥 Warming up MLX engine with sample image '{sample_image_path}'...")
        t0 = time.perf_counter()
        formatted_prompt = str(
            apply_chat_template(
                self._processor,
                self._config,
                "Multi page parsing.",
                num_images=1,
            )
        )
        with self._lock:
            generate(
                self._model,
                self._processor,
                formatted_prompt,
                [sample_image_path],
                max_tokens=8,
                temperature=0.0,
                verbose=False,
            )
        try:
            mx.clear_cache()
        except Exception:
            pass
        logger.info(f"✅ MLX engine warmup complete in {time.perf_counter() - t0:.2f}s.")

    def infer_batch(self, image_paths: list[str]) -> BatchOcrResult:
        if not image_paths:
            raise ValueError("infer_batch requires at least one image path.")

        self._ensure_loaded()

        import mlx.core as mx
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        num_images = len(image_paths)
        prompt = "Multi page parsing."

        formatted_prompt = str(
            apply_chat_template(
                self._processor,
                self._config,
                prompt,
                num_images=num_images,
            )
        )

        # Scale max_tokens when multiple pages are processed in one pass
        batch_max_tokens = self._max_tokens if num_images == 1 else min(32768, self._max_tokens * num_images)

        logger.info(
            f"Running MLX inference on batch of {num_images} page(s) (max_tokens={batch_max_tokens})..."
        )
        t0 = time.perf_counter()

        with self._lock:
            result = generate(
                self._model,
                self._processor,
                formatted_prompt,
                image_paths,
                max_tokens=batch_max_tokens,
                temperature=self._temperature,
                verbose=False,
            )

        latency_s = max(1e-6, time.perf_counter() - t0)
        latency_ms = latency_s * 1000.0

        # Extract text & hardware telemetry from mlx_vlm GenerationResult
        if hasattr(result, "text"):
            raw_text = str(result.text)
            prompt_tokens = int(getattr(result, "prompt_tokens", 0) or 0)
            generation_tokens = int(getattr(result, "generation_tokens", 0) or 0)
            prompt_tps = float(getattr(result, "prompt_tps", 0.0) or 0.0)
            generation_tps = float(getattr(result, "generation_tps", 0.0) or 0.0)
            peak_memory_gb = float(getattr(result, "peak_memory", 0.0) or 0.0)
        else:
            raw_text = str(result)
            prompt_tokens = 0
            generation_tokens = max(1, len(raw_text) // 4)
            prompt_tps = 0.0
            generation_tps = generation_tokens / latency_s
            peak_memory_gb = 0.0

        if peak_memory_gb <= 0.0:
            try:
                peak_memory_gb = float(mx.get_peak_memory()) / 1e9
            except Exception:
                peak_memory_gb = 0.0

        try:
            mx.clear_cache()
        except Exception:
            pass

        parsed_pages = parse_multi_page_output(raw_text, expected_pages=num_images)

        logger.info(
            f"MLX batch finished in {latency_ms:.0f}ms | "
            f"prompt={prompt_tokens} tok ({prompt_tps:.1f} tok/s) | "
            f"decode={generation_tokens} tok ({generation_tps:.1f} tok/s) | "
            f"peak_mem={peak_memory_gb:.2f} GB"
        )

        return BatchOcrResult(
            raw_output=raw_text,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            generation_tokens=generation_tokens,
            prompt_tps=prompt_tps,
            generation_tps=generation_tps,
            peak_memory_gb=peak_memory_gb,
            pages=parsed_pages,
        )
