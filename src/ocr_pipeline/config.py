from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Runtime Backend: "mlx" (Apple Silicon native) or "llamacpp" (Linux/Docker container)
    ocr_backend: Literal["mlx", "llamacpp"] = "mlx"

    # MLX Model Configuration
    mlx_model_id: str = "mlx-community/Unlimited-OCR-2-8bit"
    mlx_model_dir: str = "./models/Unlimited-OCR-2-8bit"
    hf_home: str = "./models/hf_cache"
    mlx_max_tokens: int = 8192
    mlx_temperature: float = 0.0

    # Multi-Page Batching & PDF Rasterization
    default_batch_size: int = 4
    pdf_max_pages: int = 40
    pdf_dpi: int = 150

    # FastAPI Server
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # PostgreSQL Database
    database_url: str = "postgresql+psycopg://ocr:ocr@localhost:5432/ocr_db"

    # External Dashboard URLs (for UI quick links)
    inngest_base_url: str = "http://localhost:8288"
    adminer_url: str = "http://localhost:8081"

    # llama.cpp Server Configuration (Linux / Docker Desktop mode)
    llamacpp_base_url: str = "http://localhost:8080"
    llamacpp_model_file: str = "unlimited-ocr-2.gguf"


@lru_cache
def get_settings() -> Settings:
    return Settings()
