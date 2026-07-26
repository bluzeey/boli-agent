import os

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Base, ChatMessage, Conversation, ProcurementCase


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

    from tests.conftest import FakeSearchProvider

    whatsapp = CapturingWhatsApp()
    from app.integrations.hybrid_whatsapp import HybridWhatsAppClient
    hybrid = HybridWhatsAppClient(whatsapp)

    sarvam = FakeSarvam()
    search = FakeSearchProvider()
    orchestrator = ProcurementOrchestrator(settings, hybrid, sarvam, search)
    processor = WhatsAppWebhookProcessor(hybrid, sarvam, orchestrator)
    processor._test_engine = engine
    return processor


def test_chat_page_served(client):
    api, _ = client
    r = api.get("/chat")
    assert r.status_code == 200
    assert "<html" in r.text.lower()


def test_create_new_session(client):
    api, _ = client
    r = api.post("/api/browser-chat/sessions")
    assert r.status_code == 200
    data = r.json()
    assert "session_id" in data
    assert data["messages"] == []
    assert data["active_case_id"] is None


def test_send_message_with_session_id(client):
    api, engine = client
    r = api.post("/api/browser-chat/sessions")
    session_id = r.json()["session_id"]

    r = api.post(
        "/api/browser-chat/messages",
        json={
            "text": "Find pest control vendors in Jaipur",
            "session_id": session_id,
            "client_message_id": "t1",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["session_id"] == session_id
    assert data["active_case_id"] is not None
    assert data["case_status"] is not None

    messages = data["messages"]
    assert len(messages) >= 2
    assert messages[0]["direction"] == "inbound"
    outbound = [m for m in messages if m["direction"] == "outbound"]
    assert len(outbound) >= 1


def test_multiple_sessions_are_isolated(client):
    api, engine = client

    r1 = api.post("/api/browser-chat/sessions")
    sid1 = r1.json()["session_id"]
    r2 = api.post("/api/browser-chat/sessions")
    sid2 = r2.json()["session_id"]

    assert sid1 != sid2

    api.post(
        "/api/browser-chat/messages",
        json={
            "text": "Find catering in Bangalore",
            "session_id": sid1,
            "client_message_id": "s1-1",
        },
    )
    api.post(
        "/api/browser-chat/messages",
        json={
            "text": "Find boxes in Delhi",
            "session_id": sid2,
            "client_message_id": "s2-1",
        },
    )

    r1 = api.get(f"/api/browser-chat/messages?session_id={sid1}")
    r2 = api.get(f"/api/browser-chat/messages?session_id={sid2}")

    msgs1 = r1.json()["messages"]
    msgs2 = r2.json()["messages"]

    assert any("catering" in m["body"].lower() or "bangalore" in m["body"].lower() for m in msgs1)
    assert not any("delhi" in m["body"].lower() for m in msgs1)

    assert any("boxes" in m["body"].lower() or "delhi" in m["body"].lower() for m in msgs2)
    assert not any("catering" in m["body"].lower() for m in msgs2)

    assert r1.json()["active_case_id"] != r2.json()["active_case_id"]


def test_delete_session_removes_all_data(client):
    api, engine = client

    r = api.post("/api/browser-chat/sessions")
    sid = r.json()["session_id"]

    api.post(
        "/api/browser-chat/messages",
        json={"text": "Find pest control in Jaipur", "session_id": sid, "client_message_id": "d1"},
    )

    with Session(engine) as s:
        convs = s.scalars(select(Conversation).where(
            Conversation.whatsapp_user_id == f"browser:{sid}"
        )).all()
        assert len(convs) == 1
        cases = s.scalars(select(ProcurementCase).where(
            ProcurementCase.conversation_id == convs[0].id
        )).all()
        assert len(cases) >= 1
        msgs = s.scalars(select(ChatMessage).where(
            ChatMessage.conversation_id == convs[0].id
        )).all()
        assert len(msgs) >= 2

    r = api.delete(f"/api/browser-chat/sessions/{sid}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    with Session(engine) as s:
        convs = s.scalars(select(Conversation).where(
            Conversation.whatsapp_user_id == f"browser:{sid}"
        )).all()
        assert len(convs) == 0
        msgs = s.scalars(select(ChatMessage).where(
            ChatMessage.sender == f"browser:{sid}"
        )).all()
        assert len(msgs) == 0


def test_delete_does_not_affect_other_sessions(client):
    api, engine = client

    r1 = api.post("/api/browser-chat/sessions")
    sid1 = r1.json()["session_id"]
    r2 = api.post("/api/browser-chat/sessions")
    sid2 = r2.json()["session_id"]

    api.post(
        "/api/browser-chat/messages",
        json={"text": "Find cleaning in Mumbai", "session_id": sid1, "client_message_id": "k1"},
    )
    api.post(
        "/api/browser-chat/messages",
        json={"text": "Find packaging in Pune", "session_id": sid2, "client_message_id": "k2"},
    )

    api.delete(f"/api/browser-chat/sessions/{sid1}")

    r2 = api.get(f"/api/browser-chat/messages?session_id={sid2}")
    assert r2.status_code == 200
    msgs = r2.json()["messages"]
    assert len(msgs) >= 2
    assert any("pune" in m["body"].lower() or "packaging" in m["body"].lower() for m in msgs)


def test_delete_nonexistent_session_404(client):
    api, _ = client
    r = api.delete("/api/browser-chat/sessions/nonexistent-id")
    assert r.status_code == 404


def test_idempotent_send(client):
    api, _ = client
    r = api.post("/api/browser-chat/sessions")
    sid = r.json()["session_id"]

    payload = {
        "text": "Find cleaning vendors in Delhi",
        "session_id": sid,
        "client_message_id": "dup-1",
    }
    r1 = api.post("/api/browser-chat/messages", json=payload)
    assert r1.status_code == 200
    r2 = api.post("/api/browser-chat/messages", json=payload)
    assert r2.status_code == 200

    messages = r2.json()["messages"]
    inbound = [m for m in messages if m["direction"] == "inbound"]
    assert len(inbound) == 1


def test_poll_messages(client):
    api, _ = client
    r = api.post("/api/browser-chat/sessions")
    sid = r.json()["session_id"]
    api.post(
        "/api/browser-chat/messages",
        json={
            "text": "Find packaging vendors in Mumbai",
            "session_id": sid,
            "client_message_id": "p1",
        },
    )

    r = api.get(f"/api/browser-chat/messages?session_id={sid}")
    assert r.status_code == 200
    data = r.json()
    assert len(data["messages"]) >= 2


def test_case_persisted_in_db(client):
    api, engine = client
    r = api.post("/api/browser-chat/sessions")
    sid = r.json()["session_id"]
    api.post(
        "/api/browser-chat/messages",
        json={
            "text": "Find catering vendors in Bangalore",
            "session_id": sid,
            "client_message_id": "c1",
        },
    )

    with Session(engine) as s:
        case = s.scalars(
            select(ProcurementCase).where(ProcurementCase.status == "shortlist_ready")
        ).first()
        assert case is not None
        assert "catering" in (case.raw_request or "").lower()
