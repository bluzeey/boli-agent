import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.sarvam import SarvamClient
from app.integrations.whatsapp import WhatsAppClient
from app.models import (
    CaseStatus,
    ConsentSource,
    Conversation,
    ProcurementCase,
    RfqStatus,
    SearchRun,
    Vendor,
    VendorCandidate,
    VendorResponse,
    VendorResponseStatus,
    ensure_aware,
)
from app.schemas import SearchResult
from app.search.base import SearchProvider
from app.services.formatting import (
    render_case_status,
    render_collecting_hint,
    render_outreach_approved,
    render_outreach_summary,
    render_search_results,
    render_selection_cleared,
    render_selection_confirmation,
    render_stale_shortlist,
)
from app.services.outreach import prepare_outreach, send_outreach
from app.services.rfq import generate_rfq, latest_rfq
from app.services.selection import is_affirmative, is_negative, parse_selection

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


_SHORTLIST_STATES = {
    CaseStatus.SHORTLIST_READY.value,
    CaseStatus.AWAITING_SHORTLIST_CONFIRMATION.value,
    CaseStatus.RFQ_READY.value,
    CaseStatus.OUTREACH_APPROVED.value,
    CaseStatus.COLLECTING_RESPONSES.value,
}


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

        if active_case and active_case.status in _SHORTLIST_STATES:
            handled = self._route_shortlist_states(session, sender, text, active_case)
            if handled is not None:
                return handled
            # Not a selection/confirmation reply: start a fresh case.
            self._close_case(session, active_case)
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
            search_run = SearchRun(
                case_id=procurement_case.id,
                provider=self.search_provider.name,
                query=extraction.search_query,
                result_count=len(results),
            )
            session.add(search_run)
            session.flush()

            expires_at = utcnow() + timedelta(minutes=self.settings.google_result_cache_minutes)
            for index, result in enumerate(results, start=1):
                session.add(
                    VendorCandidate(
                        search_run_id=search_run.id,
                        case_id=procurement_case.id,
                        external_id=result.external_id,
                        provider=result.provider,
                        position=index,
                        name=result.name,
                        address=result.address,
                        phone=result.phone,
                        website=result.website,
                        rating=result.rating,
                        review_count=result.review_count,
                        source_url=result.source_url,
                        expires_at=expires_at,
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

    # -- Shortlist selection state machine ---------------------------------

    def _route_shortlist_states(
        self, session: Session, sender: str, text: str, active_case: ProcurementCase
    ) -> ProcurementCase | None:
        status = active_case.status
        if status == CaseStatus.SHORTLIST_READY.value:
            return self._handle_shortlist_ready(session, sender, text, active_case)
        if status == CaseStatus.AWAITING_SHORTLIST_CONFIRMATION.value:
            return self._handle_shortlist_confirmation(session, sender, text, active_case)
        if status == CaseStatus.RFQ_READY.value:
            return self._handle_rfq_ready(session, sender, text, active_case)
        if status in {
            CaseStatus.COLLECTING_RESPONSES.value,
            CaseStatus.OUTREACH_APPROVED.value,
        }:
            return self._handle_collecting_responses(session, sender, text, active_case)
        return None

    def _handle_shortlist_ready(
        self, session: Session, sender: str, text: str, active_case: ProcurementCase
    ) -> ProcurementCase | None:
        candidates = self._active_candidates(session, active_case.id)
        if not candidates:
            self.whatsapp.send_text(sender, render_stale_shortlist())
            self._close_case(session, active_case)
            return active_case

        max_position = max(c.position for c in candidates)
        result = parse_selection(text, max_position)

        if result.is_reset:
            self._close_case(session, active_case)
            self.whatsapp.send_text(
                sender, "Sure, starting fresh. Send me your new requirement."
            )
            return active_case

        if result.error:
            self.whatsapp.send_text(sender, result.error)
            return active_case

        if result.positions is None:
            # Not a selection: treat as a new requirement (fall through).
            return None

        position_map = {c.position: c for c in candidates}
        # Clear any prior selection before recording a fresh one.
        for candidate in candidates:
            candidate.selected_at = None
        selected = [position_map[p] for p in result.positions if p in position_map]
        now = utcnow()
        if any(ensure_aware(c.expires_at) and ensure_aware(c.expires_at) < now for c in selected):
            self.whatsapp.send_text(sender, render_stale_shortlist())
            self._close_case(session, active_case)
            return active_case

        for candidate in selected:
            candidate.selected_at = now
        active_case.status = CaseStatus.AWAITING_SHORTLIST_CONFIRMATION.value
        active_case.updated_at = now
        session.add_all(candidates)
        session.add(active_case)
        session.commit()
        self.whatsapp.send_text(sender, render_selection_confirmation(selected))
        return active_case

    def _handle_shortlist_confirmation(
        self, session: Session, sender: str, text: str, active_case: ProcurementCase
    ) -> ProcurementCase:
        if is_affirmative(text):
            selected = self._selected_candidates(session, active_case.id)
            if not selected:
                active_case.status = CaseStatus.SHORTLIST_READY.value
                active_case.updated_at = utcnow()
                session.add(active_case)
                session.commit()
                self.whatsapp.send_text(
                    sender,
                    "No vendors were selected. Reply with numbers to shortlist vendors.",
                )
                return active_case

            now = utcnow()
            for candidate in selected:
                candidate.confirmed_at = now
            rfq = generate_rfq(session, active_case, selected)

            active_case.status = CaseStatus.RFQ_READY.value
            active_case.updated_at = now
            session.add_all(selected)
            session.add(active_case)
            session.commit()

            self.whatsapp.send_text(sender, rfq.document_text)
            return active_case

        if is_negative(text):
            selected = self._selected_candidates(session, active_case.id)
            now = utcnow()
            for candidate in selected:
                candidate.selected_at = None
            active_case.status = CaseStatus.SHORTLIST_READY.value
            active_case.updated_at = now
            session.add_all(selected)
            session.add(active_case)
            session.commit()
            self.whatsapp.send_text(sender, render_selection_cleared())
            candidates = self._active_candidates(session, active_case.id)
            results = self._candidates_to_search_results(candidates)
            self.whatsapp.send_text(
                sender,
                render_search_results(
                    active_case.search_query or "",
                    results,
                    is_demo=self.search_provider.name == "mock",
                ),
            )
            return active_case

        self.whatsapp.send_text(
            sender,
            "Please reply *yes* to confirm and generate an RFQ, or *no* to change the selection.",
        )
        return active_case

    def _handle_rfq_ready(
        self, session: Session, sender: str, text: str, active_case: ProcurementCase
    ) -> ProcurementCase:
        if is_affirmative(text):
            rfq = latest_rfq(session, active_case.id)
            now = utcnow()
            if rfq:
                rfq.status = RfqStatus.APPROVED.value
                rfq.updated_at = now
                session.add(rfq)
            active_case.status = CaseStatus.OUTREACH_APPROVED.value
            active_case.updated_at = now
            session.add(active_case)
            session.commit()

            if not rfq:
                self.whatsapp.send_text(
                    sender, "Approved, but no RFQ was found to send. Please regenerate it."
                )
                return active_case

            self.whatsapp.send_text(sender, render_outreach_approved(rfq))

            # Prepare vendor leads and send the RFQ (consented vendors only).
            prepare_outreach(session, active_case, rfq)
            summary = send_outreach(session, active_case.id, self.whatsapp, self.settings)
            self.whatsapp.send_text(sender, render_outreach_summary(summary))
            return active_case

        if is_negative(text):
            active_case.status = CaseStatus.SHORTLIST_READY.value
            active_case.updated_at = utcnow()
            session.add(active_case)
            session.commit()
            self.whatsapp.send_text(
                sender,
                "Okay, you can re-select vendors. Reply with numbers, or send a new requirement.",
            )
            return active_case

        self.whatsapp.send_text(
            sender,
            "Please reply *approve* to authorize outreach or *no* to go back to the shortlist.",
        )
        return active_case

    def _handle_collecting_responses(
        self, session: Session, sender: str, text: str, active_case: ProcurementCase
    ) -> ProcurementCase | None:
        normalized = text.strip().lower()

        if normalized == "status":
            stats = self._case_status_stats(session, active_case.id)
            self.whatsapp.send_text(sender, render_case_status(stats))
            return active_case

        if normalized.startswith("consent "):
            rest = normalized[len("consent ") :]
            positions = parse_selection(rest, 1_000_000)
            if not positions:
                self.whatsapp.send_text(
                    sender, "Reply *consent <number>* using the original shortlist position."
                )
                return active_case
            granted = self._grant_consent(session, active_case.id, positions)
            self.whatsapp.send_text(
                sender,
                f"Consent recorded for {granted} vendor(s). Reply *resend* to send the RFQ "
                "to them, or send a new requirement to start over.",
            )
            return active_case

        if normalized == "resend":
            summary = send_outreach(session, active_case.id, self.whatsapp, self.settings)
            self.whatsapp.send_text(sender, render_outreach_summary(summary))
            return active_case

        if normalized in {"new search", "start over", "reset"}:
            self._close_case(session, active_case)
            self.whatsapp.send_text(
                sender, "Sure, starting fresh. Send me your new requirement."
            )
            return active_case

        # Unrecognized text: keep the case open and hint at available commands
        # rather than silently closing it and starting a new procurement case.
        self.whatsapp.send_text(sender, render_collecting_hint())
        return active_case

    def _case_status_stats(self, session: Session, case_id: str) -> dict:
        responses = list(
            session.scalars(
                select(VendorResponse).where(VendorResponse.case_id == case_id)
            )
        )
        sent = responded = skipped = failed = pending = 0
        for r in responses:
            status = r.status
            if status == VendorResponseStatus.RESPONDED.value:
                responded += 1
                sent += 1
            elif status in {
                VendorResponseStatus.SENT.value,
                VendorResponseStatus.DELIVERED.value,
            }:
                sent += 1
                pending += 1
            elif status == VendorResponseStatus.SKIPPED_COLD.value:
                skipped += 1
            elif status == VendorResponseStatus.FAILED.value:
                failed += 1
            elif status == VendorResponseStatus.QUEUED.value:
                pending += 1
        return {
            "sent": sent,
            "responded": responded,
            "skipped": skipped,
            "failed": failed,
            "pending": pending,
        }

    def _grant_consent(
        self, session: Session, case_id: str, positions: list[int]
    ) -> int:
        """Grant buyer-confirmed consent to vendors by original shortlist position."""
        candidates = {
            c.position: c
            for c in session.scalars(
                select(VendorCandidate).where(VendorCandidate.case_id == case_id)
            )
        }
        now = utcnow()
        granted = 0
        for position in positions:
            candidate = candidates.get(position)
            if not candidate:
                continue
            vendor = session.scalars(
                select(Vendor).where(Vendor.external_id == candidate.external_id)
            ).first()
            if not vendor:
                continue
            vendor.contact_consent = True
            vendor.consent_source = ConsentSource.BUYER_CONFIRMED.value
            vendor.consented_at = now
            vendor.updated_at = now
            session.add(vendor)
            # Re-queue any skipped/failed response for this vendor on this case.
            response = session.scalars(
                select(VendorResponse)
                .where(
                    VendorResponse.case_id == case_id,
                    VendorResponse.vendor_id == vendor.id,
                )
                .order_by(VendorResponse.created_at.desc())
            ).first()
            if response and response.status in {
                VendorResponseStatus.SKIPPED_COLD.value,
                VendorResponseStatus.FAILED.value,
            }:
                response.status = VendorResponseStatus.QUEUED.value
                response.last_error = None
                response.updated_at = now
                session.add(response)
            granted += 1
        session.commit()
        return granted

    # -- Queries / helpers -------------------------------------------------

    def _active_candidates(self, session: Session, case_id: str) -> list[VendorCandidate]:
        return list(
            session.scalars(
                select(VendorCandidate)
                .where(VendorCandidate.case_id == case_id)
                .order_by(VendorCandidate.position.asc())
            )
        )

    def _selected_candidates(self, session: Session, case_id: str) -> list[VendorCandidate]:
        return list(
            session.scalars(
                select(VendorCandidate)
                .where(
                    VendorCandidate.case_id == case_id,
                    VendorCandidate.selected_at.is_not(None),
                )
                .order_by(VendorCandidate.position.asc())
            )
        )

    @staticmethod
    def _candidates_to_search_results(candidates: list[VendorCandidate]) -> list[SearchResult]:
        return [
            SearchResult(
                external_id=c.external_id,
                name=c.name,
                address=c.address,
                phone=c.phone,
                website=c.website,
                rating=c.rating,
                review_count=c.review_count,
                source_url=c.source_url,
                provider=c.provider,
            )
            for c in candidates
        ]

    def _close_case(self, session: Session, active_case: ProcurementCase) -> None:
        active_case.status = CaseStatus.CLOSED.value
        active_case.updated_at = utcnow()
        session.add(active_case)
        session.commit()

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
