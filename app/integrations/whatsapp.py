import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class InboundWhatsAppMessage:
    message_id: str
    sender: str
    message_type: str
    text: str | None = None
    media_id: str | None = None
    mime_type: str | None = None


def normalize_phone(value: str | None) -> str:
    """Return digits-only phone for matching across providers.

    e.g. ``whatsapp:+91 90000 00000`` and ``9190000000000`` both normalize to
    ``9190000000000`` so an inbound sender can be matched to a stored Vendor.phone
    regardless of formatting.
    """
    if not value:
        return ""
    return "".join(ch for ch in str(value) if ch.isdigit())


def verify_whatsapp_signature(body: bytes, signature_header: str | None, app_secret: str) -> bool:
    if not app_secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    supplied = signature_header.removeprefix("sha256=")
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied, expected)


def extract_inbound_messages(payload: dict[str, Any]) -> list[InboundWhatsAppMessage]:
    output: list[InboundWhatsAppMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []) or []:
                message_type = message.get("type", "unknown")
                text = None
                media_id = None
                mime_type = None

                if message_type == "text":
                    text = (message.get("text") or {}).get("body")
                elif message_type in {"audio", "document", "image"}:
                    media = message.get(message_type) or {}
                    media_id = media.get("id")
                    mime_type = media.get("mime_type")
                elif message_type == "interactive":
                    interactive = message.get("interactive") or {}
                    selected = (
                        interactive.get("button_reply")
                        or interactive.get("list_reply")
                        or {}
                    )
                    text = selected.get("title") or selected.get("id")

                message_id = message.get("id")
                sender = message.get("from")
                if message_id and sender:
                    output.append(
                        InboundWhatsAppMessage(
                            message_id=message_id,
                            sender=sender,
                            message_type=message_type,
                            text=text,
                            media_id=media_id,
                            mime_type=mime_type,
                        )
                    )
    return output


class WhatsAppClient:
    def __init__(self, settings: Settings, http_client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.http = http_client or httpx.Client(timeout=30.0)

    def _messages_url(self) -> str:
        return (
            f"{self.settings.whatsapp_graph_base_url}/"
            f"{self.settings.whatsapp_phone_number_id}/messages"
        )

    def send_text(self, to: str, body: str) -> dict[str, Any]:
        body = body[: self.settings.max_message_chars]
        if not self.settings.whatsapp_access_token or not self.settings.whatsapp_phone_number_id:
            logger.warning("WhatsApp credentials missing; dry-run message to %s: %s", to, body)
            return {"dry_run": True, "to": to, "body": body}

        response = self.http.post(
            self._messages_url(),
            headers={
                "Authorization": f"Bearer {self.settings.whatsapp_access_token}",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": body},
            },
        )
        response.raise_for_status()
        return response.json()

    def mark_read(self, message_id: str) -> None:
        if not self.settings.whatsapp_access_token or not self.settings.whatsapp_phone_number_id:
            return
        response = self.http.post(
            self._messages_url(),
            headers={
                "Authorization": f"Bearer {self.settings.whatsapp_access_token}",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id,
            },
        )
        response.raise_for_status()

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        if not self.settings.whatsapp_access_token:
            raise RuntimeError("WHATSAPP_ACCESS_TOKEN is required to download media")

        metadata_response = self.http.get(
            f"{self.settings.whatsapp_graph_base_url}/{media_id}",
            headers={"Authorization": f"Bearer {self.settings.whatsapp_access_token}"},
        )
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
        media_url = metadata.get("url")
        if not media_url:
            raise RuntimeError("WhatsApp media metadata did not include a download URL")

        media_response = self.http.get(
            media_url,
            headers={"Authorization": f"Bearer {self.settings.whatsapp_access_token}"},
        )
        media_response.raise_for_status()
        if len(media_response.content) > self.settings.max_audio_bytes:
            raise ValueError("Media file exceeds MAX_AUDIO_BYTES")
        mime_type = metadata.get("mime_type") or media_response.headers.get(
            "content-type", "application/octet-stream"
        )
        return media_response.content, mime_type
