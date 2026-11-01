import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.api.webhooks as webhooks
from app.config import Settings
from app.models import Base, ProcurementCase, Vendor, VendorResponse
from app.services.orchestrator import ProcurementOrchestrator
from app.services.webhook_processor import WhatsAppWebhookProcessor
from tests.conftest import FakeSarvam, FakeSearchProvider, FakeWhatsApp
from tests.test_twilio import _sign

BUYER = "919999999999"
TWILIO_FORM = {
    "MessageSid": "synthetic-inbound",
    "From": f"whatsapp:+{BUYER}",
    "Body": "Find pest control vendors in Jaipur",
    "NumMedia": "0",
}
META_BODY = json.dumps({
    "entry": [{"changes": [{"value": {"messages": [{
        "id": "synthetic-meta",
        "from": BUYER,
        "type": "text",
        "text": {"body": "Find cleaning vendors in Delhi"},
    }]}}]}],
}).encode()


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app.db as db_module
    import app.main

    engine = create_engine(
        f"sqlite:///{tmp_path / 'callbacks.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    configured = Settings(
        app_base_url="https://boli.test",
        twilio_auth_token="synthetic-twilio-signing-key",
        whatsapp_app_secret="synthetic-meta-signing-key",
        whatsapp_verify_token="synthetic-verification-token",
        allow_outreach=True,
    )
    whatsapp = FakeWhatsApp()
    sarvam = FakeSarvam()
    orchestrator = ProcurementOrchestrator(configured, whatsapp, sarvam, FakeSearchProvider())
    processor = WhatsAppWebhookProcessor(whatsapp, sarvam, orchestrator)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(webhooks, "engine", engine)
    monkeypatch.setattr(webhooks, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(webhooks, "settings", configured)
    monkeypatch.setattr(webhooks, "build_webhook_processor", lambda *args: processor)
    with TestClient(app.main.app) as api:
        yield api, engine
    engine.dispose()


def twilio_headers(path, form):
    return {"x-twilio-signature": _sign(
        webhooks.settings.app_base_url + path, form, webhooks.settings.twilio_auth_token
    )}


def meta_headers(body):
    digest = hmac.new(
        webhooks.settings.whatsapp_app_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return {"x-hub-signature-256": f"sha256={digest}"}


def test_twilio_webhook_creates_case(client):
    api, engine = client
    path = "/webhooks/twilio/whatsapp"
    response = api.post(path, data=TWILIO_FORM, headers=twilio_headers(path, TWILIO_FORM))
    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    with Session(engine) as session:
        assert session.scalars(select(ProcurementCase)).one().status == "shortlist_ready"


def test_meta_webhook_creates_case(client):
    api, engine = client
    response = api.post("/webhooks/whatsapp", content=META_BODY, headers=meta_headers(META_BODY))
    assert response.status_code == 200
    with Session(engine) as session:
        case = session.scalars(select(ProcurementCase)).one()
        assert case.status == "shortlist_ready"
        assert "cleaning" in case.raw_request.lower()


@pytest.mark.parametrize("inline", [True, False])
@pytest.mark.parametrize("provider", ["twilio", "meta"])
@pytest.mark.parametrize(
    "failure", ["missing-key", "missing-signature", "bad-signature", "tampered"]
)
def test_unauthenticated_inbound_never_dispatches(client, monkeypatch, inline, provider, failure):
    api, engine = client
    webhooks.settings.process_inline = inline

    def forbidden(*args, **kwargs):
        pytest.fail("Unauthenticated callback dispatched processing")

    monkeypatch.setattr(webhooks, "_process_messages", forbidden)
    if provider == "twilio":
        path = "/webhooks/twilio/whatsapp"
        headers = twilio_headers(path, TWILIO_FORM)
        form = dict(TWILIO_FORM)
        if failure == "missing-key":
            webhooks.settings.twilio_auth_token = ""
        elif failure == "missing-signature":
            headers = {}
        elif failure == "bad-signature":
            headers = {"x-twilio-signature": "invalid"}
        else:
            form["Body"] = "tampered"
        response = api.post(path, data=form, headers=headers)
    else:
        headers = meta_headers(META_BODY)
        body = META_BODY
        if failure == "missing-key":
            webhooks.settings.whatsapp_app_secret = ""
        elif failure == "missing-signature":
            headers = {}
        elif failure == "bad-signature":
            headers = {"x-hub-signature-256": "sha256=invalid"}
        else:
            body += b" "
        response = api.post("/webhooks/whatsapp", content=body, headers=headers)
    assert response.status_code == 401
    with Session(engine) as session:
        assert session.scalars(select(ProcurementCase)).all() == []


@pytest.mark.parametrize("provider", ["twilio", "meta"])
def test_configured_signed_callback_can_dispatch_to_worker(client, monkeypatch, provider):
    import app.worker as worker

    api, _ = client
    webhooks.settings.process_inline = False
    queued = []
    monkeypatch.setattr(worker.process_inbound, "delay", queued.append)
    if provider == "twilio":
        path = "/webhooks/twilio/whatsapp"
        response = api.post(path, data=TWILIO_FORM, headers=twilio_headers(path, TWILIO_FORM))
    else:
        response = api.post(
            "/webhooks/whatsapp", content=META_BODY, headers=meta_headers(META_BODY)
        )
    assert response.status_code == 200
    assert len(queued) == 1
    assert queued[0][0]["sender"] == BUYER


@pytest.mark.parametrize(
    "failure", ["missing-key", "missing-signature", "bad-signature", "wrong-url"]
)
def test_status_callback_rejects_before_database_access(client, monkeypatch, failure):
    api, _ = client
    path = "/webhooks/twilio/status"
    form = {"MessageSid": "synthetic-status", "MessageStatus": "delivered", "To": BUYER}
    headers = twilio_headers(path, form)
    if failure == "missing-key":
        webhooks.settings.twilio_auth_token = ""
    elif failure == "missing-signature":
        headers = {}
    elif failure == "bad-signature":
        headers = {"x-twilio-signature": "invalid"}
    else:
        headers = twilio_headers("/webhooks/twilio/whatsapp", form)

    def forbidden():
        pytest.fail("Unauthenticated status callback accessed delivery state")

    monkeypatch.setattr(webhooks, "SessionLocal", forbidden)
    assert api.post(path, data=form, headers=headers).status_code == 401


def test_signed_status_callback_updates_delivery_state(client):
    from tests.test_outreach_api import _prepared_case

    api, engine = client
    processor = webhooks.build_webhook_processor()
    with Session(engine) as session:
        case = _prepared_case(session, processor.orchestrator)
        case.status = "collecting_responses"
        session.commit()
        response = session.scalars(select(VendorResponse)).first()
        response_id = response.id
        response.twilio_message_sid = "SM" + "1" * 32
        response.status = "sent"
        session.commit()
        vendor_phone = session.get(Vendor, response.vendor_id).phone

    path = "/webhooks/twilio/status"
    form = {
        "MessageSid": "SM" + "1" * 32, "MessageStatus": "delivered",
        "To": f"whatsapp:{vendor_phone}",
    }
    assert api.post(path, data=form, headers=twilio_headers(path, form)).status_code == 200
    with Session(engine) as session:
        response = session.get(VendorResponse, response_id)
        assert response.status == "delivered"
        assert response.delivered_at is not None


@pytest.mark.parametrize("configured, supplied, expected", [
    ("", "", 403),
    ("", "synthetic", 403),
    ("synthetic-verification-token", "incorrect", 403),
    ("synthetic-verification-token", "synthetic-verification-token", 200),
])
def test_meta_verification_requires_configured_token(client, configured, supplied, expected):
    api, _ = client
    webhooks.settings.whatsapp_verify_token = configured
    response = api.get("/webhooks/whatsapp", params={
        "hub.mode": "subscribe",
        "hub.verify_token": supplied,
        "hub.challenge": "synthetic-challenge",
    })
    assert response.status_code == expected
    if expected == 200:
        assert response.text == "synthetic-challenge"


def test_verification_token_has_no_source_fallback(monkeypatch):
    monkeypatch.delenv("WHATSAPP_VERIFY_TOKEN")
    assert Settings().whatsapp_verify_token == ""
