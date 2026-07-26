from sqlalchemy import select

from app.models import VendorResponse, VendorResponseStatus
from app.services.rfq import latest_rfq

BUYER = "919999999999"


def test_three_pre_consented_vendors_receive_rfq(session, orchestrator, whatsapp, settings):
    # Acceptance test: buyer approves an RFQ and three pre-consented vendors
    # receive the same requirement with a response deadline.
    case = orchestrator.handle_text(
        session, BUYER, "Find pest control vendors in Jaipur by Friday"
    )
    orchestrator.handle_text(session, BUYER, "1, 2, 3")
    orchestrator.handle_text(session, BUYER, "yes")  # rfq_ready

    case = orchestrator.handle_text(session, BUYER, "approve")  # approve + send
    assert case.status == "collecting_responses"

    vendor_phones = {"+91 90000 00000", "+91 90000 00001", "+91 90000 00002"}
    sent_to_vendors = {to for to, _ in whatsapp.messages if to in vendor_phones}
    assert sent_to_vendors == vendor_phones

    responses = list(
        session.scalars(
            select(VendorResponse).where(VendorResponse.case_id == case.id)
        )
    )
    assert len(responses) == 3
    assert all(r.status == VendorResponseStatus.SENT.value for r in responses)
    assert all(r.sent_at is not None for r in responses)

    rfq = latest_rfq(session, case.id)
    # The response deadline from the case is carried on each vendor response.
    assert all(r.rfq_id == rfq.id for r in responses)
    # Each sent message contains the requirement and a response request.
    for to, body in whatsapp.messages:
        if to in vendor_phones:
            assert "pest control" in body.lower()
            assert "Please respond" in body
