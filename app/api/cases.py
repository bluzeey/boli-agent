from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ProcurementCase
from app.schemas import CaseRead

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.get("/{case_id}", response_model=CaseRead)
def get_case(case_id: str, session: Session = Depends(get_session)) -> ProcurementCase:
    procurement_case = session.scalars(
        select(ProcurementCase).where(ProcurementCase.id == case_id)
    ).first()
    if not procurement_case:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return procurement_case
