import asyncio
import functools
import math
import shutil
import time
from pathlib import Path
from typing import Any

import inngest
from loguru import logger
from sqlalchemy import delete, select

from ocr_pipeline.config import get_settings
from ocr_pipeline.db.models import Document, DocumentPage, OcrBatch
from ocr_pipeline.db.session import session_scope
from ocr_pipeline.engine import get_ocr_engine
from ocr_pipeline.engine.pdf import clean_and_decode_base64, rasterize_document_to_pages
from ocr_pipeline.inngest.client import inngest_client

SCRATCH_ROOT = Path("/tmp/ocr_pipeline")


def _scratch_dir_for(document_id: str) -> Path:
    path = SCRATCH_ROOT / document_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ensure_page_files_on_disk(document_id: str, page_numbers: list[int]) -> list[str]:
    """
    Ensures the PNG files for the given 1-based page numbers exist on disk in /tmp/ocr_pipeline/<doc_id>.
    If missing (e.g., after a worker restart), re-materializes them from PostgreSQL `DocumentPage.image_data_uri`.
    """
    scratch_dir = _scratch_dir_for(document_id)
    paths: list[str] = []
    missing_pages: list[int] = []

    for pnum in page_numbers:
        pfile = scratch_dir / f"page_{pnum:04d}.png"
        if not pfile.exists():
            missing_pages.append(pnum)
        paths.append(str(pfile))

    if missing_pages:
        with session_scope() as session:
            stmt = select(DocumentPage).where(
                DocumentPage.document_id == document_id,
                DocumentPage.page_number.in_(missing_pages),
            )
            for row in session.scalars(stmt):
                png_bytes = clean_and_decode_base64(row.image_data_uri)
                pfile = scratch_dir / f"page_{row.page_number:04d}.png"
                pfile.write_bytes(png_bytes)

    return paths


def _step_rasterize_sync(document_id: str, batch_size: int) -> dict[str, Any]:
    settings = get_settings()
    engine = get_ocr_engine()

    with session_scope() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise ValueError(f"Document {document_id} not found in database.")
        if not doc.file_bytes_b64:
            raise ValueError(f"Document {document_id} has no payload bytes to rasterize.")

        doc.status = "rasterizing"
        doc.current_step = f"Rasterizing document at {settings.pdf_dpi} DPI..."
        doc.backend = engine.backend_name
        doc.model_id = engine.model_name
        raw_b64 = doc.file_bytes_b64

    t0 = time.perf_counter()
    file_bytes = clean_and_decode_base64(raw_b64)
    mime_type, rasterized_pages = rasterize_document_to_pages(
        file_bytes=file_bytes,
        max_pages=settings.pdf_max_pages,
        dpi=settings.pdf_dpi,
    )
    rasterize_ms = (time.perf_counter() - t0) * 1000.0

    total_pages = len(rasterized_pages)
    effective_batch_size = max(1, min(10, batch_size))
    num_batches = math.ceil(total_pages / effective_batch_size)

    scratch_dir = _scratch_dir_for(document_id)
    for rpage in rasterized_pages:
        pfile = scratch_dir / f"page_{rpage.page_number:04d}.png"
        pfile.write_bytes(rpage.png_bytes)

    with session_scope() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise ValueError(f"Document {document_id} disappeared during rasterization.")

        # Clear any existing pages/batches if this step is being retried by Inngest
        session.execute(delete(DocumentPage).where(DocumentPage.document_id == document_id))
        session.execute(delete(OcrBatch).where(OcrBatch.document_id == document_id))

        doc.mime_type = mime_type
        doc.total_pages = total_pages
        doc.batch_size = effective_batch_size
        doc.batches_total = num_batches
        doc.batches_completed = 0
        doc.rasterize_ms = round(rasterize_ms, 2)
        doc.status = "processing"
        doc.current_step = f"Rasterized {total_pages} page(s) in {rasterize_ms:.0f}ms. Starting {num_batches} batch(es)..."

        for rpage in rasterized_pages:
            session.add(
                DocumentPage(
                    document_id=document_id,
                    page_number=rpage.page_number,
                    width=rpage.width,
                    height=rpage.height,
                    image_data_uri=rpage.image_data_uri,
                    markdown="",
                    raw_text="",
                    bboxes_json=[],
                )
            )

    return {
        "document_id": document_id,
        "mime_type": mime_type,
        "total_pages": total_pages,
        "batch_size": effective_batch_size,
        "num_batches": num_batches,
        "rasterize_ms": round(rasterize_ms, 2),
    }


def _step_run_ocr_batch_sync(
    document_id: str,
    batch_index: int,
    num_batches: int,
    start_page: int,
    end_page: int,
) -> dict[str, Any]:
    page_numbers = list(range(start_page, end_page + 1))

    with session_scope() as session:
        doc = session.get(Document, document_id)
        if doc is not None:
            doc.status = "processing"
            doc.current_step = (
                f"Running OCR batch {batch_index}/{num_batches} "
                f"(page{'s' if len(page_numbers) > 1 else ''} {start_page}"
                f"{f'-{end_page}' if end_page > start_page else ''})..."
            )

    image_paths = _ensure_page_files_on_disk(document_id, page_numbers)
    engine = get_ocr_engine()
    batch_result = engine.infer_batch(image_paths)

    with session_scope() as session:
        # Remove prior batch entry if retried
        session.execute(
            delete(OcrBatch).where(
                OcrBatch.document_id == document_id,
                OcrBatch.batch_index == batch_index,
            )
        )

        session.add(
            OcrBatch(
                document_id=document_id,
                batch_index=batch_index,
                start_page=start_page,
                end_page=end_page,
                num_pages=len(page_numbers),
                latency_ms=round(batch_result.latency_ms, 2),
                prompt_tokens=batch_result.prompt_tokens,
                generation_tokens=batch_result.generation_tokens,
                prompt_tps=round(batch_result.prompt_tps, 2),
                generation_tps=round(batch_result.generation_tps, 2),
                peak_memory_gb=round(batch_result.peak_memory_gb, 3),
                raw_output=batch_result.raw_output,
            )
        )

        for parsed_page in batch_result.pages:
            target_page_num = start_page + parsed_page.page_offset
            stmt = select(DocumentPage).where(
                DocumentPage.document_id == document_id,
                DocumentPage.page_number == target_page_num,
            )
            page_row = session.scalar(stmt)
            if page_row is not None:
                page_row.markdown = parsed_page.markdown
                page_row.raw_text = parsed_page.raw_text
                page_row.bboxes_json = [b.to_dict() for b in parsed_page.bboxes]

        doc = session.get(Document, document_id)
        if doc is not None:
            doc.batches_completed = batch_index
            doc.current_step = f"Completed batch {batch_index}/{num_batches} ({batch_result.latency_ms:.0f}ms)"

    return {
        "batch_index": batch_index,
        "pages": f"{start_page}-{end_page}",
        "num_pages": len(page_numbers),
        "latency_ms": round(batch_result.latency_ms, 2),
        "prompt_tokens": batch_result.prompt_tokens,
        "generation_tokens": batch_result.generation_tokens,
        "prompt_tps": round(batch_result.prompt_tps, 2),
        "generation_tps": round(batch_result.generation_tps, 2),
        "peak_memory_gb": round(batch_result.peak_memory_gb, 3),
    }


def _step_finalize_sync(document_id: str) -> dict[str, Any]:
    with session_scope() as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise ValueError(f"Document {document_id} not found during finalization.")

        pages = list(
            session.scalars(
                select(DocumentPage)
                .where(DocumentPage.document_id == document_id)
                .order_by(DocumentPage.page_number)
            )
        )
        batches = list(
            session.scalars(
                select(OcrBatch)
                .where(OcrBatch.document_id == document_id)
                .order_by(OcrBatch.batch_index)
            )
        )

        full_markdown = "\n\n---\n\n".join(p.markdown for p in pages if p.markdown.strip())
        full_raw = "\n\n<PAGE>\n\n".join(b.raw_output for b in batches if b.raw_output.strip())

        llm_total_ms = sum(b.latency_ms for b in batches)
        total_latency_ms = doc.rasterize_ms + llm_total_ms
        total_pages = max(1, doc.total_pages)
        pages_per_sec = (total_pages / (total_latency_ms / 1000.0)) if total_latency_ms > 0 else 0.0

        total_prompt_tokens = sum(b.prompt_tokens for b in batches)
        total_gen_tokens = sum(b.generation_tokens for b in batches)

        prompt_tps_vals = [b.prompt_tps for b in batches if b.prompt_tps > 0]
        gen_tps_vals = [b.generation_tps for b in batches if b.generation_tps > 0]

        avg_prompt_tps = (sum(prompt_tps_vals) / len(prompt_tps_vals)) if prompt_tps_vals else 0.0
        avg_gen_tps = (sum(gen_tps_vals) / len(gen_tps_vals)) if gen_tps_vals else 0.0
        peak_mem_gb = max((b.peak_memory_gb for b in batches), default=0.0)

        doc.markdown = full_markdown
        doc.raw_output = full_raw
        doc.llm_total_ms = round(llm_total_ms, 2)
        doc.total_latency_ms = round(total_latency_ms, 2)
        doc.pages_per_sec = round(pages_per_sec, 3)
        doc.prompt_tokens = total_prompt_tokens
        doc.generation_tokens = total_gen_tokens
        doc.avg_prompt_tps = round(avg_prompt_tps, 2)
        doc.avg_generation_tps = round(avg_gen_tps, 2)
        doc.peak_memory_gb = round(peak_mem_gb, 3)
        doc.status = "done"
        doc.current_step = "Completed"
        doc.file_bytes_b64 = None

    # Clean up temporary PNG files on disk
    shutil.rmtree(SCRATCH_ROOT / document_id, ignore_errors=True)

    return {
        "document_id": document_id,
        "status": "done",
        "total_pages": total_pages,
        "batches_processed": len(batches),
        "total_latency_ms": round(total_latency_ms, 2),
        "pages_per_sec": round(pages_per_sec, 3),
        "prompt_tokens": total_prompt_tokens,
        "generation_tokens": total_gen_tokens,
        "avg_prompt_tps": round(avg_prompt_tps, 2),
        "avg_generation_tps": round(avg_gen_tps, 2),
        "peak_memory_gb": round(peak_mem_gb, 3),
    }


@inngest_client.create_function(
    fn_id="process-ocr-document",
    trigger=inngest.TriggerEvent(event="ocr/document.submitted"),
    concurrency=[inngest.Concurrency(limit=1)],
    retries=2,
)
async def process_document_workflow(ctx: inngest.Context) -> dict[str, Any]:
    """
    Durable multi-step OCR workflow orchestrated by Inngest.
    1. Rasterizes PDF or image at 150 DPI and stores page images in PostgreSQL.
    2. Executes multi-page OCR batches sequentially (1 batch per step) on MLX / llama.cpp.
    3. Aggregates Markdown, bounding boxes, and LLM hardware telemetry.
    """
    step = ctx.step
    document_id = str(ctx.event.data["document_id"])
    raw_bs = ctx.event.data.get("batch_size")
    requested_batch_size = int(str(raw_bs)) if raw_bs is not None else get_settings().default_batch_size

    try:
        plan = await step.run(
            "rasterize-document",
            functools.partial(asyncio.to_thread, _step_rasterize_sync, document_id, requested_batch_size),
        )

        total_pages = int(plan["total_pages"])
        batch_size = int(plan["batch_size"])
        num_batches = int(plan["num_batches"])

        for batch_idx in range(1, num_batches + 1):
            start_page = (batch_idx - 1) * batch_size + 1
            end_page = min(total_pages, batch_idx * batch_size)
            step_id = f"ocr-batch-{batch_idx}-pages-{start_page}-to-{end_page}"

            await step.run(
                step_id,
                functools.partial(
                    asyncio.to_thread,
                    _step_run_ocr_batch_sync,
                    document_id,
                    batch_idx,
                    num_batches,
                    start_page,
                    end_page,
                ),
            )

        summary = await step.run(
            "finalize-document",
            functools.partial(asyncio.to_thread, _step_finalize_sync, document_id),
        )
        return summary

    except Exception as exc:
        logger.exception(f"OCR workflow failed for document {document_id}: {exc}")
        with session_scope() as session:
            doc = session.get(Document, document_id)
            if doc is not None:
                doc.status = "failed"
                doc.current_step = "Failed"
                doc.error = str(exc)
        raise
