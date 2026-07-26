import base64
import hashlib
import hmac

from app.config import Settings
from app.integrations.twilio import (
    TwilioWhatsAppClient,
    _normalize_recipient,
    _normalize_sender,
    extract_twilio_inbound,
    verify_twilio_signature,
)


def _sign(url: str, params: dict, token: str) -> str:
    concatenated = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    digest = hmac.new(token.encode(), concatenated.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def test_signature_validation_round_trip() -> None:
    url = "https://example.ngrok.app/webhooks/twilio/whatsapp"
    params = {"MessageSid": "SM123", "From": "whatsapp:+919999999999", "Body": "hi"}
    token = "test-token"
    sig = _sign(url, params, token)
    assert verify_twilio_signature(url, params, sig, token)
    assert not verify_twilio_signature(url, params, "bad-signature", token)


def test_signature_skipped_when_token_empty() -> None:
    # Dev escape hatch: no auth token configured -> validation skipped.
    assert verify_twilio_signature("https://x", {"a": "b"}, "anything", "")


def test_normalize_sender_strips_whatsapp_prefix() -> None:
    assert _normalize_sender("whatsapp:+919999999999") == "919999999999"
    assert _normalize_sender("+919999999999") == "919999999999"


def test_normalize_recipient_adds_whatsapp_prefix() -> None:
    assert _normalize_recipient("919999999999") == "whatsapp:+919999999999"
    assert _normalize_recipient("whatsapp:+919999999999") == "whatsapp:+919999999999"


def test_extract_text_message() -> None:
    form = {
        "MessageSid": "SMabc",
        "From": "whatsapp:+919999999999",
        "Body": "Find packaging vendors in Jaipur",
        "NumMedia": "0",
    }
    messages = extract_twilio_inbound(form)
    assert len(messages) == 1
    msg = messages[0]
    assert msg.message_id == "SMabc"
    assert msg.sender == "919999999999"  # normalized to bare digits (Meta format)
    assert msg.message_type == "text"
    assert msg.text == "Find packaging vendors in Jaipur"
    assert msg.media_id is None


def test_extract_voice_note_as_audio() -> None:
    form = {
        "MessageSid": "SMdef",
        "From": "whatsapp:+919999999999",
        "Body": "",
        "NumMedia": "1",
        "MediaUrl0": "https://api.twilio.com/media/xyz",
        "MediaContentType0": "audio/ogg",
    }
    messages = extract_twilio_inbound(form)
    assert len(messages) == 1
    msg = messages[0]
    assert msg.message_type == "audio"
    assert msg.media_id == "https://api.twilio.com/media/xyz"
    assert msg.mime_type == "audio/ogg"
    assert msg.text is None


def test_send_text_dry_run_without_credentials() -> None:
    client = TwilioWhatsAppClient(Settings())  # no Twilio creds configured
    result = client.send_text("919999999999", "hello from boli")
    assert result["dry_run"] is True
    assert result["to"] == "919999999999"
    assert result["body"] == "hello from boli"


def test_mark_read_is_noop() -> None:
    client = TwilioWhatsAppClient(Settings())
    # Should not raise and returns None.
    assert client.mark_read("SM123") is None
