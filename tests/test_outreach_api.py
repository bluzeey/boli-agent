from sqlalchemy import select

from app.api.cases import list_case_responses, list_case_vendors, set_vendor_consent
from app.models import Vendor, VendorResponse
from app.schemas import ConsentRequest
from app.services.outreach import prepare_outreach
from app.services.rfq import latest_rfq

BUYER = "919999999999"


def _prepared_case(session, orchestrator):
    case = orchestrator.handle_text(session, BUYER, "Find pest control vendors in Jaipur")
    orchestrator.handle_text(session, BUYER, "1, 2")
    orchestrator.handle_text(session, BUYER, "yes")
    rfq = latest_rfq(session, case.id)
    prepare_outreach(session, case, rfq)
    return case


def test_list_case_vendors_returns_outreach_status(session, orchestrator):
    case = _prepared_case(session, orchestrator)
    vendors = list_case_vendors(case.id, session)
    assert len(vendors) == 2
    assert all(v.contact_consent for v in vendors)  # mock vendors pre-consented
    assert all(v.outreach_status == "queued" for v in vendors)


def test_set_vendor_consent_grants_and_revokes(session, orchestrator):
    case = _prepared_case(session, orchestrator)
    vendor = session.scalars(
        select(Vendor).where(Vendor.external_id.like("test-%"))
    ).first()

    # Revoke consent.
    result = set_vendor_consent(
        case.id,
        vendor.id,
        ConsentRequest(consent=False),
        session,
    )
    assert result.contact_consent is False

    # Grant consent again with a buyer-confirmed source.
    result = set_vendor_consent(
        case.id,
        vendor.id,
        ConsentRequest(consent=True, source="buyer_confirmed"),
        session,
    )
    assert result.contact_consent is True
    assert result.consent_source == "buyer_confirmed"


def test_list_case_responses(session, orchestrator):
    case = _prepared_case(session, orchestrator)
    responses = list_case_responses(case.id, session)
    assert len(responses) == 2
    assert all(r.case_id == case.id for r in responses)
    assert all(r.channel == "whatsapp" for r in responses)
    # VendorResponse rows are queryable via the model too.
    direct = list(
        session.scalars(
            select(VendorResponse).where(VendorResponse.case_id == case.id)
        )
    )
    assert len(direct) == 2
