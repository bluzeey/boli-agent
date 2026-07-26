import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.sarvam import SarvamClient
from app.integrations.whatsapp import InboundWhatsAppMessage, WhatsAppClient, normalize_phone
from app.models import (
    CaseStatus,
    Conversation,
    InboundMessage,
    MessageStatus,
    ProcurementCase,
    Vendor,
    VendorResponse,
    VendorResponseStatus,
)
from app.services.formatting import (
    render_vendor_ack,
    render_vendor_replied,
)
from app.services.orchestrator import ProcurementOrchestrator
from app.services.quote import extract_response_quote

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


_ACTIVE_VENDOR_CASE_STATES = {
    CaseStatus.OUTREACH_APPROVED.value,
    CaseStatus.OUTREACH_IN_PROGRESS.value,
    CaseStatus.COLLECTING_RESPONSES.value,
}


class WhatsAppWebhookProcessor:
    def __init__(
        self,
        whatsapp: WhatsAppClient,
        sarvam: SarvamClient,
        orchestrator: ProcurementOrchestrator,
    ) -> None:
        self.whatsapp = whatsapp
        self.sarvam = sarvam
        self.orchestrator = orchestrator

    def process(self, session: Session, messages: list[InboundWhatsAppMessage]) -> int:
        processed = 0
        logger.info("processor: starting to process %d message(s)", len(messages))
        for incoming in messages:
            logger.info(
                "processor: message_id=%s sender=%s type=%s text=%s",
                incoming.message_id,
                incoming.sender,
                incoming.message_type,
                (incoming.text[:200] + "...")
                if incoming.text and len(incoming.text) > 200
                else incoming.text,
            )
            duplicate = session.scalars(
                select(InboundMessage).where(
                    InboundMessage.wa_message_id == incoming.message_id
                )
            ).first()
            if duplicate:
                logger.info("processor: duplicate message_id=%s, skipping", incoming.message_id)
                continue

            record = InboundMessage(
                wa_message_id=incoming.message_id,
                sender=incoming.sender,
                message_type=incoming.message_type,
                text=incoming.text,
                media_id=incoming.media_id,
                status=MessageStatus.PROCESSING.value,
            )
            session.add(record)
            session.commit()
            logger.info("processor: persisted inbound record id=%s", record.id)

            try:
                self.whatsapp.mark_read(incoming.message_id)
                text = incoming.text
                if incoming.message_type == "audio" and incoming.media_id:
                    logger.info("processor: downloading audio media_id=%s", incoming.media_id)
                    audio, mime_type = self.whatsapp.download_media(incoming.media_id)
                    logger.info(
                        "processor: transcribing audio (%d bytes, %s)",
                        len(audio),
                        mime_type,
                    )
                    text = self.sarvam.transcribe_audio(audio, mime_type)
                    record.text = text
                    logger.info(
                        "processor: transcription complete: %s",
                        text[:200] if text else "(empty)",
                    )

                vendor_match = self._find_vendor_response(session, incoming.sender)
                if vendor_match:
                    response, vendor, case = vendor_match
                    logger.info(
                        "processor: vendor reply detected — vendor=%s case_id=%s",
                        vendor.name,
                        case.id,
                    )
                    self._handle_vendor_reply(
                        session, record, incoming, text, response, vendor, case
                    )
                    processed += 1
                    logger.info(
                        "processor: vendor reply handled for message_id=%s",
                        incoming.message_id,
                    )
                    continue

                if incoming.message_type not in {"text", "interactive"}:
                    logger.info(
                        "processor: non-text message type=%s, asking for text",
                        incoming.message_type,
                    )
                    self.whatsapp.send_text(
                        incoming.sender,
                        "For this MVP, send your requirement as text or a voice note.",
                    )
                    record.status = MessageStatus.PROCESSED.value
                    record.processed_at = utcnow()
                    session.add(record)
                    session.commit()
                    processed += 1
                    continue

                if not text:
                    raise ValueError("Inbound message had no usable text")

                logger.info(
                    "processor: dispatching to orchestrator: sender=%s text=%s",
                    incoming.sender,
                    text[:200],
                )
                procurement_case = self.orchestrator.handle_text(
                    session, incoming.sender, text
                )
                record.conversation_id = procurement_case.conversation_id
                record.status = MessageStatus.PROCESSED.value
                record.processed_at = utcnow()
                session.add(record)
                session.commit()
                processed += 1
                logger.info(
                    "processor: orchestrator complete — case_id=%s status=%s",
                    procurement_case.id,
                    procurement_case.status,
                )
            except Exception as exc:
                logger.exception(
                    "processor: FAILED to process message_id=%s: %s",
                    incoming.message_id,
                    exc,
                )
                record.status = MessageStatus.FAILED.value
                record.error = str(exc)
                record.processed_at = utcnow()
                session.add(record)
                session.commit()
                try:
                    self.whatsapp.send_text(
                        incoming.sender,
                        "I could not process that message. Please resend it as a shorter text "
                        "or voice note.",
                    )
                except Exception:
                    logger.exception("processor: failed to send error message to WhatsApp")
        logger.info("processor: finished — %d/%d message(s) processed", processed, len(messages))
        return processed

    def _find_vendor_response(
        self, session: Session, sender: str
    ) -> tuple[VendorResponse, Vendor, ProcurementCase] | None:
        """Match an inbound sender to a vendor on an active outreach case."""
        sender_digits = normalize_phone(sender)
        if not sender_digits:
            return None
        rows = session.execute(
            select(VendorResponse, Vendor, ProcurementCase)
            .join(Vendor, VendorResponse.vendor_id == Vendor.id)
            .join(ProcurementCase, ProcurementCase.id == VendorResponse.case_id)
            .where(ProcurementCase.status.in_(_ACTIVE_VENDOR_CASE_STATES))
        ).all()
        for response, vendor, case in rows:
            if normalize_phone(vendor.phone) == sender_digits:
                return response, vendor, case
        return None

    def _handle_vendor_reply(
        self,
        session: Session,
        record: InboundMessage,
        incoming: InboundWhatsAppMessage,
        text: str | None,
        response: VendorResponse,
        vendor: Vendor,
        case: ProcurementCase,
    ) -> None:
        """Link a vendor reply to its VendorResponse and notify both parties."""
        now = utcnow()
        response.status = VendorResponseStatus.RESPONDED.value
        response.responded_at = now
        response.raw_reply = text
        response.reply_message_id = incoming.message_id
        # Extract structured commercial fields from the reply (text/transcript).
        extract_response_quote(self.sarvam, response, case.category or "generic")
        response.updated_at = now
        session.add(response)

        record.conversation_id = case.conversation_id
        record.status = MessageStatus.PROCESSED.value
        record.processed_at = now
        session.add(record)
        session.commit()

        # Acknowledge the vendor.
        try:
            self.whatsapp.send_text(incoming.sender, render_vendor_ack())
        except Exception:
            logger.exception("Failed to acknowledge vendor reply")

        # Notify the buyer.
        buyer = session.scalars(
            select(Conversation).where(Conversation.id == case.conversation_id)
        ).first()
        if buyer:
            try:
                self.whatsapp.send_text(
                    buyer.whatsapp_user_id, render_vendor_replied(vendor.name, case.id)
                )
            except Exception:
                logger.exception("Failed to notify buyer of vendor reply")
