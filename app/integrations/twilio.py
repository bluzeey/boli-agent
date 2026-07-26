import base64
import hashlib
import hmac
import logging
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import Settings
from app.integrations.whatsapp import InboundWhatsAppMessage

logger = logging.getLogger(__name__)


def _normalize_recipient(to: str) -> str:
    """Normalize a recipient to Twilio's ``whatsapp:+E.164`` form."""
    stripped = to
    if stripped.startswith("whatsapp:"):
        stripped = stripped[len("whatsapp:") :]
    stripped = stripped.lstrip("+")
    return f"whatsapp:+{stripped}"


def _normalize_sender(from_value: str) -> str:
    """Normalize Twilio's ``whatsapp:+9199...`` From to bare digits (Meta format)."""
    stripped = from_value
    if stripped.startswith("whatsapp:"):
        stripped = stripped[len("whatsapp:") :]
    return stripped.lstrip("+")


def verify_twilio_signature(
    url: str, params: dict[str, Any], signature: str | None, auth_token: str
) -> bool:
    """Validate Twilio's X-Twilio-Signature header.

    Twilio signs ``url + sorted(key+value)`` with HMAC-SHA1 using the auth token,
    base64-encoded. When ``auth_token`` is empty, validation is skipped (dev escape
    hatch, mirroring the Meta adapter).
    """
    if not auth_token:
        return True
    if not signature:
        return False
    sorted_items = sorted(params.items())
    concatenated = url + "".join(f"{k}{v}" for k, v in sorted_items)
    digest = hmac.new(
        auth_token.encode("utf-8"), concatenated.encode("utf-8"), hashlib.sha1
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def extract_twilio_inbound(form: dict[str, Any]) -> list[InboundWhatsAppMessage]:
    """Parse a Twilio WhatsApp webhook form payload into inbound messages.

    Twilio posts one message per webhook. Media (voice notes) arrive as
    ``MediaUrl0`` / ``MediaContentType0``.
    """
    output: list[InboundWhatsAppMessage] = []
    message_id = form.get("MessageSid")
    from_value = form.get("From")
    if not message_id or not from_value:
        return output

    sender = _normalize_sender(str(from_value))
    body = form.get("Body") or ""
    num_media = int(form.get("NumMedia") or 0)
    media_url = form.get("MediaUrl0") if num_media else None
    media_content_type = form.get("MediaContentType0") if num_media else None

    if media_url and media_content_type and str(media_content_type).startswith("audio/"):
        message_type = "audio"
        text = None
    elif media_url:
        message_type = "media"
        text = None
    else:
        message_type = "text"
        text = str(body) if body else None

    output.append(
        InboundWhatsAppMessage(
            message_id=str(message_id),
            sender=sender,
            message_type=message_type,
            text=text,
            media_id=str(media_url) if media_url else None,
            mime_type=str(media_content_type) if media_content_type else None,
        )
    )
    return output


class TwilioWhatsAppClient:
    """A WhatsApp client backed by the Twilio REST API (Sandbox-compatible)."""

    def __init__(self, settings: Settings, http_client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.http = http_client or httpx.Client(timeout=30.0)

    def _messages_url(self) -> str:
        return f"https://api.twilio.com/2010-04-01/Accounts/{self.settings.twilio_account_sid}/Messages.json"

    def _auth(self) -> tuple[str, str]:
        return (self.settings.twilio_account_sid, self.settings.twilio_auth_token)

    def _has_credentials(self) -> bool:
        return bool(
            self.settings.twilio_account_sid
            and self.settings.twilio_auth_token
            and self.settings.twilio_whatsapp_from
        )

    def send_text(self, to: str, body: str) -> dict[str, Any]:
        body = body[: self.settings.max_message_chars]
        if not self._has_credentials():
            logger.warning("Twilio credentials missing; dry-run message to %s: %s", to, body)
            return {"dry_run": True, "to": to, "body": body}

        from_number = self.settings.twilio_whatsapp_from
        if not from_number.startswith("whatsapp:"):
            from_number = f"whatsapp:{from_number}"

        response = self.http.post(
            self._messages_url(),
            auth=self._auth(),
            data=urlencode(
                {
                    "From": from_number,
                    "To": _normalize_recipient(to),
                    "Body": body,
                }
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        return response.json()

    def mark_read(self, message_id: str) -> None:
        # Twilio has no equivalent read-receipt API for WhatsApp Sandbox.
        return None

    def download_media(self, media_url: str) -> tuple[bytes, str]:
        if not self._has_credentials():
            raise RuntimeError("TWILIO_ACCOUNT_SID/AUTH_TOKEN are required to download media")

        media_response = self.http.get(
            media_url, auth=self._auth(), follow_redirects=True
        )
        media_response.raise_for_status()
        if len(media_response.content) > self.settings.max_audio_bytes:
            raise ValueError("Media file exceeds MAX_AUDIO_BYTES")
        mime_type = media_response.headers.get("content-type", "application/octet-stream")
        return media_response.content, mime_type
