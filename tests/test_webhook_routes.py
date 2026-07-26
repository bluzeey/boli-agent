import os

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Base, ProcurementCase

BUYER = "919999999999"


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import app.api.webhooks as webhooks
    import app.db as db_module
    import app.main
    import app.search.factory as search_factory
    from tests.conftest import FakeSearchProvider

    test_engine = create_engine(
        "sqlite:///./test_boli_routes.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(webhooks, "engine", test_engine)
    monkeypatch.setattr(
        search_factory,
        "build_search_provider",
        lambda settings: FakeSearchProvider(),
    )
    monkeypatch.setattr(
        webhooks,
        "settings",
        Settings(
            whatsapp_provider="twilio",
            search_provider="mock",
            search_result_limit=5,
            outbound_rate_delay_seconds=0.0,
            process_inline=True,
        ),
    )
    with TestClient(app.main.app) as c:
        yield c, test_engine
    Base.metadata.drop_all(test_engine)
    if os.path.exists("./test_boli_routes.db"):
        os.remove("./test_boli_routes.db")


def test_twilio_webhook_creates_case(client):
    api, engine = client
    r = api.post(
        "/webhooks/twilio/whatsapp",
        data={
            "MessageSid": "route-t1",
            "From": f"whatsapp:+{BUYER}",
            "Body": "Find pest control vendors in Jaipur",
            "NumMedia": "0",
        },
    )
    assert r.status_code == 200
    assert r.json() == {"status": "accepted"}
    with Session(engine) as s:
        case = s.scalars(
            select(ProcurementCase).where(ProcurementCase.status == "shortlist_ready")
        ).first()
        assert case is not None


def test_meta_webhook_creates_case(client):
    api, engine = client
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "route-m1",
                                    "from": BUYER,
                                    "type": "text",
                                    "text": {"body": "Find cleaning vendors in Delhi"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    r = api.post("/webhooks/whatsapp", json=payload)
    assert r.status_code == 200
    with Session(engine) as s:
        case = s.scalars(
            select(ProcurementCase).where(ProcurementCase.status == "shortlist_ready")
        ).first()
        assert case is not None
        assert "cleaning" in (case.raw_request or "").lower()
