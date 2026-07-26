import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.db as db_module
from app.container import build_webhook_processor
from app.models import Conversation
from app.schemas import (
    BrowserChatDeleteResponse,
    BrowserChatMessageSend,
    BrowserChatTranscriptRead,
)
from app.services.chat_transcript import (
    BROWSER_CHAT_COOKIE,
    build_browser_sender,
    create_browser_session_id,
    delete_session,
    get_active_case,
    get_or_create_conversation,
    list_messages,
    record_inbound_message,
)

logger = logging.getLogger("boli.browser_chat")
router = APIRouter(tags=["browser-chat"])
_CHAT_PAGE = Path(__file__).resolve().parent.parent / "static" / "chat.html"


def _resolve_session_id(
    request: Request,
    response: Response,
    session_id: str | None = None,
) -> str:
    if session_id:
        return session_id

    cookie_id = request.cookies.get(BROWSER_CHAT_COOKIE)
    if cookie_id:
        return cookie_id

    new_id = create_browser_session_id()
    response.set_cookie(
        key=BROWSER_CHAT_COOKIE,
        value=new_id,
        max_age=60 * 60 * 24 * 30,
        samesite="lax",
    )
    return new_id


@router.get("/chat")
def chat_page() -> FileResponse:
    return FileResponse(_CHAT_PAGE)


@router.post("/api/browser-chat/sessions", response_model=BrowserChatTranscriptRead)
def create_browser_chat_session(request: Request, response: Response) -> BrowserChatTranscriptRead:
    session_id = create_browser_session_id()
    response.set_cookie(
        key=BROWSER_CHAT_COOKIE,
        value=session_id,
        max_age=60 * 60 * 24 * 30,
        samesite="lax",
    )
    return _snapshot_for_session(session_id)


@router.delete(
    "/api/browser-chat/sessions/{session_id}",
    response_model=BrowserChatDeleteResponse,
)
def delete_browser_chat_session(session_id: str) -> BrowserChatDeleteResponse:
    sender = build_browser_sender(session_id)
    with Session(db_module.engine, expire_on_commit=False) as session:
        deleted = delete_session(session, sender)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return BrowserChatDeleteResponse(session_id=session_id, deleted=True)


@router.get("/api/browser-chat/messages", response_model=BrowserChatTranscriptRead)
def get_browser_chat_messages(
    request: Request,
    response: Response,
    session_id: str | None = Query(default=None),
) -> BrowserChatTranscriptRead:
    sid = _resolve_session_id(request, response, session_id)
    return _snapshot_for_session(sid)


@router.post("/api/browser-chat/messages", response_model=BrowserChatTranscriptRead)
def send_browser_chat_message(
    payload: BrowserChatMessageSend,
    request: Request,
    response: Response,
) -> BrowserChatTranscriptRead:
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message text is required")

    sid = _resolve_session_id(request, response, payload.session_id)
    sender = build_browser_sender(sid)

    with Session(db_module.engine, expire_on_commit=False) as session:
        conversation = get_or_create_conversation(session, sender)
        _, created = record_inbound_message(
            session,
            conversation.id,
            sender="operator",
            body=text,
            client_message_id=payload.client_message_id,
        )
        session.commit()

    if created:
        processor = build_webhook_processor()
        with Session(db_module.engine, expire_on_commit=False) as session:
            processor.orchestrator.handle_text(session, sender, text)

    return _snapshot_for_session(sid)


def _snapshot_for_session(session_id: str) -> BrowserChatTranscriptRead:
    sender = build_browser_sender(session_id)
    with Session(db_module.engine, expire_on_commit=False) as session:
        conversation = session.scalars(
            select(Conversation).where(Conversation.whatsapp_user_id == sender)
        ).first()
        if not conversation:
            return BrowserChatTranscriptRead(
                session_id=session_id,
                sender=sender,
                conversation_id=None,
                active_case_id=None,
                case_status=None,
                messages=[],
            )

        active_case = get_active_case(session, conversation.id)
        messages = list_messages(session, conversation.id)
        return BrowserChatTranscriptRead(
            session_id=session_id,
            sender=sender,
            conversation_id=conversation.id,
            active_case_id=active_case.id if active_case else None,
            case_status=active_case.status if active_case else None,
            messages=[
                {
                    "id": m.id,
                    "direction": m.direction,
                    "sender": m.sender,
                    "body": m.body,
                    "transport": m.transport,
                    "created_at": m.created_at,
                }
                for m in messages
            ],
        )
