from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BoundingBoxSchema(BaseModel):
    label: str
    xmin: int
    ymin: int
    xmax: int
    ymax: int
    text: str


class DocumentPageSchema(BaseModel):
    page_number: int
    width: int
    height: int
    image_data_uri: str
    markdown: str
    raw_text: str
    bboxes: list[BoundingBoxSchema] = Field(default_factory=list)


class OcrBatchSchema(BaseModel):
    batch_index: int
    start_page: int
    end_page: int
    num_pages: int
    latency_ms: float
    prompt_tokens: int
    generation_tokens: int
    prompt_tps: float
    generation_tps: float
    peak_memory_gb: float
    raw_output: str


class TelemetryMetricsSchema(BaseModel):
    rasterize_ms: float
    llm_total_ms: float
    total_latency_ms: float
    pages_per_sec: float
    prompt_tokens: int
    generation_tokens: int
    total_tokens: int
    avg_prompt_tps: float
    avg_generation_tps: float
    peak_memory_gb: float


class DocumentSummarySchema(BaseModel):
    id: str
    filename: str
    mime_type: str
    status: str
    current_step: str
    batch_size: int
    total_pages: int
    batches_total: int
    batches_completed: int
    backend: str
    model_id: str
    total_latency_ms: float
    pages_per_sec: float
    avg_generation_tps: float
    peak_memory_gb: float
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class DocumentDetailSchema(DocumentSummarySchema):
    markdown: str
    raw_output: str
    inngest_event_id: str | None = None
    metrics: TelemetryMetricsSchema
    pages: list[DocumentPageSchema] = Field(default_factory=list)
    batches: list[OcrBatchSchema] = Field(default_factory=list)


class SubmitDocumentRequest(BaseModel):
    file: str = Field(..., description="Base64-encoded PDF or image (or Data URI)")
    filename: str = Field(default="document.pdf")
    batch_size: int | None = Field(default=None, ge=1, le=10)


class SubmitDocumentResponse(BaseModel):
    task_id: str
    document_id: str
    status: str
    batch_size: int
    inngest_event_id: str | None = None


class SystemInfoSchema(BaseModel):
    status: str
    backend: str
    model_id: str
    platform: str
    default_batch_size: int
    pdf_max_pages: int
    pdf_dpi: int
    inngest_url: str
    adminer_url: str
    extras: dict[str, Any] = Field(default_factory=dict)

