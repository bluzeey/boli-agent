"""Anonymous browser ownership: opaque cookies, server-issued chat IDs, private cases."""

import hashlib
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import BrowserOwner, BrowserSession, Conversation, ProcurementCase, ensure_aware

OWNER_COOKIE = "__Host-boli_owner"
OWNER_LIFETIME = timedelta(days=30)
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_SESSION = re.compile(r"^[0-9a-f]{32}$")


def owner_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def find_owner(request: Request, session: Session) -> BrowserOwner | None:
    token = request.cookies.get(OWNER_COOKIE, "")
    if not _TOKEN.fullmatch(token):
        return None
    owner = session.get(BrowserOwner, owner_digest(token))
    if owner is None or ensure_aware(owner.expires_at) <= datetime.now(UTC):
        return None
    return owner


def require_owner(request: Request, session: Session) -> BrowserOwner:
    owner = find_owner(request, session)
    if owner is None:
        raise HTTPException(status_code=401, detail="Start a private browser chat first")
    return owner


def create_or_reuse_owner(request: Request, response: Response, session: Session) -> BrowserOwner:
    owner = find_owner(request, session)
    if owner is not None:
        return owner
    token = secrets.token_urlsafe(32)
    owner = BrowserOwner(
        token_hash=owner_digest(token), expires_at=datetime.now(UTC) + OWNER_LIFETIME
    )
    session.add(owner)
    session.flush()
    response.set_cookie(
        OWNER_COOKIE,
        token,
        max_age=int(OWNER_LIFETIME.total_seconds()),
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return owner


def require_chat(session: Session, owner: BrowserOwner, session_id: str) -> BrowserSession:
    if not _SESSION.fullmatch(session_id):
        raise HTTPException(status_code=404, detail="Chat not found")
    chat = session.scalar(
        select(BrowserSession).where(
            BrowserSession.id == session_id,
            BrowserSession.owner_hash == owner.token_hash,
        )
    )
    if chat is None:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


def require_private_case(
    case_id: str,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    owner = require_owner(request, session)
    found = session.scalar(
        select(ProcurementCase.id)
        .join(Conversation, ProcurementCase.conversation_id == Conversation.id)
        .join(BrowserSession, Conversation.whatsapp_user_id == "browser:" + BrowserSession.id)
        .where(ProcurementCase.id == case_id, BrowserSession.owner_hash == owner.token_hash)
    )
    if found is None:
        raise HTTPException(status_code=404, detail="Case not found")
    request.state.browser_owner_hash = owner.token_hash


def throttle_case_mutation(request: Request) -> None:
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        enforce_rate_limit("case_action", request.state.browser_owner_hash)


def allowed_browser_origin(request: Request) -> bool:
    origin = request.headers.get("origin", "")
    if not origin or origin == "null":
        return False
    configured = get_settings().app_base_url.rstrip("/")
    if origin == configured:
        return True
    # Private SSH forwards and local development never publish an application domain.
    parsed = urlsplit(origin)
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and origin == str(request.base_url).rstrip("/")
    )


@lru_cache(maxsize=1)
def get_rate_store():
    import redis

    return redis.Redis.from_url(
        get_settings().redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def enforce_rate_limit(action: str, owner_hash: str | None = None) -> None:
    """Bound provider-triggering traffic across workers; Redis failure is closed."""
    bucket = int(time.time()) // 60
    limits = [(f"boli:web:{action}:global:{bucket}", 60)]
    if owner_hash:
        limits.append((f"boli:web:{action}:{owner_hash}:{bucket}", 10))
    try:
        pipe = get_rate_store().pipeline(transaction=True)
        for key, _ in limits:
            pipe.incr(key)
            pipe.expire(key, 120, nx=True)
        results = pipe.execute()
    except Exception:
        raise HTTPException(status_code=503, detail="Chat is temporarily unavailable") from None
    if any(results[i * 2] > limit for i, (_, limit) in enumerate(limits)):
        raise HTTPException(
            status_code=429,
            detail="Please wait a minute before trying again",
            headers={"Retry-After": "60"},
        )
