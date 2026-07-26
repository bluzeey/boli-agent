from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_base_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./boli.db"
    redis_url: str = "redis://localhost:6379/0"
    process_inline: bool = True
    log_level: str = "INFO"

    whatsapp_verify_token: str = "change-me"
    whatsapp_access_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_graph_version: str = ""

    sarvam_api_key: str = ""
    sarvam_chat_model: str = "sarvam-105b"
    sarvam_stt_model: str = "saaras:v3"
    sarvam_stt_mode: str = "codemix"

    search_provider: str = "mock"
    google_places_api_key: str = ""
    search_result_limit: int = 5

    max_audio_bytes: int = 12_000_000
    max_message_chars: int = 4_000
    google_result_cache_minutes: int = 30

    # Controlled vendor outreach
    allow_outreach: bool = True
    outbound_rate_delay_seconds: float = 2.0
    max_outreach_per_batch: int = 20
    outreach_channel: str = "whatsapp"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def whatsapp_graph_base_url(self) -> str:
        root = "https://graph.facebook.com"
        return f"{root}/{self.whatsapp_graph_version}" if self.whatsapp_graph_version else root


@lru_cache
def get_settings() -> Settings:
    return Settings()
