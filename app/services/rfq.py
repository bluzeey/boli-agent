from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.categories import get_category_pack
from app.models import ProcurementCase, Rfq, RfqStatus, VendorCandidate


def utcnow() -> datetime:
    return datetime.now(UTC)


def case_snapshot(procurement_case: ProcurementCase) -> dict[str, Any]:
    return {
        "case_id": procurement_case.id,
        "normalized_need": procurement_case.normalized_need,
        "request_type": procurement_case.request_type,
        "category": procurement_case.category or "generic",
        "location": procurement_case.location,
        "quantity": procurement_case.quantity,
        "budget": procurement_case.budget,
        "deadline": procurement_case.deadline,
        "must_haves": procurement_case.must_haves or [],
    }


def build_recipients(selected: list[VendorCandidate]) -> list[dict[str, Any]]:
    return [
        {"candidate_id": c.id, "name": c.name, "phone": c.phone} for c in selected
    ]


def latest_rfq(session: Session, case_id: str) -> Rfq | None:
    return session.scalars(
        select(Rfq).where(Rfq.case_id == case_id).order_by(Rfq.version.desc())
    ).first()


def generate_rfq(
    session: Session, procurement_case: ProcurementCase, selected: list[VendorCandidate]
) -> Rfq:
    """Build a versioned RFQ from the canonical case and selected vendor leads."""
    pack = get_category_pack(procurement_case.category)
    snapshot = case_snapshot(procurement_case)
    recipients = build_recipients(selected)
    document_text = pack.render_rfq(snapshot, recipients)

    latest = latest_rfq(session, procurement_case.id)
    version = (latest.version + 1) if latest else 1
    if latest:
        latest.status = RfqStatus.SUPERSEDED.value
        latest.updated_at = utcnow()
        session.add(latest)

    rfq = Rfq(
        case_id=procurement_case.id,
        version=version,
        document_text=document_text,
        fields_snapshot=snapshot,
        recipients=recipients,
        response_deadline=procurement_case.deadline,
        status=RfqStatus.SHOWN.value,
    )
    session.add(rfq)
    session.flush()
    return rfq
