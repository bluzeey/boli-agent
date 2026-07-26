

def _value(snapshot: dict, key: str, default: str = "") -> str:
    value = snapshot.get(key)
    return str(value) if value else default


class GenericCategoryPack:
    """A generic, category-agnostic pack used as the default.

    It produces a deterministic, versioned RFQ from the canonical case fields
    without any category-specific qualification logic. Real category packs will
    override required/comparison fields and the RFQ template.
    """

    id = "generic"
    procurement_type = "unknown"
    support_tier = "partially_supported"
    required_fields = ["price", "tax", "lead_time", "payment_terms"]
    comparison_fields = ["unit_price", "total_cost", "lead_time", "exclusions"]

    def render_rfq(self, case_snapshot: dict, recipients: list[dict]) -> str:
        case_id = _value(snapshot=case_snapshot, key="case_id")
        normalized_need = _value(snapshot=case_snapshot, key="normalized_need")
        request_type = _value(snapshot=case_snapshot, key="request_type")
        category = _value(snapshot=case_snapshot, key="category", default="generic")
        location = _value(snapshot=case_snapshot, key="location")
        quantity = _value(snapshot=case_snapshot, key="quantity")
        budget = _value(snapshot=case_snapshot, key="budget")
        deadline = _value(snapshot=case_snapshot, key="deadline")
        must_haves = case_snapshot.get("must_haves") or []

        lines: list[str] = []
        lines.append(f"*Boli RFQ — #{case_id}*")
        lines.append("")
        lines.append(f"Buyer requirement: {normalized_need or 'Not specified'}")
        lines.append(f"Type: {request_type or 'unknown'}  |  Category: {category}")

        if location:
            lines.append(f"Location: {location}")
        if quantity:
            lines.append(f"Quantity: {quantity}")
        if budget:
            lines.append(f"Budget: {budget}")
        if deadline:
            lines.append(f"Deadline: {deadline}")
        if must_haves:
            lines.append("Must-haves:")
            for item in must_haves:
                lines.append(f"  - {item}")

        lines.append("")
        lines.append("Selected vendors:")
        if recipients:
            for index, recipient in enumerate(recipients, start=1):
                name = recipient.get("name") or "Unnamed vendor"
                phone = recipient.get("phone")
                contact = phone if phone else "contact pending"
                lines.append(f"{index}. {name} — {contact}")
        else:
            lines.append("None selected yet.")

        lines.append("")
        required = ", ".join(self.required_fields)
        lines.append(f"Please quote with: {required}.")

        if deadline:
            lines.append(f"Response deadline: {deadline}")

        lines.append("")
        lines.append(
            "No vendors have been contacted yet. Reply *approve* to authorize outreach."
        )
        return "\n".join(lines)

    def render_vendor_rfq(self, case_snapshot: dict, vendor_name: str) -> str:
        normalized_need = _value(snapshot=case_snapshot, key="normalized_need")
        location = _value(snapshot=case_snapshot, key="location")
        quantity = _value(snapshot=case_snapshot, key="quantity")
        budget = _value(snapshot=case_snapshot, key="budget")
        deadline = _value(snapshot=case_snapshot, key="deadline")

        lines: list[str] = []
        lines.append(f"Hello {vendor_name},")
        lines.append("")
        lines.append("A buyer would like to request a quotation:")
        lines.append("")
        lines.append(f"Requirement: {normalized_need or 'Not specified'}")
        if location:
            lines.append(f"Location: {location}")
        if quantity:
            lines.append(f"Quantity: {quantity}")
        if budget:
            lines.append(f"Indicative budget: {budget}")
        lines.append("")
        required = ", ".join(self.required_fields)
        lines.append(f"Please respond with: {required}.")
        if deadline:
            lines.append(f"Response deadline: {deadline}")
        lines.append("")
        lines.append("Reply to this number with your quotation. Thank you.")
        return "\n".join(lines)
