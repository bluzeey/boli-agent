import hashlib
import hmac

from app.integrations.whatsapp import extract_inbound_messages, verify_whatsapp_signature


def test_signature_verification() -> None:
    body = b'{"hello":"world"}'
    secret = "secret"
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_whatsapp_signature(body, f"sha256={digest}", secret)
    assert not verify_whatsapp_signature(body, "sha256=bad", secret)


def test_extract_text_and_audio_messages() -> None:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "m1",
                                    "from": "919999999999",
                                    "type": "text",
                                    "text": {"body": "Find printers in Jaipur"},
                                },
                                {
                                    "id": "m2",
                                    "from": "919999999999",
                                    "type": "audio",
                                    "audio": {"id": "media-1", "mime_type": "audio/ogg"},
                                },
                            ]
                        }
                    }
                ]
            }
        ]
    }
    messages = extract_inbound_messages(payload)
    assert messages[0].text == "Find printers in Jaipur"
    assert messages[1].media_id == "media-1"
