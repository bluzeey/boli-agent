from sqlalchemy import select

from app.models import (
    ExtractionStatus,
    VendorResponse,
    VendorResponseStatus,
)
from app.services.comparison import build_comparison

BUYER = "919999999999"


def _drive_to_outreach(orchestrator, session):
    case = orchestrator.handle_text(session, BUYER, "Find pest control vendors in Jaipur")
    orchestrator.handle_text(session, BUYER, "1, 2")
    orchestrator.handle_text(session, BUYER, "yes")
    orchestrator.handle_text(session, BUYER, "approve")
    return case


def _set_quote(session, case_id, idx, fields):
    response = list(
        session.scalars(
            select(VendorResponse)
            .where(VendorResponse.case_id == case_id)
            .order_by(VendorResponse.created_at.asc())
        )
    )[idx]
    response.status = VendorResponseStatus.RESPONDED.value
    response.extracted_fields = fields
    response.extraction_status = ExtractionStatus.EXTRACTED.value
    session.add(response)
    session.commit()
    return response


def test_comparison_recommends_lowest_complete_effective_cost(session, orchestrator):
    case = _drive_to_outreach(orchestrator, session)

    # Vendor 1: complete, price 5000 + 18% tax => effective 5900.
    _set_quote(session, case.id, 0, {
        "price": "5000", "tax": "18%", "lead_time": "3 days",
        "payment_terms": "50% advance",
    })
    # Vendor 2: cheaper (4000) but missing tax => incomplete.
    _set_quote(session, case.id, 1, {
        "price": "4000", "lead_time": "5 days",
    })

    comparison = build_comparison(session, case)
    assert comparison.recommendation is not None
    assert comparison.recommendation.price == 5000.0
    assert comparison.recommendation.effective_cost == 5900.0
    # A warning flags the cheapest bid being incomplete.
    assert any("cheapest" in w for w in comparison.warnings)


def test_comparison_no_recommendation_when_all_incomplete(session, orchestrator):
    case = _drive_to_outreach(orchestrator, session)
    _set_quote(session, case.id, 0, {"price": "5000"})  # missing tax, lead_time, payment
    _set_quote(session, case.id, 1, {"price": "4000"})  # missing tax, lead_time, payment

    comparison = build_comparison(session, case)
    assert comparison.recommendation is None
    assert len(comparison.warnings) >= 1


def test_comparison_warns_about_non_respondents(session, orchestrator):
    case = _drive_to_outreach(orchestrator, session)
    # Only vendor 1 responded.
    _set_quote(session, case.id, 0, {
        "price": "5000", "tax": "18%", "lead_time": "3 days",
        "payment_terms": "50% advance",
    })

    comparison = build_comparison(session, case)
    assert comparison.recommendation is not None
    assert any("not responded" in w for w in comparison.warnings)
