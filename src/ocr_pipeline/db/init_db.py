from loguru import logger

from ocr_pipeline.db.models import Base
from ocr_pipeline.db.session import engine


def init_db() -> None:
    logger.info("Initializing PostgreSQL database tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables verified.")


if __name__ == "__main__":
    init_db()
