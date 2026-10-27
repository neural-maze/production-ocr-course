from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False, default="application/octet-stream")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    current_step: Mapped[str] = mapped_column(String(256), nullable=False, default="Queued for processing")

    batch_size: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    total_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    batches_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    batches_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    backend: Mapped[str] = mapped_column(String(32), nullable=False, default="mlx")
    model_id: Mapped[str] = mapped_column(String(256), nullable=False, default="mlx-community/Unlimited-OCR-2-8bit")

    markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Server & LLM Telemetry
    rasterize_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    llm_total_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pages_per_sec: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generation_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_prompt_tps: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_generation_tps: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    peak_memory_gb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    inngest_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_bytes_b64: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    pages: Mapped[list["DocumentPage"]] = relationship(
        "DocumentPage",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentPage.page_number",
    )
    batches: Mapped[list["OcrBatch"]] = relationship(
        "OcrBatch",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="OcrBatch.batch_index",
    )


class DocumentPage(Base):
    __tablename__ = "document_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    image_data_uri: Mapped[str] = mapped_column(Text, nullable=False, default="")
    markdown: Mapped[str] = mapped_column(Text, nullable=False, default="")
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    bboxes_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)

    document: Mapped["Document"] = relationship("Document", back_populates="pages")


class OcrBatch(Base):
    __tablename__ = "ocr_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    batch_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_page: Mapped[int] = mapped_column(Integer, nullable=False)
    end_page: Mapped[int] = mapped_column(Integer, nullable=False)
    num_pages: Mapped[int] = mapped_column(Integer, nullable=False)

    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generation_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tps: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    generation_tps: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    peak_memory_gb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    raw_output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    document: Mapped["Document"] = relationship("Document", back_populates="batches")
