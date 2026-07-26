from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class CaseStatus(StrEnum):
    NEW = "new"
    NEEDS_CLARIFICATION = "needs_clarification"
    READY_TO_SEARCH = "ready_to_search"
    SEARCHING = "searching"
    SHORTLIST_READY = "shortlist_ready"
    FAILED = "failed"
    CLOSED = "closed"


class MessageStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


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
