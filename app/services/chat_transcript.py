import logging
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import (
    ChatMessage,
    ChatMessageDirection,
    ChatTransport,
    Conversation,
    InboundMessage,
    ProcurementCase,
    Rfq,
    SearchRun,
    VendorCandidate,
    VendorResponse,
)

logger = logging.getLogger("boli.chat_transcript")

BROWSER_CHAT_COOKIE = "browser_chat_session"
BROWSER_SENDER_PREFIX = "browser:"


def create_browser_session_id() -> str:
    return uuid4().hex


def build_browser_sender(session_id: str) -> str:
    return f"{BROWSER_SENDER_PREFIX}{session_id}"


def session_id_from_sender(sender: str) -> str:
    return sender.removeprefix(BROWSER_SENDER_PREFIX)


def is_browser_sender(sender: str) -> bool:
    return sender.startswith(BROWSER_SENDER_PREFIX)


def get_or_create_conversation(session: Session, sender: str) -> Conversation:
    conversation = session.scalars(
        select(Conversation).where(Conversation.whatsapp_user_id == sender)
    ).first()
    if conversation:
        return conversation

    conversation = Conversation(whatsapp_user_id=sender)
    session.add(conversation)
    session.flush()
    return conversation


def get_active_case(session: Session, conversation_id: str) -> ProcurementCase | None:
    return session.scalars(
        select(ProcurementCase)
        .where(
            ProcurementCase.conversation_id == conversation_id,
            ProcurementCase.status != "closed",
        )
        .order_by(ProcurementCase.created_at.desc())
    ).first()


def record_inbound_message(
    session: Session,
    conversation_id: str,
    sender: str,
    body: str,
    client_message_id: str | None = None,
) -> tuple[ChatMessage, bool]:
    existing = None
    if client_message_id:
        existing = session.scalars(
            select(ChatMessage).where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.client_message_id == client_message_id,
                ChatMessage.direction == ChatMessageDirection.INBOUND.value,
            )
        ).first()
    if existing:
        return existing, False

    message = ChatMessage(
        conversation_id=conversation_id,
        direction=ChatMessageDirection.INBOUND.value,
        sender=sender,
        body=body,
        transport=ChatTransport.BROWSER.value,
        client_message_id=client_message_id,
    )
    session.add(message)
    session.flush()
    return message, True


def record_outbound_message(
    session: Session,
    conversation_id: str,
    sender: str,
    body: str,
    transport: str = ChatTransport.BROWSER.value,
) -> ChatMessage:
    message = ChatMessage(
        conversation_id=conversation_id,
        direction=ChatMessageDirection.OUTBOUND.value,
        sender=sender,
        body=body,
        transport=transport,
    )
    session.add(message)
    session.flush()
    return message


def list_messages(session: Session, conversation_id: str) -> list[ChatMessage]:
    return session.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
    ).all()


def delete_session(session: Session, sender: str) -> bool:
    """Delete a browser chat session and all related data.

    Cascade-deletes in FK-safe order:
    VendorResponse -> VendorCandidate -> SearchRun -> RFQ -> ChatMessage
    -> InboundMessage -> ProcurementCase -> Conversation
    """
    conversation = session.scalars(
        select(Conversation).where(Conversation.whatsapp_user_id == sender)
    ).first()
    if not conversation:
        return False

    conv_id = conversation.id

    cases = session.scalars(
        select(ProcurementCase).where(ProcurementCase.conversation_id == conv_id)
    ).all()
    case_ids = [c.id for c in cases]

    if case_ids:
        session.execute(
            delete(VendorResponse).where(VendorResponse.case_id.in_(case_ids))
        )
        session.execute(
            delete(VendorCandidate).where(VendorCandidate.case_id.in_(case_ids))
        )
        session.execute(delete(SearchRun).where(SearchRun.case_id.in_(case_ids)))
        session.execute(delete(Rfq).where(Rfq.case_id.in_(case_ids)))

    session.execute(delete(ChatMessage).where(ChatMessage.conversation_id == conv_id))
    session.execute(
        delete(InboundMessage).where(InboundMessage.conversation_id == conv_id)
    )

    if case_ids:
        session.execute(
            delete(ProcurementCase).where(ProcurementCase.id.in_(case_ids))
        )

    session.execute(delete(Conversation).where(Conversation.id == conv_id))
    session.commit()

    logger.info("delete_session: deleted conversation=%s (%d cases)", conv_id, len(case_ids))
    return True
