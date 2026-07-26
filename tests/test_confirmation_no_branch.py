from sqlalchemy import select

from app.models import VendorCandidate

BUYER = "919999999999"


def test_no_at_confirmation_returns_to_shortlist(session, orchestrator, whatsapp):
    case = orchestrator.handle_text(
        session, BUYER, "Find pest control vendors in Jaipur"
    )

    case = orchestrator.handle_text(session, BUYER, "1, 2")
    assert case.status == "awaiting_shortlist_confirmation"

    case = orchestrator.handle_text(session, BUYER, "no")
    assert case.status == "shortlist_ready"

    candidates = list(
        session.scalars(
            select(VendorCandidate).where(VendorCandidate.case_id == case.id)
        )
    )
    assert all(c.selected_at is None for c in candidates)
    assert any("cleared" in m[1].lower() for m in whatsapp.messages)
