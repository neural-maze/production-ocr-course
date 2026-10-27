import logging

import inngest

inngest_client = inngest.Inngest(
    app_id="ocr-pipeline",
    logger=logging.getLogger("uvicorn"),
    is_production=False,
)
