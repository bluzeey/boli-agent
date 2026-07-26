from app.categories import get_category_pack
from app.models import VendorResponse


def missing_required_fields(response: VendorResponse, category: str) -> list[str]:
    """Return required commercial fields the vendor's quote is missing."""
    pack = get_category_pack(category)
    fields = response.extracted_fields or {}
    return [f for f in pack.required_fields if not fields.get(f)]


def render_followup_question(vendor_name: str, missing: list[str]) -> str:
    if not missing:
        return f"Hello {vendor_name}, thank you — your quotation is complete."
    return (
        f"Hello {vendor_name}, to complete your quotation please also share: "
        f"{', '.join(missing)}."
    )
