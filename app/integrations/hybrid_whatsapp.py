from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.db as db_module
from app.config import Settings, get_settings
from app.models import Conversation
from app.services.chat_transcript import is_browser_sender, record_outbound_message


class HybridWhatsAppClient:
    def __init__(self, base_client: Any, settings: Settings | None = None) -> None:
        self.base_client = base_client
        self.settings = settings or get_settings()

    def send_text(self, to: str, body: str) -> dict[str, Any]:
        if not is_browser_sender(to):
            if not self.settings.allow_outreach:
                return {"blocked": True, "reason": "outreach_disabled"}
            return self.base_client.send_text(to, body)

        with Session(db_module.engine, expire_on_commit=False) as session:
            conversation = session.scalars(
                select(Conversation).where(Conversation.whatsapp_user_id == to)
            ).first()
            if not conversation:
                conversation = Conversation(whatsapp_user_id=to)
                session.add(conversation)
                session.flush()
            record_outbound_message(session, conversation.id, "assistant", body)
            session.commit()
        return {"browser": True, "to": to, "body": body}

    def mark_read(self, message_id: str) -> None:
        if not self.settings.allow_outreach:
            return
        return self.base_client.mark_read(message_id)

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        return self.base_client.download_media(media_id)
