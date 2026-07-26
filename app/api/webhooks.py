import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.container import build_webhook_processor
from app.db import SessionLocal, engine
from app.integrations.twilio import extract_twilio_inbound, verify_twilio_signature
from app.integrations.whatsapp import (
    extract_inbound_messages,
    normalize_phone,
    verify_whatsapp_signature,
)
from app.models import ProcurementCase, Vendor, VendorResponse, VendorResponseStatus

logger = logging.getLogger("boli.webhooks")

meta_router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])
twilio_router = APIRouter(prefix="/webhooks/twilio", tags=["whatsapp"])
settings = get_settings()
logger.info(
    "webhooks: routes registered (meta=/webhooks/whatsapp, "
    "twilio=/webhooks/twilio/whatsapp, twilio-status=/webhooks/twilio/status, "
    "provider=%s)",
    settings.whatsapp_provider,
)


def _process_messages(messages: list) -> None:
    """Process parsed inbound messages inline or via the Celery worker."""
    logger.info("process_messages: %d message(s) to process", len(messages))
    for i, msg in enumerate(messages):
        logger.info(
            "process_messages: [%d/%d] id=%s sender=%s type=%s text=%s",
            i + 1,
            len(messages),
            msg.message_id,
            msg.sender,
            msg.message_type,
            (msg.text[:200] + "...") if msg.text and len(msg.text) > 200 else msg.text,
        )

    if settings.process_inline:
        logger.info("process_messages: processing INLINE (synchronous)")
        processor = build_webhook_processor(settings)
        with Session(engine) as session:
            processor.process(session, messages)
        logger.info("process_messages: inline processing complete")
    else:
        logger.info("process_messages: dispatching to CELERY worker")
        from app.worker import process_inbound

        process_inbound.delay([asdict(m) for m in messages])
        logger.info("process_messages: celery task dispatched")


@meta_router.get("")
def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
) -> Response:
    logger.info("meta GET verify: hub.mode=%s", hub_mode)
    if hub_mode != "subscribe" or hub_verify_token != settings.whatsapp_verify_token:
        logger.warning("meta GET verify: FAILED (token mismatch)")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid verify token")
    logger.info("meta GET verify: OK, returning challenge")
    return Response(content=hub_challenge, media_type="text/plain")


@meta_router.post("")
async def receive_meta_webhook(request: Request) -> dict[str, str]:
    logger.info("meta POST /webhooks/whatsapp: received request")
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    logger.info(
        "meta POST: signature present=%s, app_secret set=%s, body size=%d bytes",
        bool(signature),
        bool(settings.whatsapp_app_secret),
        len(body),
    )
    if not verify_whatsapp_signature(body, signature, settings.whatsapp_app_secret):
        logger.warning("meta POST: signature verification FAILED")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
    logger.info("meta POST: signature verification OK")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        logger.error("meta POST: invalid JSON: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON") from exc

    logger.info("meta POST: payload parsed, top-level keys=%s", list(payload.keys()))
    messages = extract_inbound_messages(payload)
    logger.info("meta POST: extracted %d message(s)", len(messages))
    _process_messages(messages)
    return {"status": "accepted"}


@twilio_router.post("/whatsapp")
async def receive_twilio_webhook(request: Request) -> dict[str, str]:
    logger.info("twilio POST /webhooks/twilio/whatsapp: received request")

    form = dict(await request.form())
    logger.info(
        "twilio POST: form fields=%s",
        {k: (v[:100] if isinstance(v, str) and len(v) > 100 else v) for k, v in form.items()},
    )

    signature = request.headers.get("x-twilio-signature")
    logger.info(
        "twilio POST: signature present=%s, auth_token set=%s",
        bool(signature),
        bool(settings.twilio_auth_token),
    )

    base = settings.app_base_url.rstrip("/")
    webhook_url = f"{base}/webhooks/twilio/whatsapp"
    logger.info("twilio POST: reconstructed webhook_url=%s", webhook_url)

    if not verify_twilio_signature(webhook_url, form, signature, settings.twilio_auth_token):
        logger.warning(
            "twilio POST: signature verification FAILED (url=%s, signature=%s)",
            webhook_url,
            signature,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")
    logger.info("twilio POST: signature verification OK")

    messages = extract_twilio_inbound(form)
    logger.info("twilio POST: extracted %d message(s)", len(messages))
    for i, msg in enumerate(messages):
        logger.info(
            "twilio POST: message [%d] id=%s sender=%s type=%s text=%s media_id=%s",
            i + 1,
            msg.message_id,
            msg.sender,
            msg.message_type,
            (msg.text[:200] + "...") if msg.text and len(msg.text) > 200 else msg.text,
            msg.media_id,
        )

    _process_messages(messages)
    logger.info("twilio POST: processing complete, returning accepted")
    return {"status": "accepted"}


_ACTIVE_OUTREACH_STATES = {
    "outreach_approved",
    "outreach_in_progress",
    "collecting_responses",
}


@twilio_router.post("/status")
async def receive_twilio_status(request: Request) -> dict[str, str]:
    """Receive Twilio delivery status callbacks for outbound messages.

    Twilio posts form-encoded fields: MessageSid, MessageStatus, To, From,
    ErrorCode, ErrorMessage, etc.  We log every callback and best-effort
    update the matching VendorResponse row.
    """
    form = dict(await request.form())
    message_sid = form.get("MessageSid", "")
    message_status = form.get("MessageStatus", "")
    to_number = form.get("To", "")
    error_code = form.get("ErrorCode", "")
    error_message = form.get("ErrorMessage", "")

    logger.info(
        "twilio STATUS: MessageSid=%s MessageStatus=%s To=%s ErrorCode=%s ErrorMessage=%s",
        message_sid,
        message_status,
        to_number,
        error_code,
        error_message,
    )

    # Best-effort: match the recipient phone to a VendorResponse on an active case.
    to_digits = normalize_phone(to_number)
    if to_digits:
        try:
            with SessionLocal() as session:
                rows = session.execute(
                    select(VendorResponse, Vendor, ProcurementCase)
                    .join(Vendor, VendorResponse.vendor_id == Vendor.id)
                    .join(ProcurementCase, ProcurementCase.id == VendorResponse.case_id)
                    .where(ProcurementCase.status.in_(_ACTIVE_OUTREACH_STATES))
                    .order_by(VendorResponse.updated_at.desc())
                ).all()

                matched = False
                for response, vendor, case in rows:
                    if normalize_phone(vendor.phone) == to_digits:
                        now = datetime.now(UTC)
                        if message_status == "sent":
                            response.sent_at = now
                            response.status = VendorResponseStatus.SENT.value
                        elif message_status == "delivered":
                            response.delivered_at = now
                            response.status = VendorResponseStatus.DELIVERED.value
                        elif message_status in ("failed", "undelivered"):
                            response.status = VendorResponseStatus.FAILED.value
                            response.last_error = f"Twilio: {error_message or message_status}"
                        response.updated_at = now
                        session.add(response)
                        session.commit()
                        logger.info(
                            "twilio STATUS: matched VendorResponse id=%s case_id=%s "
                            "vendor=%s -> status=%s",
                            response.id,
                            case.id,
                            vendor.name,
                            response.status,
                        )
                        matched = True
                        break

                if not matched:
                    logger.info(
                        "twilio STATUS: no matching VendorResponse for To=%s",
                        to_number,
                    )
        except Exception as exc:
            logger.exception("twilio STATUS: error updating VendorResponse: %s", exc)

    return {"status": "received"}
