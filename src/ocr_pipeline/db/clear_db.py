from loguru import logger
from sqlalchemy import text

from ocr_pipeline.db.session import session_scope


def clear_db() -> None:
    logger.info("Truncating OCR database tables (documents, document_pages, ocr_batches)...")
    with session_scope() as session:
        session.execute(text("TRUNCATE TABLE ocr_batches, document_pages, documents RESTART IDENTITY CASCADE;"))
    logger.info("All database tables cleared.")


if __name__ == "__main__":
    clear_db()
