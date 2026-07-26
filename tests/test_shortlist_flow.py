from sqlalchemy import select

from app.models import VendorCandidate
from app.services.rfq import latest_rfq

BUYER = "919999999999"


def test_full_shortlist_to_outreach_approval_flow(session, orchestrator, whatsapp):
    # 1. Buyer sends a requirement -> shortlist returned.
    case = orchestrator.handle_text(
        session, BUYER, "Find pest control vendors in Jaipur"
    )
    assert case.status == "shortlist_ready"

    # 2. Buyer selects vendors 1 and 3.
    case = orchestrator.handle_text(session, BUYER, "1, 3")
    assert case.status == "awaiting_shortlist_confirmation"

    selected = [
        c
        for c in session.scalars(
            select(VendorCandidate).where(VendorCandidate.case_id == case.id)
        )
        if c.selected_at is not None
    ]
    assert {c.position for c in selected} == {1, 3}
    assert "selected" in whatsapp.messages[-1][1].lower()

    # 3. Buyer confirms -> RFQ generated and shown.
    case = orchestrator.handle_text(session, BUYER, "yes")
    assert case.status == "rfq_ready"

    rfq = latest_rfq(session, case.id)
    assert rfq is not None
    assert rfq.version == 1
    assert rfq.status == "shown"
    assert "Boli RFQ" in whatsapp.messages[-1][1]
    assert "No vendors have been contacted" in whatsapp.messages[-1][1]

    confirmed = [
        c
        for c in session.scalars(
            select(VendorCandidate).where(VendorCandidate.case_id == case.id)
        )
        if c.confirmed_at is not None
    ]
    assert len(confirmed) == 2

    # 4. Buyer approves outreach -> RFQ sent to the two selected (pre-consented) vendors.
    case = orchestrator.handle_text(session, BUYER, "approve")
    assert case.status == "collecting_responses"

    session.refresh(rfq)
    assert rfq.status == "approved"

    # The two selected mock vendors received the vendor-facing RFQ.
    vendor_phones = {"+91 90000 00000", "+91 90000 00002"}
    sent_to_vendors = {to for to, _ in whatsapp.messages if to in vendor_phones}
    assert sent_to_vendors == vendor_phones, "Both selected vendors should have been contacted."

    # Each vendor message contains the requirement and response instructions.
    for to, body in whatsapp.messages:
        if to in vendor_phones:
            assert "pest control" in body.lower()
            assert "Please respond with" in body

    # The buyer received an outreach summary.
    assert any("Outreach complete" in body for _, body in whatsapp.messages)
