from app.models import ProcurementCase, Rfq, Vendor
from app.services.comparison import BidRow


def generate_document(
    case: ProcurementCase, bid: BidRow, vendor: Vendor | None, rfq: Rfq | None
) -> str:
    """Render a deterministic draft purchase order / agreement as text.

    The document is a draft for authorised human approval. No binding commitment
    is made until reviewed and signed.
    """
    name = vendor.name if vendor else bid.vendor_name
    phone = vendor.phone if vendor else None

    price = f"Rs {bid.price:,.0f}" if bid.price is not None else "not stated"
    tax = f" (+{bid.tax_pct:g}% tax)" if bid.tax_pct is not None else ""
    effective = f"Rs {bid.effective_cost:,.0f}" if bid.effective_cost is not None else "n/a"
    lead = bid.lead_time or "not stated"
    exclusions = ", ".join(bid.exclusions) if bid.exclusions else "none"

    lines: list[str] = []
    lines.append(f"*Boli Purchase Order — #{case.id}* (draft)")
    lines.append("")
    lines.append(f"Buyer requirement: {case.normalized_need or 'Not specified'}")
    type_line = f"Type: {case.request_type or 'unknown'}"
    if case.location:
        type_line += f" | Location: {case.location}"
    lines.append(type_line)
    if case.quantity:
        lines.append(f"Quantity: {case.quantity}")
    if case.budget:
        lines.append(f"Budget: {case.budget}")
    if case.deadline:
        lines.append(f"Deadline: {case.deadline}")
    lines.append("")
    lines.append(f"Selected vendor: {name}")
    if phone:
        lines.append(f"Vendor contact: {phone}")
    lines.append(f"Quoted price: {price}{tax}")
    lines.append(f"Effective cost: {effective}")
    lines.append(f"Lead time: {lead}")
    lines.append(f"Exclusions: {exclusions}")
    if rfq and rfq.response_deadline:
        lines.append(f"Response deadline: {rfq.response_deadline}")
    lines.append("")
    lines.append(
        "This is a draft prepared by Boli for authorised human approval. "
        "No binding commitment is made until an authorised approver reviews and signs."
    )
    return "\n".join(lines)
