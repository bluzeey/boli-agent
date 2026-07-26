from app.schemas import SearchResult


def render_search_results(query: str, results: list[SearchResult], is_demo: bool = False) -> str:
    if not results:
        return (
            f"I searched for *{query}* but did not find enough suitable vendors. "
            "Try widening the location or changing the requirement."
        )

    prefix = "🧪 *Demo search results*\n" if is_demo else "🔎 *Vendor shortlist*\n"
    lines = [prefix, f"Requirement: {query}\n"]
    for index, result in enumerate(results, start=1):
        rating = ""
        if result.rating is not None:
            reviews = f" · {result.review_count} reviews" if result.review_count is not None else ""
            rating = f"\n⭐ {result.rating}{reviews}"
        phone = f"\n📞 {result.phone}" if result.phone else ""
        address = f"\n📍 {result.address}" if result.address else ""
        source = f"\n🔗 {result.source_url}" if result.source_url else ""
        lines.append(f"*{index}. {result.name}*{rating}{phone}{address}{source}\n")

    lines.append(
        "Reply with one or more numbers to shortlist vendors, e.g. *1, 3, 4*. "
        "Send *new search* to start over, or describe a new requirement."
    )
    return "\n".join(lines)


def render_selection_confirmation(selected) -> str:
    """Echo the buyer's selected vendors and ask for confirmation."""
    lines = ["*You selected these vendors:*"]
    for candidate in selected:
        phone = f" — {candidate.phone}" if getattr(candidate, "phone", None) else ""
        lines.append(f"{candidate.position}. {candidate.name}{phone}")
    lines.append("")
    lines.append("Reply *yes* to confirm and generate an RFQ, or *no* to change the selection.")
    return "\n".join(lines)


def render_outreach_approved(rfq) -> str:
    """Authorization checkpoint message: outreach authorized, about to send."""
    return (
        f"✅ *Outreach approved* for RFQ #{rfq.id} (v{rfq.version}).\n\n"
        "I will now send the RFQ to your selected vendors."
    )


def render_outreach_summary(summary) -> str:
    """Report outreach results to the buyer."""
    lines = [
        "📨 *Outreach complete* — case now collecting responses.",
        f"Vendors contacted: {summary.sent}",
    ]
    if summary.skipped_cold:
        lines.append(
            f"Skipped (cold/not consented): {summary.skipped_cold} — "
            "reply *consent <number>* to authorize a vendor, then *resend*."
        )
    if summary.failed:
        lines.append(f"Failed to send: {summary.failed}")
    return "\n".join(lines)


def render_selection_cleared() -> str:
    return (
        "Okay, I've cleared your selection. Reply with numbers to shortlist vendors, "
        "or send a new requirement."
    )


def render_stale_shortlist() -> str:
    return (
        "The previous search results have expired. Please send the requirement again "
        "so I can run a fresh vendor search."
    )
