from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.browser_access import OWNER_COOKIE, owner_digest
from app.models import (
    Base,
    BrowserOwner,
    ChatMessage,
    Conversation,
    ProcurementCase,
)
from tests.test_browser_chat import _build_test_processor


@pytest.fixture
def browsers(monkeypatch, tmp_path):
    import app.api.browser_chat as browser
    import app.db as db
    import app.main

    engine = create_engine(
        f"sqlite:///{tmp_path / 'private.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(browser, "build_webhook_processor", lambda: _build_test_processor(engine))

    def disposable_session():
        with Session(engine) as session:
            yield session

    app.main.app.dependency_overrides[db.get_session] = disposable_session
    with ExitStack() as stack:
        clients = [
            stack.enter_context(
                TestClient(
                    app.main.app,
                    base_url="https://boli.test",
                    headers={"Origin": "https://boli.test"},
                )
            )
            for _ in range(2)
        ]
        yield (*clients, engine)
    app.main.app.dependency_overrides.pop(db.get_session)
    Base.metadata.drop_all(engine)
    engine.dispose()


def chat(client):
    response = client.post("/api/browser-chat/sessions")
    assert response.status_code == 200
    return response.json()["session_id"]


def message(client, sid, text="Find boxes in Jaipur"):
    return client.post("/api/browser-chat/messages", json={"session_id": sid, "text": text})


def test_cookie_is_opaque_secure_and_not_returned_as_a_manual_key(browsers):
    alice, _, engine = browsers
    response = alice.post("/api/browser-chat/sessions")
    header = next(
        h for h in response.headers.get_list("set-cookie") if h.startswith(OWNER_COOKIE + "=")
    )
    assert "Secure" in header and "HttpOnly" in header and "SameSite=lax" in header
    assert "Path=/" in header and "Domain=" not in header
    token = alice.cookies.get(OWNER_COOKIE)
    assert token and token not in response.text
    with Session(engine) as session:
        owner = session.get(BrowserOwner, owner_digest(token))
        assert owner is not None and owner.token_hash != token


def test_two_browsers_cannot_read_forge_or_delete_each_others_chats(browsers):
    alice, bob, engine = browsers
    a, b = chat(alice), chat(bob)
    assert message(alice, a, "Find synthetic boxes in Jaipur").status_code == 200
    assert message(bob, b, "Find synthetic catering in Mumbai").status_code == 200
    for client, own, other in [(alice, a, b), (bob, b, a)]:
        assert client.get(f"/api/browser-chat/messages?session_id={own}").status_code == 200
        assert client.get(f"/api/browser-chat/messages?session_id={other}").status_code == 404
        assert message(client, other).status_code == 404
        assert client.delete(f"/api/browser-chat/sessions/{other}").status_code == 404
        listed = client.get("/api/browser-chat/sessions").json()["sessions"]
        assert [s["session_id"] for s in listed] == [own]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Conversation)) == 2


@pytest.mark.parametrize(
    "suffix", ["", "/candidates", "/rfq", "/vendors", "/responses", "/document"]
)
def test_every_case_read_requires_its_browser_owner(browsers, suffix):
    alice, bob, _ = browsers
    a, _ = chat(alice), chat(bob)
    case_id = message(alice, a).json()["active_case_id"]
    assert bob.get(f"/api/cases/{case_id}{suffix}").status_code == 404
    assert alice.get(f"/api/cases/{case_id}").status_code == 200


@pytest.mark.parametrize(
    "suffix,payload",
    [
        ("/shortlist", {"selection": [1]}),
        ("/rfq", None),
        ("/rfq/approve", None),
        ("/outreach", None),
        ("/vendors/synthetic-vendor/consent", {"consent": True}),
    ],
)
def test_every_case_mutation_rejects_other_browser_before_work(browsers, suffix, payload):
    alice, bob, _ = browsers
    a, _ = chat(alice), chat(bob)
    case_id = message(alice, a).json()["active_case_id"]
    assert bob.post(f"/api/cases/{case_id}{suffix}", json=payload).status_code == 404


def test_legacy_chat_ids_are_never_claimed_by_new_visitors(browsers):
    alice, _, engine = browsers
    legacy_sid = uuid4().hex
    with Session(engine) as session:
        conversation = Conversation(whatsapp_user_id="browser:" + legacy_sid)
        session.add(conversation)
        session.flush()
        case = ProcurementCase(
            conversation_id=conversation.id, raw_request="legacy private fixture"
        )
        session.add(case)
        session.commit()
        case_id = case.id
    chat(alice)
    assert alice.get(f"/api/browser-chat/messages?session_id={legacy_sid}").status_code == 404
    assert message(alice, legacy_sid).status_code == 404
    assert alice.get(f"/api/cases/{case_id}").status_code == 404


def test_expired_or_cleared_cookie_cannot_recover_records_by_id(browsers):
    alice, _, engine = browsers
    sid = chat(alice)
    message(alice, sid)
    token = alice.cookies.get(OWNER_COOKIE)
    with Session(engine) as session:
        owner = session.get(BrowserOwner, owner_digest(token))
        owner.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    assert alice.get(f"/api/browser-chat/messages?session_id={sid}").status_code == 401
    assert alice.get("/api/browser-chat/sessions").json() == {"sessions": []}
    new_sid = chat(alice)
    assert new_sid != sid and alice.cookies.get(OWNER_COOKIE) != token
    assert alice.get(f"/api/browser-chat/messages?session_id={sid}").status_code == 404


def test_mutations_require_exact_origin_and_private_responses_are_not_cached(browsers):
    alice, _, _ = browsers
    sid = chat(alice)
    wrong = alice.post(
        "/api/browser-chat/messages",
        json={"session_id": sid, "text": "synthetic"},
        headers={"Origin": "https://untrusted.test"},
    )
    assert wrong.status_code == 403 and wrong.headers["cache-control"] == "no-store"
    request = alice.build_request("POST", "/api/browser-chat/sessions")
    del request.headers["origin"]
    assert alice.send(request).status_code == 403
    assert (
        alice.get(f"/api/browser-chat/messages?session_id={sid}").headers["cache-control"]
        == "no-store"
    )


def test_provider_messages_are_throttled_before_processor_creation(browsers, monkeypatch):
    import app.api.browser_chat as browser

    alice, _, engine = browsers
    sid = chat(alice)
    calls = []

    def fail():
        calls.append(1)
        raise AssertionError("No real provider in this test")

    # Put failure inside the processor's method so it remains a controlled transcript error.
    processor = _build_test_processor(engine)
    monkeypatch.setattr(processor.orchestrator, "handle_text", lambda *args: fail())
    monkeypatch.setattr(browser, "build_webhook_processor", lambda: processor)
    for _ in range(10):
        assert message(alice, sid, "synthetic").status_code == 200
    limited = message(alice, sid, "synthetic")
    assert limited.status_code == 429 and limited.headers["retry-after"] == "60"
    assert len(calls) == 10


def test_redis_outage_blocks_provider_work_without_writing_messages(browsers, monkeypatch):
    import app.browser_access as access

    alice, _, engine = browsers
    sid = chat(alice)

    def unavailable():
        raise ConnectionError("synthetic outage")

    monkeypatch.setattr(access, "get_rate_store", unavailable)
    assert message(alice, sid).status_code == 503
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ChatMessage)) == 0


def test_vendor_consent_cannot_be_changed_through_someone_elses_vendor_id(browsers):
    from app.models import Vendor, VendorResponse
    from app.services.outreach import prepare_outreach
    from app.services.rfq import latest_rfq

    alice, bob, engine = browsers
    a, b = chat(alice), chat(bob)
    cases = []
    for client, sid in [(alice, a), (bob, b)]:
        case_id = message(client, sid).json()["active_case_id"]
        message(client, sid, "1")
        message(client, sid, "yes")
        with Session(engine) as session:
            case = session.get(ProcurementCase, case_id)
            prepare_outreach(session, case, latest_rfq(session, case_id))
            session.commit()
            vendor_id = session.scalar(
                select(VendorResponse.vendor_id).where(VendorResponse.case_id == case_id)
            )
            cases.append((case_id, vendor_id))
    (a_case, a_vendor), (b_case, b_vendor) = cases
    assert a_vendor != b_vendor
    assert (
        alice.post(
            f"/api/cases/{a_case}/vendors/{a_vendor}/consent", json={"consent": False}
        ).status_code
        == 200
    )
    assert (
        bob.post(
            f"/api/cases/{b_case}/vendors/{a_vendor}/consent", json={"consent": True}
        ).status_code
        == 404
    )
    with Session(engine) as session:
        assert session.get(Vendor, a_vendor).contact_consent is False
        assert session.get(Vendor, b_vendor).contact_consent is True


def test_disabled_outreach_keeps_rfq_saved_and_never_queues_work(browsers, monkeypatch):
    import app.worker as worker
    from app.models import Rfq, VendorResponse

    alice, _, engine = browsers
    sid = chat(alice)
    case_id = message(alice, sid).json()["active_case_id"]
    message(alice, sid, "1")
    message(alice, sid, "yes")
    monkeypatch.setattr(
        worker.send_outreach, "delay", lambda *args: pytest.fail("Disabled outreach queued work")
    )
    response = message(alice, sid, "yes")
    assert response.json()["case_status"] == "rfq_ready"
    assert "disabled" in response.json()["messages"][-1]["body"]
    assert alice.post(f"/api/cases/{case_id}/rfq/approve").status_code == 409
    assert alice.post(f"/api/cases/{case_id}/outreach").status_code == 409
    with Session(engine) as session:
        assert session.scalar(select(Rfq.status).where(Rfq.case_id == case_id)) == "shown"
        assert session.scalar(select(func.count()).select_from(VendorResponse)) == 0
