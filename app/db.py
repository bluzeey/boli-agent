import logging
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import _redact_url, get_settings
from app.models import Base

logger = logging.getLogger("boli.db")

settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
logger.info("db: creating engine for %s (pool_pre_ping=True)", _redact_url(settings.database_url))
engine = create_engine(
    settings.database_url, echo=False, pool_pre_ping=True, connect_args=connect_args
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
logger.info("db: engine ready, SessionLocal bound")


def init_db() -> None:
    Base.metadata.create_all(engine)


def check_db_connection() -> bool:
    """Test database connectivity. Returns True on success, logs errors on failure."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("db: connection check OK")
        return True
    except Exception as exc:
        logger.error("db: connection check FAILED: %s", exc)
        return False


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
