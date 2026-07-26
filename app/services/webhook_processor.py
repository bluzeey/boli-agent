import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.sarvam import SarvamClient
from app.integrations.whatsapp import WhatsAppClient, extract_inbound_messages
from app.models import InboundMessage, MessageStatus
from app.services.orchestrator import ProcurementOrchestrator

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


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

    def process(self, session: Session, payload: dict[str, Any]) -> int:
        processed = 0
        for incoming in extract_inbound_messages(payload):
            duplicate = session.scalars(
                select(InboundMessage).where(
                    InboundMessage.wa_message_id == incoming.message_id
                )
            ).first()
            if duplicate:
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

            try:
                self.whatsapp.mark_read(incoming.message_id)
                text = incoming.text
                if incoming.message_type == "audio" and incoming.media_id:
                    audio, mime_type = self.whatsapp.download_media(incoming.media_id)
                    text = self.sarvam.transcribe_audio(audio, mime_type)
                    record.text = text
                elif incoming.message_type not in {"text", "interactive"}:
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

                procurement_case = self.orchestrator.handle_text(
                    session, incoming.sender, text
                )
                record.conversation_id = procurement_case.conversation_id
                record.status = MessageStatus.PROCESSED.value
                record.processed_at = utcnow()
                session.add(record)
                session.commit()
                processed += 1
            except Exception as exc:
                logger.exception("Failed to process WhatsApp message %s", incoming.message_id)
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
                    logger.exception("Failed to send error message to WhatsApp")
        return processed
