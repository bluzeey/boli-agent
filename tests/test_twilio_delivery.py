from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import VendorResponse
from app.services.outreach import send_outreach
from tests.conftest import FakeWhatsApp
from tests.test_outreach_api import _prepared_case
from tests.test_webhook_routes import client as client  # noqa: F401
from tests.test_webhook_routes import twilio_headers

PATH = "/webhooks/twilio/status"
FIRST_SID = "SM" + "1" * 32
SECOND_SID = "SM" + "2" * 32
BASE_TIME = datetime(2020, 1, 1, tzinfo=UTC)


def callback(api, sid, status):
    form = {
        "MessageSid": sid, "MessageStatus": status,
        "To": "whatsapp:+919000000000",
        "ErrorMessage": "synthetic delivery failure",
    }
    return api.post(PATH, data=form, headers=twilio_headers(PATH, form))


def prepared_response(session, orchestrator, sid=FIRST_SID):
    case = _prepared_case(session, orchestrator)
    case.status = "collecting_responses"
    response = session.scalars(
        select(VendorResponse).where(VendorResponse.case_id == case.id)
    ).first()
    response.twilio_message_sid = sid
    response.status = "sent"
    response.sent_at = BASE_TIME
    response.updated_at = BASE_TIME
    session.commit()
    return case, response


def snapshot(engine, response_id):
    with Session(engine) as session:
        response = session.get(VendorResponse, response_id)
        return {
            name: getattr(response, name)
            for name in (
                "status", "sent_at", "delivered_at", "responded_at", "updated_at", "last_error",
            )
        }


def test_callback_targets_exact_message_even_on_older_closed_case(client):
    import app.api.webhooks as webhooks

    api, engine = client
    orchestrator = webhooks.build_webhook_processor().orchestrator
    with Session(engine) as session:
        old_case, old_response = prepared_response(session, orchestrator)
        old_case.status = "closed"
        session.commit()
        _, newer_response = prepared_response(session, orchestrator, SECOND_SID)
        assert old_response.vendor_id == newer_response.vendor_id
        old_id, newer_id = old_response.id, newer_response.id
    newer_before = snapshot(engine, newer_id)
    assert callback(api, FIRST_SID, "delivered").status_code == 200
    assert snapshot(engine, old_id)["status"] == "delivered"
    assert snapshot(engine, newer_id) == newer_before


@pytest.mark.parametrize("sid", ["", "SM" + "9" * 32])
def test_missing_or_unstored_sid_never_falls_back_to_recipient_or_reply_id(client, sid):
    import app.api.webhooks as webhooks

    api, engine = client
    with Session(engine) as session:
        _, response = prepared_response(
            session, webhooks.build_webhook_processor().orchestrator, None
        )
        response.reply_message_id = sid
        session.commit()
        response_id = response.id
    before = snapshot(engine, response_id)
    assert callback(api, sid, "delivered").status_code == 200
    assert snapshot(engine, response_id) == before


def test_delivered_duplicate_and_out_of_order_callbacks_are_noops(client):
    import app.api.webhooks as webhooks

    api, engine = client
    with Session(engine) as session:
        _, response = prepared_response(session, webhooks.build_webhook_processor().orchestrator)
        response_id = response.id
    assert callback(api, FIRST_SID, "delivered").status_code == 200
    delivered = snapshot(engine, response_id)
    for status in ("sent", "undelivered", "failed", "delivered", "queued"):
        assert callback(api, FIRST_SID, status).status_code == 200
        assert snapshot(engine, response_id) == delivered


@pytest.mark.parametrize("state", ["responded", "declined", "expired", "skipped_cold", "queued"])
def test_delivery_callbacks_preserve_business_terminal_or_requeued_states(client, state):
    import app.api.webhooks as webhooks

    api, engine = client
    with Session(engine) as session:
        _, response = prepared_response(session, webhooks.build_webhook_processor().orchestrator)
        response.status = state
        if state == "responded":
            response.responded_at = BASE_TIME + timedelta(seconds=1)
        session.commit()
        response_id = response.id
    before = snapshot(engine, response_id)
    for status in ("sent", "failed", "delivered"):
        assert callback(api, FIRST_SID, status).status_code == 200
        assert snapshot(engine, response_id) == before


def test_response_timestamp_guard_blocks_stale_delivery_status(client):
    import app.api.webhooks as webhooks

    api, engine = client
    with Session(engine) as session:
        _, response = prepared_response(session, webhooks.build_webhook_processor().orchestrator)
        response.responded_at = BASE_TIME + timedelta(seconds=1)
        session.commit()
        response_id = response.id
    before = snapshot(engine, response_id)
    assert callback(api, FIRST_SID, "delivered").status_code == 200
    assert snapshot(engine, response_id) == before


def test_failure_does_not_regress_to_sent_but_can_advance_to_delivered(client):
    import app.api.webhooks as webhooks

    api, engine = client
    with Session(engine) as session:
        _, response = prepared_response(session, webhooks.build_webhook_processor().orchestrator)
        response_id = response.id
    assert callback(api, FIRST_SID, "undelivered").status_code == 200
    failed = snapshot(engine, response_id)
    assert failed["status"] == "failed"
    for status in ("sent", "failed", "undelivered"):
        assert callback(api, FIRST_SID, status).status_code == 200
        assert snapshot(engine, response_id) == failed
    assert callback(api, FIRST_SID, "delivered").status_code == 200
    delivered = snapshot(engine, response_id)
    assert delivered["status"] == "delivered"
    assert delivered["last_error"] is None


def test_send_persists_real_response_sid_and_retires_previous_attempt(client):
    import app.api.webhooks as webhooks

    api, engine = client
    processor = webhooks.build_webhook_processor()
    with Session(engine) as session:
        case, response = prepared_response(session, processor.orchestrator)
        response_id = response.id
        response.status = "queued"
        # Restrict this send to one recipient so each attempt has one SID.
        for other in session.scalars(select(VendorResponse)).all():
            if other.id != response_id:
                other.status = "skipped_cold"
        session.commit()

        class SyntheticTwilio(FakeWhatsApp):
            def send_text(self, to, body):
                # The old attempt must already be unlinked in the database before HTTP.
                with Session(engine) as observer:
                    assert observer.get(VendorResponse, response_id).twilio_message_sid is None
                return {"sid": SECOND_SID}

        summary = send_outreach(
            session, case.id, SyntheticTwilio(), processor.orchestrator.settings
        )
        assert summary.sent == 1
        assert session.get(VendorResponse, response_id).twilio_message_sid == SECOND_SID
    before = snapshot(engine, response_id)
    assert callback(api, FIRST_SID, "delivered").status_code == 200
    assert snapshot(engine, response_id) == before
    assert callback(api, SECOND_SID, "delivered").status_code == 200
    assert snapshot(engine, response_id)["status"] == "delivered"


@pytest.mark.parametrize("result", [
    {"ok": True},
    {"dry_run": True, "sid": FIRST_SID},
    {"blocked": True, "sid": FIRST_SID},
    {"sid": None},
    {"sid": ""},
])
def test_outreach_never_fabricates_or_retains_sid_without_success_result(
    session, orchestrator, settings, result
):
    case, response = prepared_response(session, orchestrator)
    response.status = "queued"
    session.commit()

    class SyntheticProvider(FakeWhatsApp):
        def send_text(self, to, body):
            return result

    send_outreach(session, case.id, SyntheticProvider(), settings)
    assert response.twilio_message_sid is None


def test_status_database_failure_is_not_silently_acknowledged(client, monkeypatch):
    import app.api.webhooks as webhooks

    api, _ = client

    class Unavailable:
        def begin(self):
            raise RuntimeError("synthetic database unavailable")

    monkeypatch.setattr(webhooks, "SessionLocal", Unavailable())
    assert callback(api, FIRST_SID, "delivered").status_code == 503
