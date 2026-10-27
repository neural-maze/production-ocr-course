import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import inngest.fast_api
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from ocr_pipeline.api.routes import router as api_router
from ocr_pipeline.db.init_db import init_db
from ocr_pipeline.engine import get_ocr_engine
from ocr_pipeline.inngest.client import inngest_client
from ocr_pipeline.inngest.workflows import process_document_workflow

STATIC_DIR = Path(__file__).parent / "static"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WARMUP_IMAGE = REPO_ROOT / "ocr-course" / "week4_rust_gateway_deployment" / "sample_image.png"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    try:
        init_db()
    except Exception as exc:
        logger.warning(f"Database initialization skipped or deferred during startup: {exc}")

    try:
        engine = get_ocr_engine()
        sample_img = str(DEFAULT_WARMUP_IMAGE) if DEFAULT_WARMUP_IMAGE.exists() else None
        await asyncio.to_thread(engine.warmup, sample_img)
    except Exception as exc:
        logger.warning(f"Engine warmup skipped during startup: {exc}")

    yield


app = FastAPI(
    title="Local Durable OCR Pipeline (Inngest + MLX / llama.cpp)",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Inngest durable workflow endpoint at /api/inngest
inngest.fast_api.serve(
    app,
    inngest_client,
    [process_document_workflow],
)

# Mount REST API routes
app.include_router(api_router)

# Mount static assets and minimalist web visualizer
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def serve_ui() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))

