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
        "Reply with a number to shortlist a vendor, or send another requirement to start a new search."
    )
    return "\n".join(lines)
