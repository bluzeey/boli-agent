from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChatMessage, ChatMessageDirection, ChatTransport, Conversation, ProcurementCase

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
