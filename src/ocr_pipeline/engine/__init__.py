from functools import lru_cache

from ocr_pipeline.config import get_settings
from ocr_pipeline.engine.base import OcrEngine


@lru_cache
def get_ocr_engine() -> OcrEngine:
    settings = get_settings()
    if settings.ocr_backend == "llamacpp":
        from ocr_pipeline.engine.llamacpp_engine import LlamaCppOcrEngine

        return LlamaCppOcrEngine(
            base_url=settings.llamacpp_base_url,
            model_id=settings.llamacpp_model_file,
            max_tokens=settings.mlx_max_tokens,
            temperature=settings.mlx_temperature,
        )

    from ocr_pipeline.engine.mlx_engine import MlxOcrEngine

    return MlxOcrEngine(
        model_id=settings.mlx_model_id,
        local_model_dir=settings.mlx_model_dir,
        hf_home=settings.hf_home,
        max_tokens=settings.mlx_max_tokens,
        temperature=settings.mlx_temperature,
    )
