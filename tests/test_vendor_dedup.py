from sqlalchemy import select

from app.models import Vendor
from app.services.outreach import prepare_outreach
from app.services.rfq import latest_rfq

BUYER = "919999999999"


def test_same_external_id_dedupes_vendor_across_cases(session, orchestrator, whatsapp, settings):
    # Case A with a given query.
    case_a = orchestrator.handle_text(session, BUYER, "Find pest control vendors in Jaipur")
    orchestrator.handle_text(session, BUYER, "1")
    orchestrator.handle_text(session, BUYER, "yes")
    rfq_a = latest_rfq(session, case_a.id)
    prepare_outreach(session, case_a, rfq_a)

    candidate = session.scalars(
        select(Vendor).where(Vendor.external_id.like("mock-%"))
    ).first()
    assert candidate is not None
    assert candidate.contact_consent is True  # mock = pre-consented

    # Case B with the SAME query produces candidates with the same external_ids.
    case_b = orchestrator.handle_text(session, BUYER, "Find pest control vendors in Jaipur")
    orchestrator.handle_text(session, BUYER, "1")
    orchestrator.handle_text(session, BUYER, "yes")
    rfq_b = latest_rfq(session, case_b.id)
    prepare_outreach(session, case_b, rfq_b)

    # Revoke consent on case A's vendor to prove case B reuses the SAME row.
    candidate.contact_consent = False
    session.add(candidate)
    session.commit()
    external_id = candidate.external_id

    vendor_b = session.scalars(
        select(Vendor).where(Vendor.external_id == external_id)
    ).first()
    assert vendor_b.id == candidate.id  # same durable row
    assert vendor_b.contact_consent is False  # consent state shared/preserved
