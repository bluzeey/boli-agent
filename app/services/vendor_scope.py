"""Resolve durable vendor ownership from the buyer attached to a case."""

from hashlib import sha256

from sqlalchemy.orm import Session

from app.integrations.whatsapp import normalize_phone
from app.models import BrowserSession, Conversation, ProcurementCase


class VendorScopeError(ValueError):
    """The case or vendor has no verified buyer ownership."""


def vendor_scope_for_case(session: Session, procurement_case: ProcurementCase | str) -> str:
    """Return a registered browser owner's hash or a stable WhatsApp buyer hash.

    Legacy browser senders are deliberately not adopted. A null vendor scope
    must never be used as a fallback for this value.
    """
    if isinstance(procurement_case, str):
        procurement_case = session.get(ProcurementCase, procurement_case)
    if procurement_case is None:
        raise VendorScopeError("Cannot resolve vendor ownership without a case")
    conversation = session.get(Conversation, procurement_case.conversation_id)
    if conversation is None:
        raise VendorScopeError("Cannot resolve vendor ownership without a conversation")

    sender = conversation.whatsapp_user_id
    if sender.startswith("browser:"):
        registry = session.get(BrowserSession, sender.removeprefix("browser:"))
        if registry is None or not registry.owner_hash:
            raise VendorScopeError("Browser session has no registered vendor owner")
        return registry.owner_hash

    normalized = normalize_phone(sender)
    if not normalized:
        raise VendorScopeError("WhatsApp buyer has no normalized sender")
    return sha256(f"whatsapp:{normalized}".encode()).hexdigest()
