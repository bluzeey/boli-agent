import os

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Base, ChatMessage, ProcurementCase


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import app.api.browser_chat as browser_chat
    import app.db as db_module
    import app.main

    test_engine = create_engine(
        "sqlite:///./test_boli_chat.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(
        browser_chat.db_module, "engine", test_engine
    )
    monkeypatch.setattr(
        browser_chat,
        "build_webhook_processor",
        lambda: _build_test_processor(test_engine),
    )
    with TestClient(app.main.app) as c:
        yield c, test_engine
    Base.metadata.drop_all(test_engine)
    if os.path.exists("./test_boli_chat.db"):
        os.remove("./test_boli_chat.db")


def _build_test_processor(engine):
    from app.search.mock import MockSearchProvider
    from app.services.orchestrator import ProcurementOrchestrator
    from app.services.webhook_processor import WhatsAppWebhookProcessor

    settings = Settings(
        whatsapp_provider="twilio",
        search_provider="mock",
        search_result_limit=5,
        outbound_rate_delay_seconds=0.0,
        process_inline=True,
    )

    class CapturingWhatsApp:
        """Captures outbound messages so the hybrid client persists them."""

        def __init__(self):
            self.messages: list[tuple[str, str]] = []

        def send_text(self, to: str, body: str) -> dict:
            self.messages.append((to, body))
            return {"dry_run": True, "to": to, "body": body}

        def mark_read(self, message_id: str) -> None:
            return None

        def download_media(self, media_id: str) -> tuple[bytes, str]:
            raise RuntimeError("not supported")

    class FakeSarvam:
        def extract_requirement(self, text, existing_case=None):
            from app.integrations.sarvam import heuristic_extract_requirement
            return heuristic_extract_requirement(text, existing_case)

        def extract_quote(self, reply_text, required_fields):
            from app.integrations.sarvam import heuristic_extract_quote
            return heuristic_extract_quote(reply_text, required_fields)

        def transcribe_audio(self, audio, mime_type):
            raise RuntimeError("not supported")

    whatsapp = CapturingWhatsApp()
    from app.integrations.hybrid_whatsapp import HybridWhatsAppClient
    hybrid = HybridWhatsAppClient(whatsapp)

    sarvam = FakeSarvam()
    search = MockSearchProvider()
    orchestrator = ProcurementOrchestrator(settings, hybrid, sarvam, search)
    processor = WhatsAppWebhookProcessor(hybrid, sarvam, orchestrator)
    processor._test_engine = engine
    return processor


def test_chat_page_served(client):
    api, _ = client
    r = api.get("/chat")
    assert r.status_code == 200
    assert "<html" in r.text.lower()


def test_session_creation(client):
    api, _ = client
    r = api.post("/api/browser-chat/session")
    assert r.status_code == 200
    data = r.json()
    assert "session_id" in data
    assert data["messages"] == []
    assert data["active_case_id"] is None


def test_send_message_creates_case(client):
    api, engine = client
    api.post("/api/browser-chat/session")

    r = api.post(
        "/api/browser-chat/messages",
        json={"text": "Find pest control vendors in Jaipur", "client_message_id": "t1"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["active_case_id"] is not None
    assert data["case_status"] is not None

    messages = data["messages"]
    assert len(messages) >= 2
    assert messages[0]["direction"] == "inbound"
    assert messages[0]["body"] == "Find pest control vendors in Jaipur"

    outbound = [m for m in messages if m["direction"] == "outbound"]
    assert len(outbound) >= 1
    assert any(
        "pest control" in m["body"].lower() or "vendor" in m["body"].lower()
        for m in outbound
    )


def test_idempotent_send(client):
    api, _ = client
    api.post("/api/browser-chat/session")

    payload = {"text": "Find cleaning vendors in Delhi", "client_message_id": "dup-1"}
    r1 = api.post("/api/browser-chat/messages", json=payload)
    assert r1.status_code == 200
    r2 = api.post("/api/browser-chat/messages", json=payload)
    assert r2.status_code == 200

    messages = r2.json()["messages"]
    inbound = [m for m in messages if m["direction"] == "inbound"]
    assert len(inbound) == 1


def test_poll_messages(client):
    api, _ = client
    api.post("/api/browser-chat/session")
    api.post(
        "/api/browser-chat/messages",
        json={"text": "Find packaging vendors in Mumbai", "client_message_id": "p1"},
    )

    r = api.get("/api/browser-chat/messages")
    assert r.status_code == 200
    data = r.json()
    assert len(data["messages"]) >= 2


def test_case_persisted_in_db(client):
    api, engine = client
    api.post("/api/browser-chat/session")
    api.post(
        "/api/browser-chat/messages",
        json={"text": "Find catering vendors in Bangalore", "client_message_id": "c1"},
    )

    with Session(engine) as s:
        case = s.scalars(
            select(ProcurementCase).where(ProcurementCase.status == "shortlist_ready")
        ).first()
        assert case is not None
        assert "catering" in (case.raw_request or "").lower()

        msgs = s.scalars(select(ChatMessage).order_by(ChatMessage.created_at.asc())).all()
        assert len(msgs) >= 2
        assert msgs[0].direction == "inbound"
