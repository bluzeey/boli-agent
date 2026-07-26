from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.sarvam import heuristic_extract_requirement
from app.models import Base
from app.services.orchestrator import ProcurementOrchestrator
from tests.conftest import FakeSearchProvider


class FakeWhatsApp:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send_text(self, to: str, body: str) -> dict:
        self.messages.append((to, body))
        return {"ok": True}


class FakeSarvam:
    def extract_requirement(self, text: str, existing_case: dict | None = None):
        return heuristic_extract_requirement(text, existing_case)


def test_orchestrator_searches_and_replies() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    settings = Settings(
        search_provider="mock", search_result_limit=3, outbound_rate_delay_seconds=0.0
    )
    whatsapp = FakeWhatsApp()
    orchestrator = ProcurementOrchestrator(
        settings,
        whatsapp,  # type: ignore[arg-type]
        FakeSarvam(),  # type: ignore[arg-type]
        FakeSearchProvider(),
    )

    with Session(engine, expire_on_commit=False) as session:
        procurement_case = orchestrator.handle_text(
            session, "919999999999", "Find pest control vendors in Jaipur"
        )
        assert procurement_case.status == "shortlist_ready"

    assert "Vendor" in whatsapp.messages[-1][1] or "search results" in whatsapp.messages[-1][1]
