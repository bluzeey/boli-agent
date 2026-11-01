import secrets
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.browser_access import OWNER_COOKIE, owner_digest
from app.config import Settings
from app.integrations.hybrid_whatsapp import HybridWhatsAppClient
from app.integrations.twilio import TwilioWhatsAppClient
from app.integrations.whatsapp import InboundWhatsAppMessage, WhatsAppClient, normalize_phone
from app.models import BrowserOwner, BrowserSession, ChatMessage, Vendor, VendorResponse
from app.services.webhook_processor import WhatsAppWebhookProcessor
from tests.conftest import FakeSarvam, FakeWhatsApp
from tests.test_outreach_api import BUYER, _prepared_case


@pytest.mark.parametrize("client_class", [TwilioWhatsAppClient, WhatsAppClient])
@pytest.mark.parametrize("enabled", [False, True])
def test_provider_send_gate_precedes_http_even_with_credentials(client_class, enabled):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "synthetic-message"})

    settings = Settings(
        allow_outreach=enabled,
        twilio_account_sid="synthetic-account",
        twilio_auth_token="synthetic-key",
        twilio_whatsapp_from="+19999999999",
        whatsapp_access_token="synthetic-key",
        whatsapp_phone_number_id="synthetic-phone",
    )
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = client_class(settings, http)
        result = client.send_text("+19999999998", "synthetic body")
        assert len(requests) == int(enabled)
        if not enabled:
            assert result == {"blocked": True, "reason": "outreach_disabled"}


@pytest.mark.parametrize("enabled", [False, True])
def test_meta_read_receipts_respect_gate(enabled):
    requests = []
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json={})
        )
    ) as http:
        client = WhatsAppClient(
            Settings(
                allow_outreach=enabled,
                whatsapp_access_token="synthetic-key",
                whatsapp_phone_number_id="synthetic-phone",
            ),
            http,
        )
        client.mark_read("synthetic-message")
    assert len(requests) == int(enabled)


def test_disabled_hybrid_preserves_browser_reply_only(engine, settings, monkeypatch):
    import app.db as db

    monkeypatch.setattr(db, "engine", engine)
    settings.allow_outreach = False
    base = FakeWhatsApp()
    hybrid = HybridWhatsAppClient(base, settings)
    assert hybrid.send_text("+19999999998", "vendor action")["blocked"]
    assert hybrid.send_text("browser:synthetic-session", "assistant reply")["browser"]
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        assert session.scalars(select(ChatMessage.body)).all() == ["assistant reply"]
    assert base.messages == []


@pytest.mark.parametrize("enabled", [False, True])
def test_followup_checks_switch_before_vendor_call(
    session, orchestrator, whatsapp, settings, enabled
):
    case = _prepared_case(session, orchestrator)
    case.status = "collecting_responses"
    session.commit()
    settings.allow_outreach = enabled
    whatsapp.messages.clear()
    orchestrator.handle_text(session, BUYER, "followup 1")
    vendor = session.scalars(select(Vendor)).first()
    assert any(to == vendor.phone for to, _ in whatsapp.messages) is enabled
    if not enabled:
        assert whatsapp.messages == [(BUYER, "Outreach is disabled. No vendor follow-up was sent.")]


@pytest.mark.parametrize("enabled", [False, True])
def test_direct_outreach_route_respects_switch(
    session, orchestrator, whatsapp, settings, enabled, monkeypatch
):
    import app.config as config
    import app.container as container
    import app.main
    import tests.test_outreach_api as outreach_tests
    from app.db import get_session

    token = secrets.token_urlsafe(32)
    owner_hash = owner_digest(token)
    chat_id = uuid4().hex
    session.add(
        BrowserOwner(token_hash=owner_hash, expires_at=datetime.now(UTC) + timedelta(days=1))
    )
    session.flush()
    session.add(BrowserSession(id=chat_id, owner_hash=owner_hash))
    session.commit()
    monkeypatch.setattr(outreach_tests, "BUYER", "browser:" + chat_id)

    case = _prepared_case(session, orchestrator)
    case.status = "outreach_approved"
    session.commit()
    settings.allow_outreach = enabled
    whatsapp.messages.clear()
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(container, "build_whatsapp_client", lambda configured: whatsapp)

    def disposable_session():
        yield session

    app.main.app.dependency_overrides[get_session] = disposable_session
    try:
        with TestClient(
            app.main.app, base_url="https://boli.test", headers={"Origin": "https://boli.test"}
        ) as api:
            api.cookies.set(OWNER_COOKIE, token)
            result = api.post(f"/api/cases/{case.id}/outreach")
        assert result.status_code == (200 if enabled else 409)
        if enabled:
            assert result.json()["sent"] == 2
        assert len(whatsapp.messages) == (2 if enabled else 0)
    finally:
        app.main.app.dependency_overrides.pop(get_session)


@pytest.mark.parametrize("enabled", [False, True])
def test_outreach_task_respects_switch_without_broker(
    engine, session, orchestrator, settings, enabled, monkeypatch
):
    import app.container as container
    import app.db as db
    import app.worker as worker

    case = _prepared_case(session, orchestrator)
    settings.allow_outreach = enabled
    base = FakeWhatsApp()
    hybrid = HybridWhatsAppClient(base, settings)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(worker, "settings", settings)
    monkeypatch.setattr(
        container, "build_webhook_processor", lambda: SimpleNamespace(whatsapp=hybrid)
    )
    result = worker.send_outreach.run(case.id)
    assert result["sent"] == (2 if enabled else 0)
    assert len(base.messages) == (3 if enabled else 0)  # vendors plus phone buyer summary


def test_disabled_vendor_ack_and_receipt_preserve_browser_notification(
    engine, session, orchestrator, settings, monkeypatch
):
    import app.db as db
    import tests.test_outreach_api as outreach_tests
    from app.models import Conversation

    owner_hash = owner_digest(secrets.token_urlsafe(32))
    chat_id = uuid4().hex
    session.add(
        BrowserOwner(token_hash=owner_hash, expires_at=datetime.now(UTC) + timedelta(days=1))
    )
    session.flush()
    session.add(BrowserSession(id=chat_id, owner_hash=owner_hash))
    session.commit()
    monkeypatch.setattr(outreach_tests, "BUYER", "browser:" + chat_id)

    case = _prepared_case(session, orchestrator)
    case.status = "collecting_responses"
    conversation = session.get(Conversation, case.conversation_id)
    assert conversation.whatsapp_user_id == "browser:" + chat_id
    session.commit()
    vendor = session.scalars(select(Vendor)).first()
    settings.allow_outreach = False

    class CapturingProvider(FakeWhatsApp):
        def mark_read(self, message_id):
            pytest.fail("Disabled provider attempted a read receipt")

    base = CapturingProvider()
    monkeypatch.setattr(db, "engine", engine)
    hybrid = HybridWhatsAppClient(base, settings)
    processor = WhatsAppWebhookProcessor(hybrid, FakeSarvam(), orchestrator)
    processor.process(
        session,
        [
            InboundWhatsAppMessage(
                message_id="synthetic-vendor-reply",
                sender=normalize_phone(vendor.phone),
                message_type="text",
                text="Rs 5000, delivery 3 days",
            )
        ],
    )
    session.expire_all()
    assert session.scalars(select(VendorResponse)).first().status == "responded"
    assert base.messages == []
    assert "replied" in session.scalars(select(ChatMessage.body)).one().lower()
