from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BoundingBox:
    label: str
    xmin: int
    ymin: int
    xmax: int
    ymax: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "xmin": self.xmin,
            "ymin": self.ymin,
            "xmax": self.xmax,
            "ymax": self.ymax,
            "text": self.text,
        }


@dataclass
class ParsedPage:
    page_offset: int
    markdown: str
    raw_text: str
    bboxes: list[BoundingBox] = field(default_factory=list)


@dataclass
class BatchOcrResult:
    raw_output: str
    latency_ms: float
    prompt_tokens: int
    generation_tokens: int
    prompt_tps: float
    generation_tps: float
    peak_memory_gb: float
    pages: list[ParsedPage] = field(default_factory=list)


class OcrEngine(ABC):
    """Abstract base class for multi-page OCR inference engines (MLX or llama.cpp)."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Backend identifier ('mlx' or 'llamacpp')."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier."""

    @abstractmethod
    def infer_batch(self, image_paths: list[str]) -> BatchOcrResult:
        """Run OCR inference across a batch of 1..N ordered page images in a single request."""

    def warmup(self, sample_image_path: str | None = None) -> None:
        """Optional warmup hook to preload weights and compile GPU/Metal kernels at startup."""
        return None

