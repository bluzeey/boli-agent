import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.cases import router as cases_router
from app.api.health import router as health_router
from app.api.webhooks import router as webhooks_router
from app.config import get_settings
from app.db import init_db

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Boli Procurement Agent",
    version="0.1.0",
    description="WhatsApp-first procurement search and sourcing backend",
    lifespan=lifespan,
)
app.include_router(health_router)
app.include_router(webhooks_router)
app.include_router(cases_router)
