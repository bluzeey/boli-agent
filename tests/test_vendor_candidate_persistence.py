from sqlalchemy import select

from app.models import VendorCandidate

BUYER = "919999999999"


def test_search_persists_candidates_with_positions(session, orchestrator):
    procurement_case = orchestrator.handle_text(
        session, BUYER, "Find pest control vendors in Jaipur"
    )

    candidates = list(
        session.scalars(
            select(VendorCandidate)
            .where(VendorCandidate.case_id == procurement_case.id)
            .order_by(VendorCandidate.position.asc())
        )
    )

    assert len(candidates) == 5
    assert [c.position for c in candidates] == [1, 2, 3, 4, 5]
    assert all(c.external_id.startswith("mock-") for c in candidates)
    assert all(c.expires_at is not None for c in candidates)
    assert all(c.selected_at is None for c in candidates)
    assert all(c.confirmed_at is None for c in candidates)
