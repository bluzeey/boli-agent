import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from time import sleep

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.categories import get_category_pack
from app.config import Settings
from app.integrations.whatsapp import WhatsAppClient
from app.models import (
    CaseStatus,
    ConsentSource,
    OutreachChannel,
    ProcurementCase,
    Rfq,
    Suppression,
    Vendor,
    VendorCandidate,
    VendorResponse,
    VendorResponseStatus,
)
from app.services.rfq import case_snapshot

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class OutreachSummary:
    case_id: str
    status: str
    total: int
    sent: int
    failed: int
    skipped_cold: int


def _default_consent(provider: str) -> tuple[bool, str | None]:
    """Mock/test vendors are pre-consented; discovered vendors are cold."""
    if provider in ("mock", "test"):
        return True, ConsentSource.PRE_CONSENTED_TEST.value
    return False, None


def _upsert_vendor(session: Session, candidate: VendorCandidate) -> Vendor:
    existing = session.scalars(
        select(Vendor).where(Vendor.external_id == candidate.external_id)
    ).first()
    now = utcnow()
    if existing:
        # Preserve durable consent/opt-out; fill missing contact details.
        if not existing.phone and candidate.phone:
            existing.phone = candidate.phone
        if not existing.name or existing.name == "":
            existing.name = candidate.name
        existing.updated_at = now
        session.add(existing)
        return existing

    consent, source = _default_consent(candidate.provider)
    vendor = Vendor(
        external_id=candidate.external_id,
        name=candidate.name,
        phone=candidate.phone,
        email=None,
        provider=candidate.provider,
        category="generic",
        contact_consent=consent,
        consent_source=source,
        consented_at=now if consent else None,
        opted_out=False,
    )
    session.add(vendor)
    session.flush()
    return vendor


def _is_suppressed(session: Session, vendor: Vendor) -> bool:
    if vendor.phone:
        if session.scalars(
            select(Suppression).where(Suppression.key == vendor.phone)
        ).first():
            return True
    if vendor.email:
        if session.scalars(
            select(Suppression).where(Suppression.key == vendor.email)
        ).first():
            return True
    return False


def prepare_outreach(
    session: Session, procurement_case: ProcurementCase, rfq: Rfq
) -> list[VendorResponse]:
    """Create queued VendorResponse rows for each confirmed candidate.

    Upserts durable Vendor records (preserving cross-case consent) and renders a
    vendor-facing RFQ message per recipient. No message is sent here.
    """
    pack = get_category_pack(procurement_case.category)
    snapshot = case_snapshot(procurement_case)

    confirmed = session.scalars(
        select(VendorCandidate)
        .where(
            VendorCandidate.case_id == procurement_case.id,
            VendorCandidate.confirmed_at.is_not(None),
        )
        .order_by(VendorCandidate.position.asc())
    ).all()

    responses: list[VendorResponse] = []
    for candidate in confirmed:
        vendor = _upsert_vendor(session, candidate)
        message_text = pack.render_vendor_rfq(snapshot, vendor.name)
        response = VendorResponse(
            case_id=procurement_case.id,
            vendor_id=vendor.id,
            rfq_id=rfq.id,
            rfq_version=rfq.version,
            channel=OutreachChannel.WHATSAPP.value,
            status=VendorResponseStatus.QUEUED.value,
            message_text=message_text,
            response_deadline=rfq.response_deadline,
        )
        session.add(response)
        responses.append(response)

    session.flush()
    return responses


def send_outreach(
    session: Session,
    case_id: str,
    whatsapp: WhatsAppClient,
    settings: Settings,
) -> OutreachSummary:
    """Send queued outreach for a case, respecting consent and rate limits."""
    procurement_case = session.scalars(
        select(ProcurementCase).where(ProcurementCase.id == case_id)
    ).first()
    if not procurement_case:
        raise ValueError(f"Case {case_id} not found")

    responses = list(
        session.scalars(
            select(VendorResponse)
            .where(
                VendorResponse.case_id == case_id,
                VendorResponse.status == VendorResponseStatus.QUEUED.value,
            )
            .order_by(VendorResponse.created_at.asc())
        )
    )

    procurement_case.status = CaseStatus.OUTREACH_IN_PROGRESS.value
    procurement_case.updated_at = utcnow()
    session.add(procurement_case)
    session.commit()

    sent = failed = skipped_cold = 0
    sent_in_batch = 0

    for response in responses:
        vendor = session.scalars(
            select(Vendor).where(Vendor.id == response.vendor_id)
        ).first()
        if not vendor:
            response.status = VendorResponseStatus.FAILED.value
            response.last_error = "Vendor record missing"
            response.attempts += 1
            failed += 1
            session.add(response)
            continue

        # Safety gate: only consented, non-suppressed vendors are contacted.
        if not settings.allow_outreach:
            response.status = VendorResponseStatus.SKIPPED_COLD.value
            response.last_error = "Outreach disabled (ALLOW_OUTREACH=false)"
            skipped_cold += 1
            session.add(response)
            continue

        if not vendor.contact_consent or vendor.opted_out or _is_suppressed(session, vendor):
            response.status = VendorResponseStatus.SKIPPED_COLD.value
            response.last_error = "Vendor not consented or suppressed (cold lead)"
            skipped_cold += 1
            session.add(response)
            continue

        if not vendor.phone:
            response.status = VendorResponseStatus.FAILED.value
            response.last_error = "Vendor has no phone number for WhatsApp"
            failed += 1
            session.add(response)
            continue

        # Rate limit between sends.
        if sent_in_batch >= settings.max_outreach_per_batch:
            response.status = VendorResponseStatus.FAILED.value
            response.last_error = "Batch cap reached; retry to continue"
            failed += 1
            session.add(response)
            continue

        if settings.outbound_rate_delay_seconds > 0:
            sleep(settings.outbound_rate_delay_seconds)

        response.attempts += 1
        try:
            result = whatsapp.send_text(vendor.phone, response.message_text)
            response.sent_at = utcnow()
            if isinstance(result, dict) and result.get("dry_run"):
                # Dry-run: treated as sent for flow purposes, flagged in the error field.
                response.status = VendorResponseStatus.SENT.value
                response.last_error = "dry_run"
            else:
                response.status = VendorResponseStatus.SENT.value
            sent += 1
            sent_in_batch += 1
        except Exception as exc:
            logger.exception("Outreach send failed for vendor %s", vendor.id)
            response.status = VendorResponseStatus.FAILED.value
            response.last_error = str(exc)
            failed += 1
        session.add(response)
        session.commit()

    # If nothing was sent (e.g. all cold), stay back at the authorization
    # checkpoint so the buyer can grant consent and resend, rather than
    # dead-ending at collecting_responses with no outbound messages.
    if sent > 0:
        procurement_case.status = CaseStatus.COLLECTING_RESPONSES.value
    else:
        procurement_case.status = CaseStatus.OUTREACH_APPROVED.value
    procurement_case.updated_at = utcnow()
    session.add(procurement_case)
    session.commit()

    return OutreachSummary(
        case_id=case_id,
        status=procurement_case.status,
        total=len(responses),
        sent=sent,
        failed=failed,
        skipped_cold=skipped_cold,
    )
