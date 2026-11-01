import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.browser_chat import router as browser_chat_router
from app.api.cases import router as cases_router
from app.api.health import router as health_router
from app.api.webhooks import meta_router, twilio_router
from app.browser_access import allowed_browser_origin
from app.config import get_settings

logger = logging.getLogger("boli.boot")
settings = get_settings()

logger.info("boot: creating FastAPI application")
app = FastAPI(
    title="Boli Procurement Agent",
    version="0.1.0",
    description="WhatsApp-first procurement search and sourcing backend",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def private_browser_headers(request: Request, call_next):
    private = request.url.path.startswith(("/api/browser-chat/", "/api/cases/"))
    if (
        private
        and request.method not in {"GET", "HEAD", "OPTIONS"}
        and not allowed_browser_origin(request)
    ):
        response = JSONResponse({"detail": "Same-origin browser request required"}, status_code=403)
    else:
        response = await call_next(request)
    if private:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Vary"] = "Cookie"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


app.include_router(health_router)
logger.info("boot: included health_router (/health)")
app.include_router(meta_router)
logger.info("boot: included meta_router (/webhooks/whatsapp)")
app.include_router(twilio_router)
logger.info("boot: included twilio_router (/webhooks/twilio/whatsapp)")
app.include_router(cases_router)
logger.info("boot: included cases_router")
app.include_router(browser_chat_router)
logger.info("boot: included browser_chat_router (/chat)")
logger.info("boot: application ready — %d routes registered", len(app.routes))
