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
