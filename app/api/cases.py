from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CaseStatus, ProcurementCase, RfqStatus, VendorCandidate, ensure_aware
from app.schemas import (
    CaseRead,
    RfqApproveResponse,
    RfqGenerateResponse,
    RfqRead,
    RfqRecipient,
    ShortlistRequest,
    ShortlistResponse,
    VendorCandidateRead,
)
from app.services.rfq import generate_rfq, latest_rfq

router = APIRouter(prefix="/api/cases", tags=["cases"])


def _get_case_or_404(session: Session, case_id: str) -> ProcurementCase:
    procurement_case = session.scalars(
        select(ProcurementCase).where(ProcurementCase.id == case_id)
    ).first()
    if not procurement_case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return procurement_case


def _is_expired(expires_at: datetime | None, now: datetime) -> bool:
    aware = ensure_aware(expires_at)
    return bool(aware and aware < now)


def _candidate_to_read(candidate: VendorCandidate, now: datetime) -> VendorCandidateRead:
    return VendorCandidateRead(
        id=candidate.id,
        position=candidate.position,
        external_id=candidate.external_id,
        provider=candidate.provider,
        name=candidate.name,
        address=candidate.address,
        phone=candidate.phone,
        website=candidate.website,
        rating=candidate.rating,
        review_count=candidate.review_count,
        source_url=candidate.source_url,
        selected=candidate.selected_at is not None,
        confirmed=candidate.confirmed_at is not None,
        expired=_is_expired(candidate.expires_at, now),
    )


def _rfq_to_read(rfq) -> RfqRead:
    return RfqRead(
        id=rfq.id,
        case_id=rfq.case_id,
        version=rfq.version,
        document_text=rfq.document_text,
        fields_snapshot=rfq.fields_snapshot,
        recipients=[RfqRecipient(**r) for r in rfq.recipients],
        response_deadline=rfq.response_deadline,
        status=rfq.status,
        created_at=rfq.created_at,
        updated_at=rfq.updated_at,
    )


@router.get("/{case_id}", response_model=CaseRead)
def get_case(case_id: str, session: Session = Depends(get_session)) -> ProcurementCase:
    return _get_case_or_404(session, case_id)


@router.get("/{case_id}/candidates", response_model=list[VendorCandidateRead])
def list_candidates(
    case_id: str, session: Session = Depends(get_session)
) -> list[VendorCandidateRead]:
    _get_case_or_404(session, case_id)
    now = datetime.now(UTC)
    candidates = session.scalars(
        select(VendorCandidate)
        .where(VendorCandidate.case_id == case_id)
        .order_by(VendorCandidate.position.asc())
    ).all()
    return [_candidate_to_read(c, now) for c in candidates]


@router.post("/{case_id}/shortlist", response_model=ShortlistResponse)
def set_shortlist(
    case_id: str,
    payload: ShortlistRequest,
    session: Session = Depends(get_session),
) -> ShortlistResponse:
    procurement_case = _get_case_or_404(session, case_id)
    candidates = session.scalars(
        select(VendorCandidate)
        .where(VendorCandidate.case_id == case_id)
        .order_by(VendorCandidate.position.asc())
    ).all()
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No vendor candidates are available for this case.",
        )

    position_map = {c.position: c for c in candidates}
    selection = sorted(set(payload.selection))
    invalid = [n for n in selection if n not in position_map]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid selection: {invalid}. Valid positions are "
                f"{sorted(position_map)}."
            ),
        )

    now = datetime.now(UTC)
    for candidate in candidates:
        candidate.selected_at = None
    selected = [position_map[p] for p in selection]
    for candidate in selected:
        candidate.selected_at = now

    procurement_case.status = CaseStatus.AWAITING_SHORTLIST_CONFIRMATION.value
    procurement_case.updated_at = now
    session.add_all(candidates)
    session.add(procurement_case)
    session.commit()

    return ShortlistResponse(
        case_id=case_id,
        status=procurement_case.status,
        selected=[_candidate_to_read(c, now) for c in selected],
    )


@router.post("/{case_id}/rfq", response_model=RfqGenerateResponse)
def generate_case_rfq(
    case_id: str, session: Session = Depends(get_session)
) -> RfqGenerateResponse:
    procurement_case = _get_case_or_404(session, case_id)
    selected = session.scalars(
        select(VendorCandidate)
        .where(
            VendorCandidate.case_id == case_id,
            VendorCandidate.selected_at.is_not(None),
        )
        .order_by(VendorCandidate.position.asc())
    ).all()
    if not selected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Select at least one vendor before generating an RFQ.",
        )

    now = datetime.now(UTC)
    for candidate in selected:
        candidate.confirmed_at = now
    rfq = generate_rfq(session, procurement_case, selected)
    procurement_case.status = CaseStatus.RFQ_READY.value
    procurement_case.updated_at = now
    session.add_all(selected)
    session.add(procurement_case)
    session.commit()

    return RfqGenerateResponse(
        case_id=case_id,
        status=procurement_case.status,
        rfq=_rfq_to_read(rfq),
    )


@router.get("/{case_id}/rfq", response_model=RfqRead)
def get_case_rfq(
    case_id: str, session: Session = Depends(get_session)
) -> RfqRead:
    _get_case_or_404(session, case_id)
    rfq = latest_rfq(session, case_id)
    if not rfq:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No RFQ has been generated for this case yet.",
        )
    return _rfq_to_read(rfq)


@router.post("/{case_id}/rfq/approve", response_model=RfqApproveResponse)
def approve_case_rfq(
    case_id: str, session: Session = Depends(get_session)
) -> RfqApproveResponse:
    procurement_case = _get_case_or_404(session, case_id)
    rfq = latest_rfq(session, case_id)
    if not rfq:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Generate an RFQ before approving outreach.",
        )

    now = datetime.now(UTC)
    rfq.status = RfqStatus.APPROVED.value
    rfq.updated_at = now
    procurement_case.status = CaseStatus.OUTREACH_APPROVED.value
    procurement_case.updated_at = now
    session.add(rfq)
    session.add(procurement_case)
    session.commit()

    return RfqApproveResponse(
        case_id=case_id,
        status=procurement_case.status,
        rfq_id=rfq.id,
        rfq_status=rfq.status,
        outreach_authorized=True,
    )
