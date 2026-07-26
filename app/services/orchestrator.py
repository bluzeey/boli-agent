import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.sarvam import SarvamClient
from app.integrations.whatsapp import WhatsAppClient
from app.models import CaseStatus, Conversation, ProcurementCase, SearchRun
from app.search.base import SearchProvider
from app.services.formatting import render_search_results

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProcurementOrchestrator:
    def __init__(
        self,
        settings: Settings,
        whatsapp: WhatsAppClient,
        sarvam: SarvamClient,
        search_provider: SearchProvider,
    ) -> None:
        self.settings = settings
        self.whatsapp = whatsapp
        self.sarvam = sarvam
        self.search_provider = search_provider

    def handle_text(self, session: Session, sender: str, text: str) -> ProcurementCase:
        text = text.strip()
        if not text:
            raise ValueError("Empty WhatsApp message")

        conversation = self._get_or_create_conversation(session, sender)
        active_case = self._get_active_case(session, conversation.id)

        if active_case and active_case.status == CaseStatus.SHORTLIST_READY.value:
            if text.isdigit():
                self.whatsapp.send_text(
                    sender,
                    "Vendor selection is the next milestone. For now, send a new requirement "
                    "to run another search.",
                )
                return active_case
            active_case = None

        existing_context = self._case_context(active_case) if active_case else None
        extraction = self.sarvam.extract_requirement(text, existing_context)

        procurement_case = active_case or ProcurementCase(conversation_id=conversation.id)
        if not active_case:
            session.add(procurement_case)

        procurement_case.raw_request = (
            f"{procurement_case.raw_request}\n{text}".strip()
            if procurement_case.raw_request
            else text
        )
        procurement_case.request_type = extraction.request_type
        procurement_case.normalized_need = extraction.normalized_need
        procurement_case.location = extraction.location
        procurement_case.quantity = extraction.quantity
        procurement_case.budget = extraction.budget
        procurement_case.deadline = extraction.deadline
        procurement_case.company_context = extraction.company_context
        procurement_case.must_haves = extraction.must_haves
        procurement_case.missing_fields = extraction.missing_fields
        procurement_case.search_query = extraction.search_query
        procurement_case.last_clarifying_question = extraction.clarifying_question
        procurement_case.updated_at = utcnow()
        conversation.preferred_language = extraction.preferred_language
        conversation.updated_at = utcnow()

        if not extraction.search_ready or not extraction.search_query:
            procurement_case.status = CaseStatus.NEEDS_CLARIFICATION.value
            session.add(procurement_case)
            session.add(conversation)
            session.commit()
            reply = extraction.acknowledgement
            if extraction.clarifying_question:
                reply = f"{reply}\n\n{extraction.clarifying_question}"
            self.whatsapp.send_text(sender, reply)
            return procurement_case

        procurement_case.status = CaseStatus.SEARCHING.value
        session.add(procurement_case)
        session.add(conversation)
        session.commit()

        try:
            results = self.search_provider.search(
                extraction.search_query, self.settings.search_result_limit
            )
            session.add(
                SearchRun(
                    case_id=procurement_case.id,
                    provider=self.search_provider.name,
                    query=extraction.search_query,
                    result_count=len(results),
                )
            )
            procurement_case.status = CaseStatus.SHORTLIST_READY.value
            procurement_case.updated_at = utcnow()
            session.add(procurement_case)
            session.commit()
            self.whatsapp.send_text(
                sender,
                render_search_results(
                    extraction.search_query,
                    results,
                    is_demo=self.search_provider.name == "mock",
                ),
            )
            return procurement_case
        except Exception:
            logger.exception("Vendor search failed for case %s", procurement_case.id)
            procurement_case.status = CaseStatus.FAILED.value
            procurement_case.updated_at = utcnow()
            session.add(procurement_case)
            session.commit()
            self.whatsapp.send_text(
                sender,
                "I understood the requirement, but the vendor search failed. "
                "Please try again shortly.",
            )
            raise

    def _get_or_create_conversation(self, session: Session, sender: str) -> Conversation:
        conversation = session.scalars(
            select(Conversation).where(Conversation.whatsapp_user_id == sender)
        ).first()
        if conversation:
            return conversation
        conversation = Conversation(whatsapp_user_id=sender)
        session.add(conversation)
        session.flush()
        return conversation

    def _get_active_case(self, session: Session, conversation_id: str) -> ProcurementCase | None:
        return session.scalars(
            select(ProcurementCase)
            .where(
                ProcurementCase.conversation_id == conversation_id,
                ProcurementCase.status != CaseStatus.CLOSED.value,
            )
            .order_by(ProcurementCase.created_at.desc())
        ).first()

    @staticmethod
    def _case_context(procurement_case: ProcurementCase) -> dict[str, Any]:
        return {
            "request_type": procurement_case.request_type,
            "normalized_need": procurement_case.normalized_need,
            "location": procurement_case.location,
            "quantity": procurement_case.quantity,
            "budget": procurement_case.budget,
            "deadline": procurement_case.deadline,
            "company_context": procurement_case.company_context,
            "must_haves": procurement_case.must_haves,
            "missing_fields": procurement_case.missing_fields,
        }
