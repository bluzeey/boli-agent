import json
import logging
import re
from typing import Any

import httpx

from app.config import Settings
from app.schemas import QuoteExtraction, RequirementExtraction

logger = logging.getLogger(__name__)

REQUIREMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "request_type": {
            "type": "string",
            "enum": ["product", "recurring_service", "project", "unknown"],
        },
        "normalized_need": {
            "type": "string",
            "description": "Concrete product or service needed, from the message or existing case; "
            "use an empty string if unknown. Do not invent specifications.",
        },
        "location": {
            "type": ["string", "null"],
            "description": "Known city, service area, country, or explicitly remote scope; "
            "null if unknown. Never assume India or remote from the language alone.",
        },
        "quantity": {
            "type": ["string", "null"],
            "description": "Quantity and units only if stated or already known; otherwise null. "
            "Optional for vendor search.",
        },
        "budget": {
            "type": ["string", "null"],
            "description": "Budget only if stated or already known; otherwise null. "
            "Optional for vendor search.",
        },
        "deadline": {
            "type": ["string", "null"],
            "description": "Deadline only if stated or already known; otherwise null. "
            "Optional for vendor search.",
        },
        "company_context": {
            "type": ["string", "null"],
            "description": "Company context only if stated or already known; otherwise null. "
            "Optional for vendor search.",
        },
        "must_haves": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Only stated or already known constraints; empty array if none.",
        },
        "missing_fields": {
            "type": "array",
            "items": {"type": "string", "enum": ["normalized_need", "location"]},
            "description": "Only missing vendor-search prerequisites: normalized_need and/or "
            "location. Empty when both are known; never include optional commercial fields.",
        },
        "preferred_language": {"type": "string"},
        "search_ready": {
            "type": "boolean",
            "description": "True if and only if a concrete need and geography are known. "
            "Missing quantity, budget, deadline, or company context must not make this false.",
        },
        "search_query": {
            "type": ["string", "null"],
            "description": "Vendor-discovery query containing the known need and geography; "
            "null until both are known. Do not add unstated facts.",
        },
        "acknowledgement": {"type": "string"},
        "clarifying_question": {
            "type": ["string", "null"],
            "description": "At most one question about a missing need or geography; "
            "null when search_ready is true. Do not demand optional commercial details.",
        },
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


QUOTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "price": {"type": ["string", "null"]},
        "tax": {"type": ["string", "null"]},
        "unit_price": {"type": ["string", "null"]},
        "lead_time": {"type": ["string", "null"]},
        "payment_terms": {"type": ["string", "null"]},
        "exclusions": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": ["string", "null"]},
        "missing": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "price",
        "tax",
        "unit_price",
        "lead_time",
        "payment_terms",
        "exclusions",
        "notes",
        "missing",
    ],
    "additionalProperties": False,
}


class SarvamClient:
    def __init__(self, settings: Settings, http_client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.http = http_client or httpx.Client(timeout=60.0)

    @staticmethod
    def _parse_json_response(content: str) -> dict[str, Any]:
        """Accept JSON or one fully enclosed JSON markdown block, without repairing it."""
        stripped = content.strip()
        if stripped.startswith("```"):
            fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", stripped, re.S | re.I)
            if not fenced:
                raise ValueError("Invalid JSON markdown block")
            stripped = fenced.group(1)
        return json.loads(stripped)

    @classmethod
    def _completion_json(cls, response: httpx.Response) -> dict[str, Any]:
        """Require an actual structured answer without exposing model output in errors."""
        try:
            payload = response.json()
            choice = payload["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (ValueError, TypeError, KeyError, IndexError):
            raise RuntimeError("Sarvam returned an invalid response envelope") from None
        if not isinstance(content, str) or not content.strip():
            reason = (
                finish_reason
                if isinstance(finish_reason, str)
                and finish_reason in {"stop", "length", "tool_calls", "content_filter"}
                else "unknown"
            )
            raise RuntimeError(f"Sarvam returned no structured answer (finish reason: {reason})")
        if finish_reason is not None and finish_reason != "stop":
            raise RuntimeError("Sarvam returned an incomplete structured answer")
        try:
            parsed = cls._parse_json_response(content)
        except (TypeError, ValueError):
            raise RuntimeError("Sarvam returned invalid structured JSON") from None
        if not isinstance(parsed, dict):
            raise RuntimeError("Sarvam returned a non-object structured answer")
        return parsed

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
Merge the existing case with the latest message; preserve known facts unless the user changes them.
A request is search-ready if and only if a concrete thing needed and relevant geography are known.
Quantity, budget, deadline, company context and must-haves are optional, never search prerequisites.
For example, 'Find packaging boxes in Jaipur' is search-ready even without quantity or budget.
Use null for unknown optional values, [] for unstated must-haves, and never guess commercial facts.
Use an empty normalized_need and null location when unknown, never placeholder text like 'unknown'.
For an explicitly national or remote requirement, geography may be 'India' or 'remote'.
Never infer geography from language, currency, or an unstated default.
missing_fields must contain only missing normalized_need and/or location, or [] when search-ready.
Ask at most one concise clarification about the missing need or geography.
When search-ready, set clarifying_question to null; do not ask for optional commercial details.
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
                "reasoning_effort": None,
                "max_tokens": 2048,
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
        parsed = self._completion_json(response)
        try:
            extraction = RequirementExtraction.model_validate(parsed, strict=True)
        except ValueError:
            raise RuntimeError("Sarvam returned invalid requirement fields") from None
        extraction.normalized_need = extraction.normalized_need.strip()
        for field in ("location", "quantity", "budget", "deadline", "company_context"):
            value = getattr(extraction, field)
            if value is not None and not value.strip():
                setattr(extraction, field, None)
        # Search metadata follows validated extracted facts, not the model's optional-field policy.
        extraction.missing_fields = [
            field for field in ("normalized_need", "location") if not getattr(extraction, field)
        ]
        extraction.search_ready = not extraction.missing_fields
        extraction.search_query = (
            f"{extraction.normalized_need} in {extraction.location}"
            if extraction.search_ready
            else None
        )
        if extraction.search_ready:
            extraction.clarifying_question = None
        return extraction

    def extract_quote(self, reply_text: str, required_fields: list[str]) -> QuoteExtraction:
        if not self.settings.sarvam_api_key:
            return heuristic_extract_quote(reply_text, required_fields)

        system_prompt = (
            "You are the quotation extraction engine for Boli, a WhatsApp-first procurement agent. "
            "Extract commercial fields from a vendor's reply to an RFQ. "
            "Do not invent values. Preserve numbers, units, currencies, and durations "
            "exactly as stated. "
            "If a field is not present in the reply, leave its value null and list it under "
            "'missing' only if it is required. Use [] for unstated exclusions. "
            "Capture any exclusions, conditions, or caveats in 'exclusions'. "
            "Capture delivery/lead time as a short string e.g. '3 days', '2 weeks'. "
            "Capture price as the total quoted amount with currency; "
            "unit_price as per-unit if stated."
        ).strip()
        user_prompt = (
            f"Required fields: {', '.join(required_fields)}\n\nVendor reply:\n{reply_text}"
        )
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
                "reasoning_effort": None,
                "max_tokens": 2048,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "vendor_quote",
                        "strict": True,
                        "schema": QUOTE_SCHEMA,
                    },
                },
            },
        )
        response.raise_for_status()
        parsed = self._completion_json(response)
        try:
            return QuoteExtraction.model_validate(parsed, strict=True)
        except ValueError:
            raise RuntimeError("Sarvam returned invalid quote fields") from None


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


def heuristic_extract_quote(
    reply_text: str, required_fields: list[str] | None = None
) -> QuoteExtraction:
    """Deterministic fallback quote parser for local development (no Sarvam key).

    Recognises common Indian quoting patterns: currency amounts, lead times,
    GST/tax, and payment terms. Anything it cannot parse is left null.
    """
    required_fields = required_fields or []
    text = (reply_text or "").strip()
    lower = text.lower()

    price = tax = unit_price = lead_time = payment_terms = None
    exclusions: list[str] = []

    # Price: "Rs 5000", "₹5000", "5000 rs", "price: 8000", "total: 12,000"
    price_match = re.search(
        r"(?:rs\.?|inr|₹|price|total|amount|quote)\s*[: ]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
        lower,
    )
    if price_match:
        price = price_match.group(1).rstrip(",")
    else:
        # bare number after a currency word
        bare = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:rs|rupees|₹)", lower)
        if bare:
            price = bare.group(1).rstrip(",")

    # Unit price: "per unit 500", "500/unit", "@ 500 each"
    unit_match = re.search(
        r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:per|/|@)\s*(?:unit|piece|each|pc|kg|item)",
        lower,
    )
    if unit_match:
        unit_price = unit_match.group(1).rstrip(",")

    # Tax / GST: "GST 18%", "including 18% gst", "tax: 18%"
    tax_match = re.search(r"(?:gst|tax)\s*[: ]?\s*([0-9]+(?:\.[0-9]+)?\s*%)", lower)
    if tax_match:
        tax = tax_match.group(1).replace(" ", "")
    else:
        tax_match2 = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%\s*(?:gst|tax)", lower)
        if tax_match2:
            tax = tax_match2.group(1) + "%"

    # Lead time: "3 days", "2 weeks", "7-10 days", "delivery in 5 days"
    lead_match = re.search(
        r"(?:delivery|lead time|delivered|ship|dispatch)?\s*(?:in|within|:)?\s*"
        r"([0-9]+(?:\s*[-to]+\s*[0-9]+)?)\s*(days|day|weeks|week|months|month)",
        lower,
    )
    if lead_match:
        lead_time = f"{lead_match.group(1).strip()} {lead_match.group(2)}"

    # Payment terms: "50% advance", "net 30", "50% advance, 50% on delivery"
    pay_match = re.search(
        r"(?:payment|terms)\s*[: ]?\s*"
        r"([0-9]+%?\s*(?:advance|upfront|on delivery|on receipt)[^.\n]*)",
        lower,
    )
    if pay_match:
        payment_terms = pay_match.group(1).strip()
    elif "net 30" in lower:
        payment_terms = "net 30"
    elif "net 15" in lower:
        payment_terms = "net 15"

    # Exclusions: "excludes ...", "not included ..."
    excl_match = re.search(r"(?:excludes?|not included?|excluding)\s*[: ]?\s*([^.\n]+)", lower)
    if excl_match:
        exclusions = [excl_match.group(1).strip()]

    extracted = QuoteExtraction(
        price=price,
        tax=tax,
        unit_price=unit_price,
        lead_time=lead_time,
        payment_terms=payment_terms,
        exclusions=exclusions,
        notes=None,
    )

    # Compute missing relative to required fields.
    field_map = {
        "price": extracted.price,
        "tax": extracted.tax,
        "lead_time": extracted.lead_time,
        "payment_terms": extracted.payment_terms,
    }
    extracted.missing = [f for f in required_fields if not field_map.get(f)]
    return extracted
