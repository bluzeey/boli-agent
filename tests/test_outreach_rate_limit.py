from app.services.outreach import prepare_outreach, send_outreach
from app.services.rfq import latest_rfq

BUYER = "919999999999"


def test_batch_cap_limits_sends(session, orchestrator, whatsapp, settings):
    settings.max_outreach_per_batch = 1
    case = orchestrator.handle_text(session, BUYER, "Find pest control vendors in Jaipur")
    orchestrator.handle_text(session, BUYER, "1, 2")
    orchestrator.handle_text(session, BUYER, "yes")
    rfq = latest_rfq(session, case.id)
    prepare_outreach(session, case, rfq)

    summary = send_outreach(session, case.id, whatsapp, settings)
    assert summary.sent == 1
    assert summary.failed == 1  # second vendor hits the batch cap
