"""Offline regressions for vendor ownership, consent, and reply assignment."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.integrations.whatsapp import InboundWhatsAppMessage, normalize_phone
from app.models import (
    BrowserOwner,
    BrowserSession,
    CaseStatus,
    ConsentSource,
    Conversation,
    InboundMessage,
    MessageStatus,
    ProcurementCase,
    Rfq,
    SearchRun,
    Vendor,
    VendorCandidate,
    VendorResponseStatus,
)
from app.services.outreach import prepare_outreach, send_outreach
from app.services.vendor_scope import VendorScopeError, vendor_scope_for_case
from app.services.webhook_processor import AmbiguousVendorReply, WhatsAppWebhookProcessor

PHONE = "+91 90000 00001"
EXTERNAL_ID = "synthetic-shared-place"


def _case(session, owner="owner-a", *, sender=None):
    now = datetime.now(UTC)
    if sender is None:
        owner_hash = sha256(owner.encode()).hexdigest()
        if session.get(BrowserOwner, owner_hash) is None:
            session.add(
                BrowserOwner(token_hash=owner_hash, expires_at=now + timedelta(days=1))
            )
            session.flush()
        browser_session = BrowserSession(id=uuid4().hex, owner_hash=owner_hash)
        session.add(browser_session)
        session.flush()
        sender = f"browser:{browser_session.id}"
    conversation = session.scalars(
        select(Conversation).where(Conversation.whatsapp_user_id == sender)
    ).first()
    if conversation is None:
        conversation = Conversation(whatsapp_user_id=sender)
        session.add(conversation)
        session.flush()
    case = ProcurementCase(
        conversation_id=conversation.id,
        status=CaseStatus.OUTREACH_APPROVED.value,
        category="generic",
        normalized_need="Synthetic office supplies",
    )
    session.add(case)
    session.flush()
    search = SearchRun(case_id=case.id, provider="google_places", query="synthetic")
    session.add(search)
    session.flush()
    candidate = VendorCandidate(
        case_id=case.id,
        search_run_id=search.id,
        external_id=EXTERNAL_ID,
        provider="google_places",
        position=1,
        name="Synthetic Vendor",
        phone=PHONE,
        confirmed_at=now,
        expires_at=now + timedelta(hours=1),
    )
    rfq = Rfq(case_id=case.id, document_text="Synthetic RFQ", version=1)
    session.add_all([candidate, rfq])
    session.flush()
    return case, candidate, rfq


def _prepare(session, case, rfq):
    response = prepare_outreach(session, case, rfq)[0]
    session.commit()
    return response, session.get(Vendor, response.vendor_id)


def _reply(message_id="synthetic-vendor-reply"):
    return InboundWhatsAppMessage(
        message_id=message_id,
        sender=normalize_phone(PHONE),
        message_type="text",
        text="Price Rs 5000, delivery in 3 days, tax included.",
    )


def _processor(whatsapp, orchestrator):
    return WhatsAppWebhookProcessor(whatsapp, orchestrator.sarvam, orchestrator)


def test_two_browser_owners_keep_same_external_id_and_consent_independent(session, orchestrator):
    case_a, _, rfq_a = _case(session, "owner-a")
    case_b, _, rfq_b = _case(session, "owner-b")
    response_a, vendor_a = _prepare(session, case_a, rfq_a)
    response_b, vendor_b = _prepare(session, case_b, rfq_b)
    response_a.status = response_b.status = VendorResponseStatus.SKIPPED_COLD.value
    session.commit()

    assert vendor_a.id != vendor_b.id
    assert vendor_a.external_id == vendor_b.external_id == EXTERNAL_ID
    assert vendor_a.owner_scope != vendor_b.owner_scope
    assert orchestrator._grant_consent(session, case_a.id, [1]) == 1
    assert vendor_a.contact_consent is True
    assert vendor_a.consent_source == ConsentSource.BUYER_CONFIRMED.value
    assert response_a.status == VendorResponseStatus.QUEUED.value
    assert vendor_b.contact_consent is False
    assert vendor_b.consented_at is None
    assert response_b.status == VendorResponseStatus.SKIPPED_COLD.value
    assert orchestrator._vendor_response_for_position(session, case_b.id, 1) == (
        vendor_b, response_b
    )


@pytest.mark.parametrize("opted_out", [False, True])
def test_same_browser_owner_reuses_consent_across_registered_sessions(session, opted_out):
    case_a, _, rfq_a = _case(session)
    _, vendor_a = _prepare(session, case_a, rfq_a)
    vendor_a.contact_consent = True
    vendor_a.consent_source = ConsentSource.BUYER_CONFIRMED.value
    vendor_a.opted_out = opted_out
    session.commit()
    case_b, _, rfq_b = _case(session)
    _, vendor_b = _prepare(session, case_b, rfq_b)

    assert case_a.conversation_id != case_b.conversation_id
    assert vendor_b.id == vendor_a.id
    assert vendor_b.contact_consent is True
    assert vendor_b.opted_out is opted_out
    assert session.scalar(select(func.count()).select_from(Vendor)) == 1


def test_legacy_null_vendor_is_not_adopted_or_exposed(session, orchestrator):
    legacy = Vendor(
        external_id=EXTERNAL_ID,
        name="Legacy private contact",
        phone="919111111111",
        provider="google_places",
        owner_scope=None,
        contact_consent=True,
        opted_out=True,
    )
    session.add(legacy)
    session.commit()
    case, candidate, rfq = _case(session)
    response, vendor = _prepare(session, case, rfq)

    assert vendor.id != legacy.id
    assert vendor.owner_scope == vendor_scope_for_case(session, case)
    assert vendor.contact_consent is False
    assert vendor.opted_out is False
    assert vendor.phone == candidate.phone
    assert orchestrator._vendor_response_for_position(session, case.id, 1) == (vendor, response)
    comparison = orchestrator._scoped_comparison(session, case)
    assert [bid.vendor_id for bid in comparison.bids] == [vendor.id]
    assert "Legacy private contact" not in str(comparison)
    assert legacy.owner_scope is None
    assert legacy.contact_consent is True
    assert legacy.opted_out is True


def test_unregistered_browser_is_denied_without_guessing_or_adoption(
    session, orchestrator, whatsapp, settings
):
    case, _, rfq = _case(session, sender=f"browser:{uuid4().hex}")
    with pytest.raises(VendorScopeError, match="registered"):
        vendor_scope_for_case(session, case)
    with pytest.raises(VendorScopeError):
        prepare_outreach(session, case, rfq)
    with pytest.raises(VendorScopeError):
        send_outreach(session, case.id, whatsapp, settings)
    with pytest.raises(VendorScopeError):
        orchestrator._grant_consent(session, case.id, [1])
    assert session.scalar(select(func.count()).select_from(Vendor)) == 0
    assert session.scalar(select(func.count()).select_from(BrowserSession)) == 0
    assert whatsapp.messages == []


def test_whatsapp_scope_is_stable_after_normalizing_and_isolates_other_buyers(session):
    case_a, _, rfq_a = _case(session, sender="whatsapp:+91 98888 88881")
    case_b, _, rfq_b = _case(session, sender="919888888881")
    case_c, _, rfq_c = _case(session, sender="919888888882")
    _, vendor_a = _prepare(session, case_a, rfq_a)
    _, vendor_b = _prepare(session, case_b, rfq_b)
    _, vendor_c = _prepare(session, case_c, rfq_c)
    expected = sha256(b"whatsapp:919888888881").hexdigest()

    assert vendor_scope_for_case(session, case_a.id) == expected
    assert vendor_scope_for_case(session=session, procurement_case=case_b) == expected
    assert vendor_a.id == vendor_b.id
    assert vendor_c.id != vendor_a.id
    assert vendor_c.owner_scope != expected


@pytest.mark.parametrize("invalid", ["missing-case", "missing-conversation", "empty-sender"])
def test_missing_buyer_identity_fails_closed(session, invalid):
    if invalid == "missing-case":
        case = "nonexistent-case"
    elif invalid == "missing-conversation":
        case = ProcurementCase(conversation_id="nonexistent-conversation")
    else:
        case, _, _ = _case(session, sender="")
    with pytest.raises(VendorScopeError):
        vendor_scope_for_case(session, case)


def test_consent_requires_response_association_on_current_case(session, orchestrator):
    case_a, _, rfq_a = _case(session)
    _, vendor = _prepare(session, case_a, rfq_a)
    case_b, _, _ = _case(session)

    assert orchestrator._grant_consent(session, case_b.id, [1]) == 0
    assert vendor.contact_consent is False
    assert vendor.consented_at is None


@pytest.mark.parametrize("legacy", [False, True])
def test_foreign_or_null_vendor_response_cannot_consent_send_compare_or_select(
    session, orchestrator, whatsapp, settings, legacy
):
    case_a, _, rfq_a = _case(session, "owner-a")
    _, vendor_a = _prepare(session, case_a, rfq_a)
    if legacy:
        vendor_a.owner_scope = None
    case_b, _, rfq_b = _case(session, "owner-b")
    response_b, vendor_b = _prepare(session, case_b, rfq_b)
    response_b.vendor_id = vendor_a.id
    vendor_a.contact_consent = False
    case_b.selected_vendor_id = vendor_a.id
    session.commit()

    assert orchestrator._vendor_response_for_position(session, case_b.id, 1) is None
    assert orchestrator._grant_consent(session, case_b.id, [1]) == 0
    assert vendor_a.contact_consent is False
    assert vendor_b.contact_consent is False
    with pytest.raises(VendorScopeError):
        orchestrator._scoped_comparison(session, case_b)
    sender_b = session.get(Conversation, case_b.conversation_id).whatsapp_user_id
    orchestrator._handle_document_approval(session, sender_b, case_b)
    assert case_b.document_text is None
    assert all(to == sender_b for to, _ in whatsapp.messages)
    whatsapp.messages.clear()
    settings.allow_outreach = False
    summary = send_outreach(session, case_b.id, whatsapp, settings)
    assert summary.failed == 1
    assert summary.sent == 0
    assert response_b.status == VendorResponseStatus.FAILED.value
    assert "owner scope" in response_b.last_error
    assert whatsapp.messages == []


@pytest.mark.parametrize("same_owner", [False, True])
def test_ambiguous_phone_reply_is_held_without_newest_case_or_buyer_fallback(
    session, orchestrator, whatsapp, monkeypatch, same_owner
):
    case_a, _, rfq_a = _case(session, "owner-a")
    case_b, _, rfq_b = _case(session, "owner-a" if same_owner else "owner-b")
    response_a, _ = _prepare(session, case_a, rfq_a)
    response_b, _ = _prepare(session, case_b, rfq_b)
    case_a.created_at = datetime.now(UTC) - timedelta(days=1)
    response_a.status = response_b.status = VendorResponseStatus.SENT.value
    session.commit()
    processor = _processor(whatsapp, orchestrator)

    def unexpected_fallback(*args, **kwargs):
        pytest.fail("Ambiguous vendor reply reached buyer routing")

    monkeypatch.setattr(orchestrator, "handle_text", unexpected_fallback)
    with pytest.raises(AmbiguousVendorReply):
        processor._find_vendor_response(session, normalize_phone(PHONE))
    assert processor.process(session, [_reply()]) == 0
    record = session.scalar(select(InboundMessage))
    assert record.status == MessageStatus.FAILED.value
    assert "multiple active responses" in record.error
    assert record.conversation_id is None
    assert response_a.raw_reply is response_b.raw_reply is None
    assert response_a.status == response_b.status == VendorResponseStatus.SENT.value
    assert session.scalar(select(func.count()).select_from(Conversation)) == 2
    assert whatsapp.messages == []
    assert processor.process(session, [_reply()]) == 0  # Duplicate remains held.


def test_multiple_active_responses_on_same_case_are_also_ambiguous(session, orchestrator, whatsapp):
    case, _, rfq = _case(session)
    _prepare(session, case, rfq)
    _prepare(session, case, rfq)
    with pytest.raises(AmbiguousVendorReply):
        _processor(whatsapp, orchestrator)._find_vendor_response(session, PHONE)


@pytest.mark.parametrize("invalid", ["legacy", "foreign-owner", "unregistered-browser"])
def test_invalid_owner_reply_never_becomes_buyer_message(
    session, orchestrator, whatsapp, monkeypatch, invalid
):
    case, _, rfq = _case(session)
    response, vendor = _prepare(session, case, rfq)
    if invalid == "legacy":
        vendor.owner_scope = None
    elif invalid == "foreign-owner":
        vendor.owner_scope = sha256(b"unrelated-owner").hexdigest()
    else:
        sender = session.get(Conversation, case.conversation_id).whatsapp_user_id
        session.delete(session.get(BrowserSession, sender.removeprefix("browser:")))
    session.commit()

    def unexpected_fallback(*args, **kwargs):
        pytest.fail("Unscoped vendor reply reached buyer routing")

    monkeypatch.setattr(orchestrator, "handle_text", unexpected_fallback)
    processor = _processor(whatsapp, orchestrator)
    with pytest.raises(VendorScopeError):
        processor._find_vendor_response(session, PHONE)
    assert processor.process(session, [_reply()]) == 0
    assert response.raw_reply is None
    record = session.scalar(select(InboundMessage))
    assert record.status == MessageStatus.FAILED.value
    assert record.conversation_id is None
    assert whatsapp.messages == []


def test_single_scoped_vendor_reply_preserves_normal_behavior(session, orchestrator, whatsapp):
    case, _, rfq = _case(session, sender="919888888881")
    response, vendor = _prepare(session, case, rfq)
    response.status = VendorResponseStatus.SENT.value
    session.commit()
    reply = _reply()
    processor = _processor(whatsapp, orchestrator)

    assert processor._find_vendor_response(session, reply.sender) == (response, vendor, case)
    assert processor.process(session, [reply]) == 1
    assert response.status == VendorResponseStatus.RESPONDED.value
    assert response.raw_reply == reply.text
    assert response.reply_message_id == reply.message_id
    assert response.responded_at is not None
    assert any(to == reply.sender and "recorded" in text for to, text in whatsapp.messages)
    assert any(to == "919888888881" and "replied" in text for to, text in whatsapp.messages)
    assert session.scalar(select(InboundMessage)).conversation_id == case.conversation_id


def test_closed_case_does_not_make_single_active_reply_ambiguous(session, orchestrator, whatsapp):
    old_case, _, old_rfq = _case(session, "old-owner")
    old_response, _ = _prepare(session, old_case, old_rfq)
    old_case.status = CaseStatus.CLOSED.value
    case, _, rfq = _case(session, "current-owner")
    response, vendor = _prepare(session, case, rfq)
    session.commit()

    assert _processor(whatsapp, orchestrator)._find_vendor_response(session, PHONE) == (
        response, vendor, case
    )
    assert old_response.raw_reply is None


def test_outreach_disabled_guard_still_blocks_scoped_consented_vendor(
    session, settings, whatsapp
):
    case, _, rfq = _case(session)
    response, vendor = _prepare(session, case, rfq)
    vendor.contact_consent = True
    session.commit()
    settings.allow_outreach = False

    summary = send_outreach(session, case.id, whatsapp, settings)
    assert summary.sent == 0
    assert summary.skipped_cold == 1
    assert response.status == VendorResponseStatus.SKIPPED_COLD.value
    assert whatsapp.messages == []
