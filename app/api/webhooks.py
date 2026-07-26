import json
import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.container import build_webhook_processor
from app.db import engine
from app.integrations.twilio import extract_twilio_inbound, verify_twilio_signature
from app.integrations.whatsapp import extract_inbound_messages, verify_whatsapp_signature

logger = logging.getLogger(__name__)

meta_router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])
twilio_router = APIRouter(prefix="/webhooks/twilio", tags=["whatsapp"])
settings = get_settings()


def _process_messages(messages: list) -> None:
    """Process parsed inbound messages inline or via the Celery worker."""
    if settings.process_inline:
        processor = build_webhook_processor(settings)
        with Session(engine) as session:
            processor.process(session, messages)
    else:
        from app.worker import process_inbound

        process_inbound.delay([asdict(m) for m in messages])


@meta_router.get("")
def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
) -> Response:
    if hub_mode != "subscribe" or hub_verify_token != settings.whatsapp_verify_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid verify token")
    return Response(content=hub_challenge, media_type="text/plain")


@meta_router.post("")
async def receive_meta_webhook(request: Request) -> dict[str, str]:
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    if not verify_whatsapp_signature(body, signature, settings.whatsapp_app_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON") from exc

    messages = extract_inbound_messages(payload)
    _process_messages(messages)
    return {"status": "accepted"}


@twilio_router.post("/whatsapp")
async def receive_twilio_webhook(request: Request) -> dict[str, str]:
    form = dict(await request.form())
    signature = request.headers.get("x-twilio-signature")
    # Twilio signs the full URL it posted to; reconstruct it from app_base_url.
    base = settings.app_base_url.rstrip("/")
    webhook_url = f"{base}/webhooks/twilio/whatsapp"
    if not verify_twilio_signature(webhook_url, form, signature, settings.twilio_auth_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    messages = extract_twilio_inbound(form)
    _process_messages(messages)
    return {"status": "accepted"}
