from typing import Protocol


class CategoryPack(Protocol):
    """Contract for a category-specific procurement pack.

    A pack supplies domain questions, qualification rules, quote fields, risk
    flags, and document templates for a procurement category. The generic pack
    is used until real category packs are introduced.
    """

    id: str
    procurement_type: str
    support_tier: str
    required_fields: list[str]
    comparison_fields: list[str]

    def render_rfq(self, case_snapshot: dict, recipients: list[dict]) -> str: ...

    def render_vendor_rfq(self, case_snapshot: dict, vendor_name: str) -> str: ...
