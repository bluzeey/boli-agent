from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_aware(dt: datetime | None) -> datetime | None:
    """Return a timezone-aware datetime, assuming UTC when naive.

    SQLite stores datetimes without timezone info, so values read back from it
    are naive even when ``DateTime(timezone=True)`` is used. This normalizes
    values before comparison so aware/naive mismatches do not raise.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


class Base(DeclarativeBase):
    pass


class CaseStatus(StrEnum):
    NEW = "new"
    NEEDS_CLARIFICATION = "needs_clarification"
    READY_TO_SEARCH = "ready_to_search"
    SEARCHING = "searching"
    SHORTLIST_READY = "shortlist_ready"
    AWAITING_SHORTLIST_CONFIRMATION = "awaiting_shortlist_confirmation"
    RFQ_READY = "rfq_ready"
    OUTREACH_APPROVED = "outreach_approved"
    OUTREACH_IN_PROGRESS = "outreach_in_progress"
    COLLECTING_RESPONSES = "collecting_responses"
    AWAITING_APPROVAL = "awaiting_approval"
    DOCUMENT_READY = "document_ready"
    FAILED = "failed"
    CLOSED = "closed"


class MessageStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class RfqStatus(StrEnum):
    DRAFT = "draft"
    SHOWN = "shown"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class ExtractionStatus(StrEnum):
    PENDING = "pending"
    EXTRACTED = "extracted"
    NO_REPLY = "no_reply"
    FAILED = "failed"


class VendorResponseStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED_COLD = "skipped_cold"
    RESPONDED = "responded"
    DECLINED = "declined"
    EXPIRED = "expired"


class ConsentSource(StrEnum):
    PRE_CONSENTED_TEST = "pre_consented_test"
    BUYER_CONFIRMED = "buyer_confirmed"
    VENDOR_OPTED_IN = "vendor_opted_in"


class OutreachChannel(StrEnum):
    WHATSAPP = "whatsapp"
    EMAIL = "email"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    whatsapp_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    preferred_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProcurementCase(Base):
    __tablename__ = "procurement_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), default=CaseStatus.NEW.value, index=True
    )
    category: Mapped[str] = mapped_column(String(32), default="generic", index=True)
    raw_request: Mapped[str] = mapped_column(Text, default="")
    request_type: Mapped[str] = mapped_column(String(32), default="unknown")
    normalized_need: Mapped[str] = mapped_column(Text, default="")
    location: Mapped[str | None] = mapped_column(String(512), nullable=True)
    quantity: Mapped[str | None] = mapped_column(String(256), nullable=True)
    budget: Mapped[str | None] = mapped_column(String(256), nullable=True)
    deadline: Mapped[str | None] = mapped_column(String(256), nullable=True)
    company_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    must_haves: Mapped[list[str]] = mapped_column(JSON, default=list)
    missing_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    search_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_clarifying_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_vendor_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    document_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InboundMessage(Base):
    __tablename__ = "inbound_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    wa_message_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    sender: Mapped[str] = mapped_column(String(64), index=True)
    message_type: Mapped[str] = mapped_column(String(32))
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default=MessageStatus.RECEIVED.value, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SearchRun(Base):
    __tablename__ = "search_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("procurement_cases.id"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64))
    query: Mapped[str] = mapped_column(Text)
    result_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VendorCandidate(Base):
    """A transient discovery result attached to a search run.

    Display fields (name, address, phone, ...) are transient and expire according
    to the source-provider policy. Only the external_id (e.g. a Google Place ID)
    is retained as a durable reference. A candidate becomes a "lead" once the buyer
    confirms the shortlist (confirmed_at is set).
    """

    __tablename__ = "vendor_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    search_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("search_runs.id"), index=True
    )
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("procurement_cases.id"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(256), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(512))
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Rfq(Base):
    """A versioned Request for Quotation generated from the canonical case."""

    __tablename__ = "rfqs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("procurement_cases.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    document_text: Mapped[str] = mapped_column(Text)
    fields_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    recipients: Mapped[list] = mapped_column(JSON, default=list)
    response_deadline: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=RfqStatus.DRAFT.value, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Vendor(Base):
    """A durable vendor business record, deduped by external_id across cases.

    Persists only independently obtained or vendor-confirmed data plus the
    external discovery ID (e.g. a Google Place ID) as a reference. Consent and
    opt-out are durable properties that survive across procurement cases so a
    vendor contacted in one case is recognised in the next.
    """

    __tablename__ = "vendors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    external_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(512))
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    provider: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32), default="generic")
    contact_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VendorResponse(Base):
    """The outreach + response lifecycle for one vendor on one case/RFQ.

    Outreach states (queued/sent/delivered/failed/skipped_cold) are populated by
    the outreach service. Inbound vendor replies are linked here (status
    RESPONDED, responded_at, raw_reply) and structured commercial fields are
    extracted into ``extracted_fields``. Response-deadline expiry is deferred.
    """

    __tablename__ = "vendor_responses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("procurement_cases.id"), index=True
    )
    vendor_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("vendors.id"), index=True
    )
    rfq_id: Mapped[str] = mapped_column(String(36), ForeignKey("rfqs.id"), index=True)
    rfq_version: Mapped[int] = mapped_column(Integer, default=1)
    channel: Mapped[str] = mapped_column(String(32), default=OutreachChannel.WHATSAPP.value)
    status: Mapped[str] = mapped_column(
        String(32), default=VendorResponseStatus.QUEUED.value, index=True
    )
    message_text: Mapped[str] = mapped_column(Text, default="")
    response_deadline: Mapped[str | None] = mapped_column(String(256), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_message_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    extracted_fields: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    extraction_status: Mapped[str] = mapped_column(
        String(32), default=ExtractionStatus.PENDING.value, server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Suppression(Base):
    """A suppression-list entry keyed by phone or email."""

    __tablename__ = "suppressions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    key: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
