import base64
import platform
import uuid
from typing import Annotated, Any

import inngest
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ocr_pipeline.api.schemas import (
    BoundingBoxSchema,
    DocumentDetailSchema,
    DocumentPageSchema,
    DocumentSummarySchema,
    OcrBatchSchema,
    SubmitDocumentRequest,
    SubmitDocumentResponse,
    SystemInfoSchema,
    TelemetryMetricsSchema,
)
from ocr_pipeline.config import get_settings
from ocr_pipeline.db.models import Document
from ocr_pipeline.db.session import get_db
from ocr_pipeline.engine import get_ocr_engine
from ocr_pipeline.engine.pdf import clean_and_decode_base64, detect_document_type
from ocr_pipeline.inngest.client import inngest_client

router = APIRouter()


def _serialize_summary(doc: Document) -> DocumentSummarySchema:
    return DocumentSummarySchema(
        id=doc.id,
        filename=doc.filename,
        mime_type=doc.mime_type,
        status=doc.status,
        current_step=doc.current_step,
        batch_size=doc.batch_size,
        total_pages=doc.total_pages,
        batches_total=doc.batches_total,
        batches_completed=doc.batches_completed,
        backend=doc.backend,
        model_id=doc.model_id,
        total_latency_ms=doc.total_latency_ms,
        pages_per_sec=doc.pages_per_sec,
        avg_generation_tps=doc.avg_generation_tps,
        peak_memory_gb=doc.peak_memory_gb,
        error=doc.error,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def _serialize_detail(doc: Document) -> DocumentDetailSchema:
    pages_out = [
        DocumentPageSchema(
            page_number=p.page_number,
            width=p.width,
            height=p.height,
            image_data_uri=p.image_data_uri,
            markdown=p.markdown,
            raw_text=p.raw_text,
            bboxes=[BoundingBoxSchema(**b) for b in (p.bboxes_json or [])],
        )
        for p in doc.pages
    ]

    batches_out = [
        OcrBatchSchema(
            batch_index=b.batch_index,
            start_page=b.start_page,
            end_page=b.end_page,
            num_pages=b.num_pages,
            latency_ms=b.latency_ms,
            prompt_tokens=b.prompt_tokens,
            generation_tokens=b.generation_tokens,
            prompt_tps=b.prompt_tps,
            generation_tps=b.generation_tps,
            peak_memory_gb=b.peak_memory_gb,
            raw_output=b.raw_output,
        )
        for b in doc.batches
    ]

    metrics = TelemetryMetricsSchema(
        rasterize_ms=doc.rasterize_ms,
        llm_total_ms=doc.llm_total_ms,
        total_latency_ms=doc.total_latency_ms,
        pages_per_sec=doc.pages_per_sec,
        prompt_tokens=doc.prompt_tokens,
        generation_tokens=doc.generation_tokens,
        total_tokens=doc.prompt_tokens + doc.generation_tokens,
        avg_prompt_tps=doc.avg_prompt_tps,
        avg_generation_tps=doc.avg_generation_tps,
        peak_memory_gb=doc.peak_memory_gb,
    )

    return DocumentDetailSchema(
        id=doc.id,
        filename=doc.filename,
        mime_type=doc.mime_type,
        status=doc.status,
        current_step=doc.current_step,
        batch_size=doc.batch_size,
        total_pages=doc.total_pages,
        batches_total=doc.batches_total,
        batches_completed=doc.batches_completed,
        backend=doc.backend,
        model_id=doc.model_id,
        total_latency_ms=doc.total_latency_ms,
        pages_per_sec=doc.pages_per_sec,
        avg_generation_tps=doc.avg_generation_tps,
        peak_memory_gb=doc.peak_memory_gb,
        error=doc.error,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        markdown=doc.markdown,
        raw_output=doc.raw_output,
        inngest_event_id=doc.inngest_event_id,
        metrics=metrics,
        pages=pages_out,
        batches=batches_out,
    )


async def _enqueue_document(
    db: Session,
    filename: str,
    file_bytes: bytes,
    batch_size: int | None,
) -> SubmitDocumentResponse:
    settings = get_settings()
    engine = get_ocr_engine()

    try:
        mime_type, _ = detect_document_type(file_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    effective_batch_size = max(1, min(10, batch_size or settings.default_batch_size))
    doc_id = str(uuid.uuid4())
    b64_payload = base64.b64encode(file_bytes).decode("ascii")

    doc = Document(
        id=doc_id,
        filename=filename,
        mime_type=mime_type,
        status="queued",
        current_step="Queued — waiting for Inngest worker...",
        batch_size=effective_batch_size,
        backend=engine.backend_name,
        model_id=engine.model_name,
        file_bytes_b64=b64_payload,
    )
    db.add(doc)
    db.commit()

    event_ids: list[str] = []
    try:
        event_ids = await inngest_client.send(
            inngest.Event(
                name="ocr/document.submitted",
                data={
                    "document_id": doc_id,
                    "filename": filename,
                    "batch_size": effective_batch_size,
                },
            )
        )
        if event_ids:
            doc.inngest_event_id = event_ids[0]
            db.commit()
    except Exception as exc:
        logger.error(f"Failed to emit Inngest event for document {doc_id}: {exc}")
        doc.status = "failed"
        doc.current_step = "Failed to reach Inngest Dev Server"
        doc.error = f"Could not send event to Inngest Dev Server ({settings.inngest_base_url}): {exc}"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Inngest Dev Server is unreachable at {settings.inngest_base_url}. Ensure Docker containers are running (`make start`).",
        ) from exc

    return SubmitDocumentResponse(
        task_id=doc_id,
        document_id=doc_id,
        status="queued",
        batch_size=effective_batch_size,
        inngest_event_id=event_ids[0] if event_ids else None,
    )


@router.get("/health")
@router.get("/api/system", response_model=SystemInfoSchema)
async def get_system_info() -> SystemInfoSchema:
    settings = get_settings()
    engine = get_ocr_engine()
    return SystemInfoSchema(
        status="ok",
        backend=engine.backend_name,
        model_id=engine.model_name,
        platform=f"{platform.system()} {platform.machine()}",
        default_batch_size=settings.default_batch_size,
        pdf_max_pages=settings.pdf_max_pages,
        pdf_dpi=settings.pdf_dpi,
        inngest_url=settings.inngest_base_url,
        adminer_url=settings.adminer_url,
    )


@router.post("/api/documents", response_model=SubmitDocumentResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: Annotated[UploadFile, File(...)],
    batch_size: Annotated[int | None, Form()] = None,
    db: Session = Depends(get_db),
) -> SubmitDocumentResponse:
    file_bytes = await file.read()
    filename = file.filename or "uploaded_document.pdf"
    return await _enqueue_document(db, filename=filename, file_bytes=file_bytes, batch_size=batch_size)


@router.post("/process", response_model=SubmitDocumentResponse, status_code=status.HTTP_202_ACCEPTED)
async def process_compat_endpoint(
    request: Request,
    db: Session = Depends(get_db),
) -> SubmitDocumentResponse:
    """
    Compatible ingest endpoint supporting both multipart form uploads (`-F "file=@..."`)
    and JSON payloads (`{"file": "<base64>", "batch_size": 4}`).
    """
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="Missing 'file' field in multipart form.")
        file_bytes = await upload.read()  # type: ignore[union-attr]
        filename = getattr(upload, "filename", None) or "upload.pdf"
        raw_bs = form.get("batch_size")
        batch_size = int(str(raw_bs)) if raw_bs is not None else None
        return await _enqueue_document(db, filename=filename, file_bytes=file_bytes, batch_size=batch_size)

    body = await request.json()
    req_obj = SubmitDocumentRequest(**body)
    try:
        file_bytes = clean_and_decode_base64(req_obj.file)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid Base64 payload: {exc}") from exc

    return await _enqueue_document(
        db,
        filename=req_obj.filename,
        file_bytes=file_bytes,
        batch_size=req_obj.batch_size,
    )


@router.get("/api/documents", response_model=list[DocumentSummarySchema])
async def list_documents(
    limit: int = 25,
    db: Session = Depends(get_db),
) -> list[DocumentSummarySchema]:
    stmt = select(Document).order_by(Document.created_at.desc()).limit(max(1, min(100, limit)))
    docs = list(db.scalars(stmt))
    return [_serialize_summary(d) for d in docs]


@router.get("/api/documents/{document_id}", response_model=DocumentDetailSchema)
async def get_document(
    document_id: str,
    db: Session = Depends(get_db),
) -> DocumentDetailSchema:
    stmt = (
        select(Document)
        .where(Document.id == document_id)
        .options(selectinload(Document.pages), selectinload(Document.batches))
    )
    doc = db.scalar(stmt)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document ID not found.")
    return _serialize_detail(doc)


@router.get("/status/{task_id}")
async def get_status_compat(
    task_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Week 4/5 compatible status endpoint."""
    stmt = (
        select(Document)
        .where(Document.id == task_id)
        .options(selectinload(Document.pages), selectinload(Document.batches))
    )
    doc = db.scalar(stmt)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task ID not found.")

    detail = _serialize_detail(doc)
    result_payload = None
    if doc.status == "done":
        result_payload = {
            "markdown": detail.markdown,
            "total_pages": detail.total_pages,
            "batches_processed": detail.batches_total,
            "latency_ms": detail.total_latency_ms,
            "document_type": detail.mime_type,
            "metrics": detail.metrics.model_dump(),
            "pages": [p.model_dump() for p in detail.pages],
            "batches": [b.model_dump() for b in detail.batches],
        }

    return {
        "task_id": doc.id,
        "status": doc.status,
        "current_step": doc.current_step,
        "result": result_payload,
        "error": doc.error,
    }


@router.delete("/api/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    db: Session = Depends(get_db),
) -> None:
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document ID not found.")
    db.delete(doc)
    db.commit()

