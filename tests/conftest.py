import os
import socket

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

# Set disposable configuration before importing any application module.
os.environ.update({
    "APP_ENV": "test",
    "APP_BASE_URL": "https://boli.test",
    "DATABASE_URL": "sqlite://",
    "REDIS_URL": "redis://127.0.0.1:1/15",
    "PROCESS_INLINE": "true",
    "ALLOW_OUTREACH": "false",
    "LOG_LEVEL": "WARNING",
    "PORT": "8000",
    "WHATSAPP_PROVIDER": "twilio",
    "WHATSAPP_VERIFY_TOKEN": "",
    "WHATSAPP_ACCESS_TOKEN": "",
    "WHATSAPP_APP_SECRET": "",
    "WHATSAPP_PHONE_NUMBER_ID": "",
    "WHATSAPP_GRAPH_VERSION": "",
    "TWILIO_ACCOUNT_SID": "",
    "TWILIO_AUTH_TOKEN": "",
    "TWILIO_WHATSAPP_FROM": "",
    "SARVAM_API_KEY": "",
    "SARVAM_CHAT_MODEL": "synthetic",
    "SARVAM_STT_MODEL": "synthetic",
    "SARVAM_STT_MODE": "codemix",
    "SEARCH_PROVIDER": "mock",
    "SEARCH_RESULT_LIMIT": "5",
    "MAX_AUDIO_BYTES": "12000000",
    "MAX_MESSAGE_CHARS": "4000",
    "GOOGLE_PLACES_API_KEY": "",
    "GOOGLE_RESULT_CACHE_MINUTES": "30",
    "OUTBOUND_RATE_DELAY_SECONDS": "0",
    "MAX_OUTREACH_PER_BATCH": "20",
    "OUTREACH_CHANNEL": "whatsapp",
})

from app.config import Settings  # noqa: E402

# No test Settings() may load a checkout's .env file.
Settings.model_config["env_file"] = None

from app.integrations.sarvam import heuristic_extract_requirement  # noqa: E402
from app.models import Base  # noqa: E402
from app.schemas import SearchResult  # noqa: E402
from app.services.orchestrator import ProcurementOrchestrator  # noqa: E402


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("External network is forbidden in offline tests")

    async def async_blocked(*args, **kwargs):
        blocked()

    # In-process ASGI TestClient and explicit httpx.MockTransport remain usable.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


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
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine, expire_on_commit=False) as s:
        yield s


@pytest.fixture
def settings():
    return Settings(
        allow_outreach=True,
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
