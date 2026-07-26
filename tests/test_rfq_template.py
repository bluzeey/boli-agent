from sqlalchemy import select

from app.categories.generic import GenericCategoryPack
from app.models import VendorCandidate
from app.services.rfq import latest_rfq

BUYER = "919999999999"


def test_rfq_template_contains_canonical_fields():
    snapshot = {
        "case_id": "abc-123",
        "normalized_need": "commercial pest control",
        "request_type": "recurring_service",
        "category": "generic",
        "location": "Jaipur",
        "quantity": "monthly service",
        "budget": "Rs 5000/month",
        "deadline": "2025-12-01",
        "must_haves": ["GST invoice", "emergency support"],
    }
    recipients = [
        {"candidate_id": "c1", "name": "Aarav Services", "phone": "+91 90000 00001"}
    ]

    text = GenericCategoryPack().render_rfq(snapshot, recipients)

    assert "Boli RFQ" in text
    assert "abc-123" in text
    assert "commercial pest control" in text
    assert "recurring_service" in text
    assert "Jaipur" in text
    assert "GST invoice" in text
    assert "Aarav Services" in text
    assert "+91 90000 00001" in text
    assert "No vendors have been contacted" in text


def test_rfq_version_increments_on_regen(session, orchestrator):
    case = orchestrator.handle_text(
        session, BUYER, "Find pest control vendors in Jaipur"
    )

    # First confirmation -> RFQ v1.
    orchestrator.handle_text(session, BUYER, "1")
    orchestrator.handle_text(session, BUYER, "yes")
    rfq_v1 = latest_rfq(session, case.id)
    assert rfq_v1 is not None
    assert rfq_v1.version == 1
    assert rfq_v1.status == "shown"

    # Go back to the shortlist and confirm again -> RFQ v2.
    case = orchestrator.handle_text(session, BUYER, "no")
    assert case.status == "shortlist_ready"

    orchestrator.handle_text(session, BUYER, "2")
    orchestrator.handle_text(session, BUYER, "yes")
    rfq_v2 = latest_rfq(session, case.id)
    assert rfq_v2 is not None
    assert rfq_v2.version == 2
    assert rfq_v2.status == "shown"

    # The previous version is superseded.
    session.refresh(rfq_v1)
    assert rfq_v1.status == "superseded"

    # Recipients reflect the new selection.
    recipient_positions = [
        next(
            c.position
            for c in session.scalars(
                select(VendorCandidate).where(VendorCandidate.case_id == case.id)
            )
            if c.id == r["candidate_id"]
        )
        for r in rfq_v2.recipients
    ]
    assert recipient_positions == [2]
