import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.db as db_module
from app.browser_access import (
    create_or_reuse_owner,
    enforce_rate_limit,
    find_owner,
    require_chat,
    require_owner,
)
from app.container import build_webhook_processor
from app.models import BrowserSession, ChatMessage, Conversation
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
    record_outbound_message,
)

logger = logging.getLogger("boli.browser_chat")
router = APIRouter(tags=["browser-chat"])
_CHAT_PAGE = Path(__file__).resolve().parent.parent / "static" / "chat.html"


def _resolve_session_id(
    request: Request,
    response: Response,
    session_id: str | None = None,
) -> str:
    sid = session_id or request.cookies.get(BROWSER_CHAT_COOKIE, "")
    with Session(db_module.engine) as session:
        owner = require_owner(request, session)
        require_chat(session, owner, sid)
    return sid


@router.get("/chat")
def chat_page() -> FileResponse:
    return FileResponse(_CHAT_PAGE)


@router.post("/api/browser-chat/sessions", response_model=BrowserChatTranscriptRead)
def create_browser_chat_session(request: Request, response: Response) -> BrowserChatTranscriptRead:
    enforce_rate_limit("create")
    session_id = create_browser_session_id()
    with Session(db_module.engine) as session:
        owner = create_or_reuse_owner(request, response, session)
        session.add(BrowserSession(id=session_id, owner_hash=owner.token_hash))
        session.commit()
    response.set_cookie(
        key=BROWSER_CHAT_COOKIE,
        value=session_id,
        max_age=60 * 60 * 24 * 30,
        samesite="lax",
        secure=True,
        httponly=True,
    )
    return _snapshot_for_session(session_id)


@router.get("/api/browser-chat/sessions")
def list_browser_sessions(request: Request) -> dict:
    with Session(db_module.engine) as session:
        owner = find_owner(request, session)
        if owner is None:
            return {"sessions": []}
        rows = session.scalars(
            select(BrowserSession)
            .where(
                BrowserSession.owner_hash == owner.token_hash,
            )
            .order_by(BrowserSession.created_at.desc())
        ).all()
        out = []
        for row in rows:
            conversation = session.scalar(
                select(Conversation).where(
                    Conversation.whatsapp_user_id == build_browser_sender(row.id),
                )
            )
            latest = (
                session.scalar(
                    select(ChatMessage)
                    .where(
                        ChatMessage.conversation_id == conversation.id,
                    )
                    .order_by(ChatMessage.created_at.desc())
                    .limit(1)
                )
                if conversation
                else None
            )
            case = get_active_case(session, conversation.id) if conversation else None
            when = latest.created_at if latest else row.created_at
            out.append(
                {
                    "session_id": row.id,
                    "preview": latest.body[:60] if latest else "",
                    "case_status": case.status if case else None,
                    "updated_at": when.isoformat(),
                }
            )
        return {"sessions": out}


@router.delete(
    "/api/browser-chat/sessions/{session_id}",
    response_model=BrowserChatDeleteResponse,
)
def delete_browser_chat_session(session_id: str, request: Request) -> BrowserChatDeleteResponse:
    sender = build_browser_sender(session_id)
    with Session(db_module.engine, expire_on_commit=False) as session:
        owner = require_owner(request, session)
        chat = require_chat(session, owner, session_id)
        delete_session(session, sender, commit=False)
        session.delete(chat)
        session.commit()
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
        owner = require_owner(request, session)
        enforce_rate_limit("message", owner.token_hash)
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
        try:
            processor = build_webhook_processor()
            with Session(db_module.engine, expire_on_commit=False) as session:
                processor.orchestrator.handle_text(session, sender, text)
        except Exception as exc:
            logger.exception("browser chat processing failed for sender=%s: %s", sender, exc)
            error_body = "[ERROR] The request could not be processed. Please try again."
            with Session(db_module.engine, expire_on_commit=False) as session:
                conv = session.scalars(
                    select(Conversation).where(Conversation.whatsapp_user_id == sender)
                ).first()
                if conv:
                    record_outbound_message(
                        session,
                        conv.id,
                        sender="system",
                        body=error_body,
                        transport="error",
                    )
                    session.commit()

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
