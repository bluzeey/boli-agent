from sqlalchemy import select

from app.integrations.sarvam import heuristic_extract_requirement
from app.integrations.whatsapp import InboundWhatsAppMessage, normalize_phone
from app.models import (
    Vendor,
    VendorResponse,
    VendorResponseStatus,
)
from app.services.webhook_processor import WhatsAppWebhookProcessor

BUYER = "919999999999"


class _FakeSarvam:
    def extract_requirement(self, text, existing_case=None):
        return heuristic_extract_requirement(text, existing_case)

    def transcribe_audio(self, audio, mime_type):
        raise RuntimeError("transcription not supported in tests")


def _processor(whatsapp, orchestrator):
    return WhatsAppWebhookProcessor(whatsapp, _FakeSarvam(), orchestrator)


def _drive_to_outreach(orchestrator, session):
    case = orchestrator.handle_text(session, BUYER, "Find pest control vendors in Jaipur")
    orchestrator.handle_text(session, BUYER, "1")  # select vendor 1
    orchestrator.handle_text(session, BUYER, "yes")  # confirm -> RFQ
    orchestrator.handle_text(session, BUYER, "approve")  # approve -> send RFQ
    return case


def test_vendor_reply_is_linked_and_parties_notified(session, orchestrator, whatsapp):
    case = _drive_to_outreach(orchestrator, session)

    vendor = session.scalars(
        select(Vendor).where(Vendor.external_id.like("test-%"))
    ).first()
    assert vendor is not None
    sender_digits = normalize_phone(vendor.phone)

    processor = _processor(whatsapp, orchestrator)
    reply = InboundWhatsAppMessage(
        message_id="vendor-reply-1",
        sender=sender_digits,
        message_type="text",
        text="We can quote Rs 5000 per unit, delivery in 3 days.",
    )
    processor.process(session, [reply])

    response = session.scalars(
        select(VendorResponse).where(VendorResponse.case_id == case.id)
    ).first()
    assert response.status == VendorResponseStatus.RESPONDED.value
    assert response.responded_at is not None
    assert response.raw_reply == "We can quote Rs 5000 per unit, delivery in 3 days."
    assert response.reply_message_id == "vendor-reply-1"

    # Vendor was acknowledged.
    assert any(
        to == sender_digits and "recorded" in body.lower() for to, body in whatsapp.messages
    )
    # Buyer was notified.
    assert any(to == BUYER and "replied" in body.lower() for to, body in whatsapp.messages)


def test_buyer_message_is_not_treated_as_vendor_reply(session, orchestrator, whatsapp):
    case = _drive_to_outreach(orchestrator, session)
    processor = _processor(whatsapp, orchestrator)

    # The buyer sends "status" — must NOT be matched as a vendor reply.
    msg = InboundWhatsAppMessage(
        message_id="buyer-status-1", sender=BUYER, message_type="text", text="status"
    )
    processor.process(session, [msg])

    response = session.scalars(
        select(VendorResponse).where(VendorResponse.case_id == case.id)
    ).first()
    assert response.status != VendorResponseStatus.RESPONDED.value
    # The buyer received a status summary (routed through the orchestrator).
    assert any(to == BUYER and "Case status" in body for to, body in whatsapp.messages)


def test_unrecognized_text_at_collecting_does_not_close_case(session, orchestrator, whatsapp):
    case = _drive_to_outreach(orchestrator, session)
    processor = _processor(whatsapp, orchestrator)

    msg = InboundWhatsAppMessage(
        message_id="buyer-mumble-1", sender=BUYER, message_type="text", text="any updates?"
    )
    processor.process(session, [msg])

    # The case stays open at collecting_responses (not closed, not a new case).
    session.refresh(case)
    assert case.status == "collecting_responses"
    assert any("didn't recognise" in body.lower() for to, body in whatsapp.messages)


def test_status_command_reports_responded_count(session, orchestrator, whatsapp):
    _drive_to_outreach(orchestrator, session)
    processor = _processor(whatsapp, orchestrator)

    vendor = session.scalars(
        select(Vendor).where(Vendor.external_id.like("test-%"))
    ).first()
    # Vendor replies.
    processor.process(
        session,
        [
            InboundWhatsAppMessage(
                message_id="vr1",
                sender=normalize_phone(vendor.phone),
                message_type="text",
                text="Quote: Rs 5000.",
            )
        ],
    )
    whatsapp.messages.clear()
    # Buyer asks for status.
    processor.process(
        session,
        [
            InboundWhatsAppMessage(
                message_id="bs1", sender=BUYER, message_type="text", text="status"
            )
        ],
    )
    status_msg = next(body for to, body in whatsapp.messages if to == BUYER)
    assert "Vendors responded: 1" in status_msg
    assert "RFQs sent: 1" in status_msg
