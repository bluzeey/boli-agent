from app.config import Settings, get_settings
from app.integrations.sarvam import SarvamClient
from app.integrations.whatsapp import WhatsAppClient
from app.search.factory import build_search_provider
from app.services.orchestrator import ProcurementOrchestrator
from app.services.webhook_processor import WhatsAppWebhookProcessor


def build_webhook_processor(settings: Settings | None = None) -> WhatsAppWebhookProcessor:
    settings = settings or get_settings()
    whatsapp = WhatsAppClient(settings)
    sarvam = SarvamClient(settings)
    search_provider = build_search_provider(settings)
    orchestrator = ProcurementOrchestrator(settings, whatsapp, sarvam, search_provider)
    return WhatsAppWebhookProcessor(whatsapp, sarvam, orchestrator)
