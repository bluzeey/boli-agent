from app.schemas import SearchResult


def render_search_results(query: str, results: list[SearchResult], is_demo: bool = False) -> str:
    if not results:
        return (
            f"I searched for *{query}* but did not find enough suitable vendors. "
            "Try widening the location or changing the requirement."
        )

    prefix = "🔎 *Vendor shortlist*\n" if not is_demo else "🔎 *Vendor shortlist*\n"
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
    if summary.sent == 0:
        lines = ["*No vendors were contacted yet.*"]
        if summary.skipped_cold:
            lines.append(
                f"{summary.skipped_cold} vendor(s) skipped (cold/not consented)."
            )
        if summary.failed:
            lines.append(f"{summary.failed} failed to send.")
        lines.append(
            "Reply *consent <number>* to authorize a vendor, then *resend*."
        )
        return "\n".join(lines)

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


def render_vendor_ack() -> str:
    """Acknowledgement sent to a vendor when their reply is received."""
    return (
        "Thank you, your response has been recorded and shared with the buyer. "
        "They will reach out if they need anything else."
    )


def render_vendor_replied(vendor_name: str, case_id: str) -> str:
    """Notification sent to the buyer when a vendor replies."""
    return (
        f"📋 *{vendor_name}* replied to your RFQ (case #{case_id}). "
        "Reply *status* to see response progress."
    )


def render_case_status(stats: dict) -> str:
    """Summarise outreach/response progress for the buyer."""
    lines = ["*Case status*"]
    lines.append(f"RFQs sent: {stats.get('sent', 0)}")
    lines.append(f"Vendors responded: {stats.get('responded', 0)}")
    skipped = stats.get("skipped", 0)
    failed = stats.get("failed", 0)
    if skipped:
        lines.append(f"Skipped (cold): {skipped}")
    if failed:
        lines.append(f"Failed to send: {failed}")
    pending = stats.get("pending", 0)
    if pending:
        lines.append(f"Awaiting response: {pending}")
    lines.append("")
    lines.append(
        "Reply *consent <number>* to authorize a cold vendor, *resend* to reach out "
        "again, or *new search* to start over."
    )
    return "\n".join(lines)


def render_collecting_hint() -> str:
    return (
        "I didn't recognise that. Reply *status* to see progress, *consent <number>* "
        "to authorize a vendor, *compare* to compare bids, *followup <number>* to "
        "request a missing field, *resend* to re-send, or *new search* to start over."
    )


def _format_money(amount: float | None) -> str:
    return "n/a" if amount is None else f"Rs {amount:,.0f}"


def render_comparison(comparison) -> str:
    """Render a bid comparison table + recommendation for WhatsApp."""
    lines = [f"*Bid comparison — case #{comparison.case_id}*"]
    if not comparison.bids:
        lines.append("No vendor responses to compare yet.")
        return "\n".join(lines)

    for bid in comparison.bids:
        marker = "✅" if bid.complete else ("⏳" if bid.responded else "—")
        cost = _format_money(bid.effective_cost)
        tax = f" (+{bid.tax_pct:g}% tax)" if bid.tax_pct is not None else ""
        lead = f", lead {bid.lead_time}" if bid.lead_time else ""
        lines.append(f"{marker} {bid.position}. {bid.vendor_name} — {cost}{tax}{lead}")
        if bid.exclusions:
            lines.append(f"   ⚠️ Exclusions: {', '.join(bid.exclusions)}")
        if bid.missing:
            lines.append(f"   ⚠️ Missing: {', '.join(bid.missing)}")
        if not bid.responded:
            lines.append("   (no response yet)")

    lines.append("")
    if comparison.recommendation:
        rec = comparison.recommendation
        lines.append(
            f"*Recommended: {rec.vendor_name} ({_format_money(rec.effective_cost)})*"
        )
    else:
        lines.append(
            "*No recommendation yet* — need at least one complete bid "
            "(all required fields present)."
        )

    for warning in comparison.warnings:
        lines.append(f"⚠️ {warning}")

    lines.append("")
    lines.append(
        "Reply *select <number>* to choose a winner, *followup <number>* to request "
        "a missing field, or *new search* to start over."
    )
    return "\n".join(lines)
