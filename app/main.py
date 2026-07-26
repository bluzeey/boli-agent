import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.cases import router as cases_router
from app.api.health import router as health_router
from app.api.webhooks import meta_router, twilio_router
from app.config import get_settings

logger = logging.getLogger("boli.boot")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup: FastAPI lifespan beginning")
    try:
        from app.db import check_db_connection

        check_db_connection()
    except Exception as exc:
        logger.error("startup: DB check raised: %s", exc)
    logger.info("startup: application ready, accepting requests on /health")
    yield
    logger.info("shutdown: application stopping")


logger.info("boot: creating FastAPI application")
app = FastAPI(
    title="Boli Procurement Agent",
    version="0.1.0",
    description="WhatsApp-first procurement search and sourcing backend",
    lifespan=lifespan,
)
app.include_router(health_router)
logger.info("boot: included health_router (/health)")
app.include_router(meta_router)
logger.info("boot: included meta_router (/webhooks/whatsapp)")
app.include_router(twilio_router)
logger.info("boot: included twilio_router (/webhooks/twilio/whatsapp)")
app.include_router(cases_router)
logger.info("boot: included cases_router")
logger.info("boot: application ready — %d routes registered", len(app.routes))
