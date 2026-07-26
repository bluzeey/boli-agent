from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProcurementCase, Vendor, VendorCandidate, VendorResponse
from app.services.completeness import missing_required_fields


@dataclass(frozen=True, slots=True)
class BidRow:
    position: int | None
    vendor_id: str
    vendor_name: str
    responded: bool
    price: float | None
    tax_pct: float | None
    effective_cost: float | None
    lead_time: str | None
    exclusions: list[str]
    missing: list[str]
    complete: bool


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    case_id: str
    bids: list[BidRow]
    recommendation: BidRow | None
    warnings: list[str] = field(default_factory=list)


def _parse_amount(value: Any) -> float | None:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit() or ch == ".")
    if not digits or digits == ".":
        return None
    try:
        return float(digits)
    except ValueError:
        return None


def _parse_tax_pct(value: Any) -> float | None:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit() or ch == ".")
    if not digits or digits == ".":
        return None
    try:
        return float(digits)
    except ValueError:
        return None


def _build_bid(
    position: int | None, vendor: Vendor | None, response: VendorResponse, category: str
) -> BidRow:
    fields = response.extracted_fields or {}
    price = _parse_amount(fields.get("price"))
    tax_pct = _parse_tax_pct(fields.get("tax"))
    if price is not None and tax_pct is not None:
        effective_cost = price * (1 + tax_pct / 100.0)
    elif price is not None:
        effective_cost = price
    else:
        effective_cost = None

    responded = response.status == "responded"
    missing = missing_required_fields(response, category) if responded else []
    # A bid is comparable only if it responded and has no missing required fields.
    complete = responded and not missing

    return BidRow(
        position=position,
        vendor_id=vendor.id if vendor else "",
        vendor_name=vendor.name if vendor else "Unknown vendor",
        responded=responded,
        price=price,
        tax_pct=tax_pct,
        effective_cost=effective_cost,
        lead_time=fields.get("lead_time"),
        exclusions=list(fields.get("exclusions") or []),
        missing=missing,
        complete=complete,
    )


def build_comparison(session: Session, case: ProcurementCase) -> ComparisonResult:
    responses = list(
        session.scalars(
            select(VendorResponse)
            .where(VendorResponse.case_id == case.id)
            .order_by(VendorResponse.created_at.asc())
        )
    )
    candidate_positions = {
        c.external_id: c.position
        for c in session.scalars(
            select(VendorCandidate).where(VendorCandidate.case_id == case.id)
        )
    }

    category = case.category or "generic"
    bids: list[BidRow] = []
    for response in responses:
        vendor = session.scalars(
            select(Vendor).where(Vendor.id == response.vendor_id)
        ).first()
        position = candidate_positions.get(vendor.external_id) if vendor else None
        bids.append(_build_bid(position, vendor, response, category))

    # Recommendation: lowest effective cost among complete bids.
    complete_bids = sorted(
        [b for b in bids if b.complete and b.effective_cost is not None],
        key=lambda b: b.effective_cost,
    )
    recommendation = complete_bids[0] if complete_bids else None

    warnings: list[str] = []
    priced = [b for b in bids if b.price is not None]
    if priced:
        cheapest = min(priced, key=lambda b: b.price)  # type: ignore[arg-type]
        if not cheapest.complete:
            missing_str = ", ".join(cheapest.missing) if cheapest.missing else "required fields"
            warnings.append(
                f"The cheapest bid ({cheapest.vendor_name}) is missing {missing_str}; "
                "it is not eligible for recommendation until completed."
            )
    not_responded = [b for b in bids if not b.responded]
    if not_responded:
        warnings.append(
            f"{len(not_responded)} vendor(s) have not responded yet."
        )

    return ComparisonResult(
        case_id=case.id,
        bids=bids,
        recommendation=recommendation,
        warnings=warnings,
    )
