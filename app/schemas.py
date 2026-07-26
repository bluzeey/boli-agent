from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RequirementExtraction(BaseModel):
    request_type: Literal["product", "recurring_service", "project", "unknown"] = "unknown"
    normalized_need: str
    location: str | None = None
    quantity: str | None = None
    budget: str | None = None
    deadline: str | None = None
    company_context: str | None = None
    must_haves: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    preferred_language: str = "en-IN"
    search_ready: bool = False
    search_query: str | None = None
    acknowledgement: str
    clarifying_question: str | None = None


class SearchResult(BaseModel):
    external_id: str
    name: str
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    rating: float | None = None
    review_count: int | None = None
    source_url: str | None = None
    provider: str


class CaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    category: str
    raw_request: str
    request_type: str
    normalized_need: str
    location: str | None
    quantity: str | None
    budget: str | None
    deadline: str | None
    company_context: str | None
    must_haves: list[str]
    missing_fields: list[str]
    search_query: str | None


class VendorCandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    position: int
    external_id: str
    provider: str
    name: str
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    rating: float | None = None
    review_count: int | None = None
    source_url: str | None = None
    selected: bool = False
    confirmed: bool = False
    expired: bool = False


class RfqRecipient(BaseModel):
    candidate_id: str
    name: str


class RfqRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    version: int
    document_text: str
    fields_snapshot: dict
    recipients: list[RfqRecipient]
    response_deadline: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class ShortlistRequest(BaseModel):
    selection: list[int] = Field(default_factory=list)


class ShortlistResponse(BaseModel):
    case_id: str
    status: str
    selected: list[VendorCandidateRead]


class RfqGenerateResponse(BaseModel):
    case_id: str
    status: str
    rfq: RfqRead


class RfqApproveResponse(BaseModel):
    case_id: str
    status: str
    rfq_id: str
    rfq_status: str
    outreach_authorized: bool


class VendorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    external_id: str
    name: str
    phone: str | None = None
    email: str | None = None
    provider: str
    contact_consent: bool = False
    consent_source: str | None = None
    opted_out: bool = False


class VendorResponseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    vendor_id: str
    rfq_id: str
    rfq_version: int
    channel: str
    status: str
    message_text: str
    response_deadline: str | None = None
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    responded_at: datetime | None = None
    raw_reply: str | None = None
    reply_message_id: str | None = None
    attempts: int = 0
    last_error: str | None = None


class VendorWithStatusRead(VendorRead):
    outreach_status: str = "not_contacted"


class ConsentRequest(BaseModel):
    consent: bool = True
    source: str = "buyer_confirmed"


class ConsentResponse(BaseModel):
    vendor_id: str
    contact_consent: bool
    consent_source: str | None = None


class OutreachSummaryRead(BaseModel):
    case_id: str
    status: str
    total: int
    sent: int
    failed: int
    skipped_cold: int
