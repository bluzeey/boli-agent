import json
from typing import Any

import httpx

from app.config import Settings
from app.schemas import RequirementExtraction

REQUIREMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "request_type": {
            "type": "string",
            "enum": ["product", "recurring_service", "project", "unknown"],
        },
        "normalized_need": {"type": "string"},
        "location": {"type": ["string", "null"]},
        "quantity": {"type": ["string", "null"]},
        "budget": {"type": ["string", "null"]},
        "deadline": {"type": ["string", "null"]},
        "company_context": {"type": ["string", "null"]},
        "must_haves": {"type": "array", "items": {"type": "string"}},
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "preferred_language": {"type": "string"},
        "search_ready": {"type": "boolean"},
        "search_query": {"type": ["string", "null"]},
        "acknowledgement": {"type": "string"},
        "clarifying_question": {"type": ["string", "null"]},
    },
    "required": [
        "request_type",
        "normalized_need",
        "location",
        "quantity",
        "budget",
        "deadline",
        "company_context",
        "must_haves",
        "missing_fields",
        "preferred_language",
        "search_ready",
        "search_query",
        "acknowledgement",
        "clarifying_question",
    ],
    "additionalProperties": False,
}


class SarvamClient:
    def __init__(self, settings: Settings, http_client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.http = http_client or httpx.Client(timeout=60.0)

    def transcribe_audio(self, audio: bytes, mime_type: str) -> str:
        if not self.settings.sarvam_api_key:
            raise RuntimeError("SARVAM_API_KEY is required for voice-note transcription")

        extension = {
            "audio/ogg": "ogg",
            "audio/opus": "opus",
            "audio/mpeg": "mp3",
            "audio/mp4": "m4a",
            "audio/aac": "aac",
            "audio/wav": "wav",
        }.get(mime_type.split(";")[0], "audio")

        response = self.http.post(
            "https://api.sarvam.ai/speech-to-text",
            headers={"api-subscription-key": self.settings.sarvam_api_key},
            files={"file": (f"voice-note.{extension}", audio, mime_type)},
            data={
                "model": self.settings.sarvam_stt_model,
                "mode": self.settings.sarvam_stt_mode,
                "language_code": "unknown",
            },
        )
        response.raise_for_status()
        transcript = response.json().get("transcript")
        if not transcript:
            raise RuntimeError("Sarvam returned an empty transcript")
        return transcript

    def extract_requirement(
        self, text: str, existing_case: dict[str, Any] | None = None
    ) -> RequirementExtraction:
        if not self.settings.sarvam_api_key:
            return heuristic_extract_requirement(text, existing_case)

        system_prompt = """
You are the intake engine for Boli, a WhatsApp-first procurement agent for SMBs.
Convert the user's multilingual or code-mixed message into a procurement requirement.
Do not invent commercial facts. Preserve names, numbers, locations, units, budgets and deadlines.
A request is search-ready only when the thing needed and the relevant geography are known.
For a national or remote requirement, the geography may be 'India' or 'remote'.
Ask at most one concise, high-information clarification question.
The acknowledgement and clarification should follow the user's language and script where practical.
Search query must be suitable for vendor discovery and should contain the category plus location.
""".strip()

        context = json.dumps(existing_case or {}, ensure_ascii=False)
        user_prompt = f"Existing procurement case:\n{context}\n\nLatest user message:\n{text}"
        response = self.http.post(
            "https://api.sarvam.ai/v1/chat/completions",
            headers={
                "api-subscription-key": self.settings.sarvam_api_key,
                "Content-Type": "application/json",
            },
            json={
                "model": self.settings.sarvam_chat_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "procurement_requirement",
                        "strict": True,
                        "schema": REQUIREMENT_SCHEMA,
                    },
                },
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return RequirementExtraction.model_validate(json.loads(content))


def heuristic_extract_requirement(
    text: str, existing_case: dict[str, Any] | None = None
) -> RequirementExtraction:
    existing_case = existing_case or {}
    normalized_need = existing_case.get("normalized_need") or text.strip()
    location = existing_case.get("location")

    lower = text.lower()
    location_markers = [" in ", " near ", " at ", " within "]
    found_location_marker = False
    for marker in location_markers:
        if marker in lower:
            index = lower.rfind(marker)
            possible = text[index + len(marker) :].strip(" .?!")
            if possible:
                location = possible
                normalized_need = text[:index].strip(" .?!") or normalized_need
                found_location_marker = True
                break

    if (
        not found_location_marker
        and not location
        and existing_case.get("normalized_need")
        and 1 <= len(text.split()) <= 6
    ):
        location = text.strip(" .?!")
        normalized_need = existing_case["normalized_need"]

    service_words = ["service", "repair", "maintenance", "cleaning", "pest", "agency"]
    project_words = ["build", "develop", "renovation", "redesign", "install"]
    if any(word in lower for word in project_words):
        request_type = "project"
    elif any(word in lower for word in service_words):
        request_type = "recurring_service"
    else:
        request_type = "product"

    search_ready = bool(normalized_need and location)
    missing_fields = [] if search_ready else ["location"]
    question = None if search_ready else "Which city or service area should I search in?"
    search_query = f"{normalized_need} in {location}" if search_ready else None

    return RequirementExtraction(
        request_type=request_type,
        normalized_need=normalized_need,
        location=location,
        quantity=existing_case.get("quantity"),
        budget=existing_case.get("budget"),
        deadline=existing_case.get("deadline"),
        company_context=existing_case.get("company_context"),
        must_haves=existing_case.get("must_haves", []),
        missing_fields=missing_fields,
        preferred_language=existing_case.get("preferred_language", "en-IN"),
        search_ready=search_ready,
        search_query=search_query,
        acknowledgement="I have captured your requirement.",
        clarifying_question=question,
    )
