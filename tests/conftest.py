import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.sarvam import heuristic_extract_requirement
from app.models import Base
from app.schemas import SearchResult
from app.services.orchestrator import ProcurementOrchestrator


class FakeWhatsApp:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def send_text(self, to: str, body: str) -> dict:
        self.messages.append((to, body))
        return {"ok": True}

    def mark_read(self, message_id: str) -> None:
        return None

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        raise RuntimeError("media download not supported in tests")


class FakeSarvam:
    def extract_requirement(self, text: str, existing_case: dict | None = None):
        return heuristic_extract_requirement(text, existing_case)

    def extract_quote(self, reply_text: str, required_fields: list[str]):
        from app.integrations.sarvam import heuristic_extract_quote

        return heuristic_extract_quote(reply_text, required_fields)

    def transcribe_audio(self, audio: bytes, mime_type: str) -> str:
        raise RuntimeError("transcription not supported in tests")


class FakeSearchProvider:
    name = "test"

    def search(self, query: str, limit: int) -> list[SearchResult]:
        return [
            SearchResult(
                external_id=f"test-{i}",
                name=f"Test Vendor {i + 1}",
                address="Test Address",
                phone=f"+91 90000 0000{i}",
                rating=round(4.0 + i * 0.1, 1),
                review_count=20 + i * 7,
                source_url=None,
                provider="test",
            )
            for i in range(min(limit, 5))
        ]


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine, expire_on_commit=False) as s:
        yield s


@pytest.fixture
def settings():
    return Settings(
        search_provider="mock",
        search_result_limit=5,
        outbound_rate_delay_seconds=0.0,
    )


@pytest.fixture
def whatsapp():
    return FakeWhatsApp()


@pytest.fixture
def orchestrator(settings, whatsapp):
    return ProcurementOrchestrator(
        settings,
        whatsapp,  # type: ignore[arg-type]
        FakeSarvam(),  # type: ignore[arg-type]
        FakeSearchProvider(),
    )


BUYER = "919999999999"
