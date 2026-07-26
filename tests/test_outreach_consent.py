from sqlalchemy import select

from app.models import (
    Suppression,
    Vendor,
    VendorCandidate,
    VendorResponse,
    VendorResponseStatus,
)
from app.services.outreach import prepare_outreach, send_outreach
from app.services.rfq import latest_rfq

BUYER = "919999999999"


def _candidate_at(session, case_id, position):
    return session.scalars(
        select(VendorCandidate).where(
            VendorCandidate.case_id == case_id,
            VendorCandidate.position == position,
        )
    ).first()


def _vendor_for(session, candidate):
    return session.scalars(
        select(Vendor).where(Vendor.external_id == candidate.external_id)
    ).first()


def _response_for(session, case_id, vendor_id):
    return session.scalars(
        select(VendorResponse)
        .where(
            VendorResponse.case_id == case_id,
            VendorResponse.vendor_id == vendor_id,
        )
        .order_by(VendorResponse.created_at.desc())
    ).first()


def test_cold_vendor_skipped_and_consentted_vendor_sent(session, orchestrator, whatsapp, settings):
    case = orchestrator.handle_text(session, BUYER, "Find pest control vendors in Jaipur")
    orchestrator.handle_text(session, BUYER, "1, 2")
    orchestrator.handle_text(session, BUYER, "yes")  # rfq_ready
    rfq = latest_rfq(session, case.id)

    prepare_outreach(session, case, rfq)

    # Revoke consent on vendor 1 (simulate a cold discovered lead).
    cold = _vendor_for(session, _candidate_at(session, case.id, 1))
    cold.contact_consent = False
    cold.consent_source = None
    cold.consented_at = None
    session.add(cold)
    session.commit()

    summary = send_outreach(session, case.id, whatsapp, settings)

    assert summary.sent == 1
    assert summary.skipped_cold == 1

    cold_response = _response_for(session, case.id, cold.id)
    assert cold_response.status == VendorResponseStatus.SKIPPED_COLD.value


def test_opted_out_vendor_is_skipped(session, orchestrator, whatsapp, settings):
    case = orchestrator.handle_text(session, BUYER, "Find pest control vendors in Jaipur")
    orchestrator.handle_text(session, BUYER, "1, 2")
    orchestrator.handle_text(session, BUYER, "yes")
    rfq = latest_rfq(session, case.id)
    prepare_outreach(session, case, rfq)

    vendor = _vendor_for(session, _candidate_at(session, case.id, 1))
    vendor.opted_out = True
    session.add(vendor)
    session.commit()

    summary = send_outreach(session, case.id, whatsapp, settings)
    assert summary.sent == 1
    assert summary.skipped_cold == 1


def test_suppressed_vendor_is_skipped(session, orchestrator, whatsapp, settings):
    case = orchestrator.handle_text(session, BUYER, "Find pest control vendors in Jaipur")
    orchestrator.handle_text(session, BUYER, "1, 2")
    orchestrator.handle_text(session, BUYER, "yes")
    rfq = latest_rfq(session, case.id)
    prepare_outreach(session, case, rfq)

    vendor = _vendor_for(session, _candidate_at(session, case.id, 1))
    session.add(Suppression(key=vendor.phone, reason="vendor requested no contact"))
    session.commit()

    summary = send_outreach(session, case.id, whatsapp, settings)
    assert summary.sent == 1
    assert summary.skipped_cold == 1


def test_allow_outreach_false_skips_all(session, orchestrator, whatsapp, settings):
    settings.allow_outreach = False
    case = orchestrator.handle_text(session, BUYER, "Find pest control vendors in Jaipur")
    orchestrator.handle_text(session, BUYER, "1, 2")
    orchestrator.handle_text(session, BUYER, "yes")
    rfq = latest_rfq(session, case.id)
    prepare_outreach(session, case, rfq)

    summary = send_outreach(session, case.id, whatsapp, settings)
    assert summary.sent == 0
    assert summary.skipped_cold == 2
