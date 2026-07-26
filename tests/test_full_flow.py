from sqlalchemy import select

from app.integrations.sarvam import heuristic_extract_requirement
from app.integrations.whatsapp import InboundWhatsAppMessage, normalize_phone
from app.models import Vendor, VendorResponse, VendorResponseStatus
from app.services.webhook_processor import WhatsAppWebhookProcessor

BUYER = "919999999999"


class _FakeSarvam:
    def extract_requirement(self, text, existing_case=None):
        return heuristic_extract_requirement(text, existing_case)

    def extract_quote(self, reply_text, required_fields):
        from app.integrations.sarvam import heuristic_extract_quote

        return heuristic_extract_quote(reply_text, required_fields)

    def transcribe_audio(self, audio, mime_type):
        raise RuntimeError("transcription not supported in tests")


def _processor(whatsapp, orchestrator):
    return WhatsAppWebhookProcessor(whatsapp, _FakeSarvam(), orchestrator)


def test_full_flow_search_to_document(session, orchestrator, whatsapp):
    processor = _processor(whatsapp, orchestrator)

    # Buyer drives the case to outreach.
    case = orchestrator.handle_text(session, BUYER, "Find pest control vendors in Jaipur")
    orchestrator.handle_text(session, BUYER, "1")
    orchestrator.handle_text(session, BUYER, "yes")
    orchestrator.handle_text(session, BUYER, "approve")  # outreach -> collecting_responses
    assert case.status == "collecting_responses"

    # Vendor 1 replies with a complete quote (extracted via heuristic).
    vendor = session.scalars(
        select(Vendor).where(Vendor.external_id.like("mock-%"))
    ).first()
    processor.process(
        session,
        [
            InboundWhatsAppMessage(
                message_id="vr-e2e-1",
                sender=normalize_phone(vendor.phone),
                message_type="text",
                text="Quote: Rs 5000, GST 18%, delivery in 3 days. Payment: 50% advance.",
            )
        ],
    )
    response = session.scalars(
        select(VendorResponse).where(VendorResponse.case_id == case.id)
    ).first()
    assert response.status == VendorResponseStatus.RESPONDED.value
    assert response.extraction_status == "extracted"
    assert response.extracted_fields["price"] == "5000"

    # Buyer compares bids.
    case = orchestrator.handle_text(session, BUYER, "compare")
    assert any("Bid comparison" in body for _, body in whatsapp.messages)
    assert any("Recommended" in body for _, body in whatsapp.messages)

    # Buyer selects vendor 1.
    case = orchestrator.handle_text(session, BUYER, "select 1")
    assert case.status == "awaiting_approval"

    # Buyer approves -> draft document generated.
    case = orchestrator.handle_text(session, BUYER, "approve")
    assert case.status == "document_ready"
    assert case.document_text is not None
    assert "Purchase Order" in case.document_text
    assert "5,000" in case.document_text  # price
    assert "5,900" in case.document_text  # effective cost (5000 + 18%)


def test_compare_then_followup_for_missing_field(session, orchestrator, whatsapp):
    processor = _processor(whatsapp, orchestrator)
    orchestrator.handle_text(session, BUYER, "Find pest control vendors in Jaipur")
    orchestrator.handle_text(session, BUYER, "1")
    orchestrator.handle_text(session, BUYER, "yes")
    orchestrator.handle_text(session, BUYER, "approve")

    vendor = session.scalars(
        select(Vendor).where(Vendor.external_id.like("mock-%"))
    ).first()
    # Vendor replies with an incomplete quote (no tax/payment).
    processor.process(
        session,
        [
            InboundWhatsAppMessage(
                message_id="vr-e2e-2",
                sender=normalize_phone(vendor.phone),
                message_type="text",
                text="Rs 4500, delivery 4 days",
            )
        ],
    )

    whatsapp.messages.clear()
    orchestrator.handle_text(session, BUYER, "followup 1")
    # A follow-up was sent to the vendor asking for the missing fields.
    vendor_msg = next(
        body for to, body in whatsapp.messages if to == vendor.phone
    )
    assert "tax" in vendor_msg.lower() or "payment" in vendor_msg.lower()
