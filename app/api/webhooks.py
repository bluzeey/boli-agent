import json

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.container import build_webhook_processor
from app.db import engine
from app.integrations.whatsapp import verify_whatsapp_signature

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])
settings = get_settings()


@router.get("")
def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
) -> Response:
    if hub_mode != "subscribe" or hub_verify_token != settings.whatsapp_verify_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid verify token")
    return Response(content=hub_challenge, media_type="text/plain")


@router.post("")
async def receive_webhook(request: Request) -> dict[str, str]:
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    if not verify_whatsapp_signature(body, signature, settings.whatsapp_app_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON") from exc

    if settings.process_inline:
        processor = build_webhook_processor(settings)
        with Session(engine) as session:
            processor.process(session, payload)
    else:
        from app.worker import process_whatsapp_payload

        process_whatsapp_payload.delay(payload)

    return {"status": "accepted"}
