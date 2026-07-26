import logging

from fastapi import FastAPI

from app.api.cases import router as cases_router
from app.api.health import router as health_router
from app.api.webhooks import meta_router, twilio_router
from app.config import get_settings

logger = logging.getLogger("boli.boot")
settings = get_settings()

logger.info("boot: creating FastAPI application")
app = FastAPI(
    title="Boli Procurement Agent",
    version="0.1.0",
    description="WhatsApp-first procurement search and sourcing backend",
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
