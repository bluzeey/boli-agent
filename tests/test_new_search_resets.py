BUYER = "919999999999"


def test_new_search_closes_case_and_starts_fresh(session, orchestrator, whatsapp):
    case1 = orchestrator.handle_text(
        session, BUYER, "Find pest control vendors in Jaipur"
    )
    assert case1.status == "shortlist_ready"

    # "new search" closes the active case without parsing it as a requirement.
    returned = orchestrator.handle_text(session, BUYER, "new search")
    assert returned.id == case1.id

    session.refresh(case1)
    assert case1.status == "closed"

    # The next message starts a brand new case.
    case2 = orchestrator.handle_text(session, BUYER, "Find cleaning vendors in Delhi")
    assert case2.id != case1.id
    assert case2.status == "shortlist_ready"
    assert "cleaning" in case2.raw_request.lower()


def test_new_requirement_at_shortlist_starts_new_case(session, orchestrator):
    case1 = orchestrator.handle_text(
        session, BUYER, "Find pest control vendors in Jaipur"
    )
    assert case1.status == "shortlist_ready"

    # A plain new requirement (not a number, not "new search") starts a new case.
    case2 = orchestrator.handle_text(session, BUYER, "Find cleaning vendors in Delhi")
    assert case2.id != case1.id

    session.refresh(case1)
    assert case1.status == "closed"
